"""manage.py demo: nur mit SIGNNEXT_DEMO=1, erfundene Daten, normaler Ablauf bis zum Download. Keine Mail."""
import io
import os
import re
import unittest
from contextlib import redirect_stdout
from unittest import mock

import db
import demo
import demo_daten
import manage
import pdf_eingang
from tests.test_baustein4 import BASIS, CODE, ORIGIN, SEITEN, ZugangBasis, png_bytes, uri


class DemoBasis(ZugangBasis):
    ABSCHLUSS_AUS = False

    def demo_an(self):
        p = mock.patch.dict(os.environ, {"SIGNNEXT_DEMO": "1"})
        p.start()
        self.addCleanup(p.stop)

    def manage(self, *args):
        puffer = io.StringIO()
        with redirect_stdout(puffer), mock.patch("db.init"):
            code = manage.main(["manage.py", *args])
        return code, puffer.getvalue()

    def anzahl(self, tabelle="vorgang"):
        with db.verbindung() as c:
            return c.execute("SELECT COUNT(*) FROM %s" % tabelle).fetchone()[0]


class TestDemoSchutz(DemoBasis):
    def test_ohne_schalter_wird_nichts_angelegt(self):
        vorher = self.anzahl()
        for wert in (None, "", "0", "true", "ja", "yes", "11", " 1"):
            env = {k: v for k, v in os.environ.items() if k != "SIGNNEXT_DEMO"}
            if wert is not None:
                env["SIGNNEXT_DEMO"] = wert
            with mock.patch.dict(os.environ, env, clear=True):
                code, ausgabe = self.manage("demo")
            self.assertEqual((code, "demo_nicht_erlaubt" in ausgabe), (1, True), wert)
        self.assertEqual(self.anzahl(), vorher)
        with self.assertRaises(demo.DemoFehler):
            demo.vorgang_erzeugen("demo")
        self.assertEqual(self.anzahl("link"), 0)

    def test_ohne_hauptschluessel(self):
        self.demo_an()
        env = {k: v for k, v in os.environ.items() if k != "SIGNNEXT_HAUPTSCHLUESSEL"}
        with mock.patch.dict(os.environ, env, clear=True):
            code, ausgabe = self.manage("demo")
        self.assertEqual((code, "hauptschluessel_fehlt" in ausgabe), (1, True))

    def test_ungueltiger_mandant(self):
        self.demo_an()
        for ref in ("../x", "a b", "x" * 65, "", "a/b", "demo\n"):
            with self.assertRaises(demo.DemoFehler, msg=repr(ref)):
                demo.vorgang_erzeugen(ref)
        self.assertEqual(self.anzahl(), 0)

    def test_aufruf_falsch(self):
        self.demo_an()
        self.assertEqual(self.manage("demo", "a", "b")[0], 2)
        self.assertEqual(self.manage("demo-download")[0], 2)
        self.assertEqual(self.manage("demo-download", "x")[0], 2)

    def test_bestehende_angaben_der_beratungsstelle_bleiben(self):
        self.demo_an()
        with db.verbindung() as c:
            c.execute("INSERT INTO stelle_einstellung(mandant_ref, verantwortlicher, datenschutz_link, geaendert_am, geaendert_von) VALUES ('demo','Meine Stelle, Weg 9','https://meine.example/ds',?,?)", (db.jetzt(), "x"))
        demo.vorgang_erzeugen("demo")
        self.assertEqual(db.stelle_einstellung_lesen("demo")["verantwortlicher"], "Meine Stelle, Weg 9")


class TestDemoAblauf(DemoBasis):
    def test_demo_pdfs_sind_erfunden_und_erkennbar(self):
        b, a = demo_daten.b_pdf(), demo_daten.a_pdf()
        self.assertTrue(demo_daten.MAIL.endswith("@example.org"))
        eb, ea = pdf_eingang.auswerten(b), pdf_eingang.auswerten(a)
        self.assertEqual((eb["typ"], ea["typ"], eb["personen"]), ("B", "A", 2))

    def test_kommando_gibt_link_und_geburtsdatum_aus_ohne_mail(self):
        self.demo_an()
        n_mails = len(self.srv.nachrichten)
        code, ausgabe = self.manage("demo")
        self.assertEqual(code, 0, ausgabe)
        self.assertRegex(ausgabe, r"Mitglieds-Link:  https://signnext\.example/u#[A-Za-z0-9_-]{43}")
        self.assertIn("01.02.1960", ausgabe)
        self.assertIn("eingeben als 01021960", ausgabe)
        self.assertEqual(len(self.srv.nachrichten), n_mails)
        self.assertEqual(self.anzahl("vorgang"), 1)

    def test_von_demo_bis_download(self):
        self.demo_an()
        e = demo.vorgang_erzeugen("demo")
        token = re.search(r"/u#(\S+)$", e["link"]).group(1)
        vid = e["vorgang_id"]
        self.assertEqual(db.vorgang_intern("demo", vid)["status"], "versendet")
        self.assertEqual(self.anzahl("erinnerung"), 6)
        self.vid, self.token = vid, token
        self.assertEqual(self.pruefen().status_code, 200)
        self.lesen()
        self.assertEqual(self.sign(1, self.texte_holen()["hash"]).status_code, 200)
        self.assertEqual(self.sign(2, self.texte_holen()["hash"], bild=uri(png_bytes(300, 100))).get_json(), {"fertig": True})
        self.assertEqual(db.vorgang_intern("demo", vid)["status"], "abgeschlossen")
        d = demo.download_link_erzeugen(vid, "demo")
        dtoken = re.search(r"/d#(\S+)$", d["link"]).group(1)
        r = self.m.post("/api/v1/download", json={"token": dtoken, "code": d["code"]}, headers=dict(ORIGIN), base_url=BASIS)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_data().startswith(b"%PDF"))

    def test_download_link_nur_fuer_abgeschlossene(self):
        self.demo_an()
        e = demo.vorgang_erzeugen("demo")
        with self.assertRaises(demo.DemoFehler):
            demo.download_link_erzeugen(e["vorgang_id"], "demo")
        with self.assertRaises(demo.DemoFehler):
            demo.download_link_erzeugen(999, "demo")
        self.assertEqual(self.manage("demo-download", str(e["vorgang_id"]))[0], 1)
        self.assertEqual(self.anzahl("download_link"), 0)

    def test_demo_vorgang_gehoert_nur_zum_demo_mandanten(self):
        self.demo_an()
        e = demo.vorgang_erzeugen("demo")
        self.assertIsNone(db.vorgang_lesen("1001", e["vorgang_id"]))
        self.assertIsNotNone(db.vorgang_lesen("demo", e["vorgang_id"]))

    def test_ohne_basis_url_nur_der_pfad(self):
        self.demo_an()
        env = {k: v for k, v in os.environ.items() if k != "SIGNNEXT_BASIS_URL"}
        with mock.patch.dict(os.environ, env, clear=True):
            os.environ["SIGNNEXT_DEMO"] = "1"
            code, ausgabe = self.manage("demo")
        self.assertEqual(code, 0, ausgabe)
        self.assertIn("Hinweis: SIGNNEXT_BASIS_URL ist nicht gesetzt", ausgabe)


if __name__ == "__main__":
    unittest.main()
