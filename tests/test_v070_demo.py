"""v0.7.0 Demo-Modus mit echtem Mailversand: Demo-Endpunkte, Pflichtabgleich Mail-Zugang, Versand mit Link/QR, Fallback ohne Mail, Testmail, Allowlist. Nur erfundene Daten."""
import os
import re
import unittest
from unittest import mock

import config
import db
import qr
from tests import pdfs
from tests.test_baustein2 import hochladen
from tests.test_baustein3 import MailBasis, PW

MITGLIED = pdfs.MAIL.lower()
EXTERN = "max.muster1972@example.org"


class DemoMail(MailBasis):
    DEMO = True
    ALLOWLIST = MITGLIED + "," + EXTERN

    def setUp(self):
        super().setUp()
        env = {"SIGNNEXT_VERSAND": "testadressen", "SIGNNEXT_VERSAND_TESTADRESSEN": self.ALLOWLIST}
        if self.DEMO:
            env["SIGNNEXT_DEMO"] = "1"
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        if not self.DEMO:
            os.environ.pop("SIGNNEXT_DEMO", None)
        self.srv = self.starte("implicit")
        self.assertEqual(self.zugang(self.srv).status_code, 200)
        self.markiere_geprueft()


class TestDemoEndpunkte(DemoMail):
    def test_zustand(self):
        j = self.c.get("/api/v1/demo").get_json()
        self.assertEqual((j["demo"], j["versand"], j["mail_zugang_geprueft"]), (True, "testadressen", True))
        self.assertEqual(j["testadressen"], sorted({MITGLIED, EXTERN}))
        self.assertIn("protokoll-ehepaar", j["beispiele"])

    def test_beispiel_pdfs_laden_und_passen_zusammen_hoch(self):
        pdf = {n: self.c.get("/api/v1/demo/beispiel/" + n) for n in ("anschreiben", "anschreiben-einzel", "protokoll-ehepaar", "protokoll-einzel")}
        for n, r in pdf.items():
            self.assertEqual((r.status_code, r.mimetype, r.get_data()[:5]), (200, "application/pdf", b"%PDF-"), n)
            self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertEqual(self.c.get("/api/v1/demo/beispiel/gibt-es-nicht").status_code, 404)
        def stellen(b, a):
            r = hochladen(self.c, pdf[b].get_data(), pdf[a].get_data())
            self.assertEqual((r.status_code, r.get_json()["status"]), (201, "bereit"), r.get_data(as_text=True))
            d = self.c.get("/api/v1/vorgaenge/%d" % r.get_json()["vorgang_id"]).get_json()
            return r.get_json()["personen"], sum(len(x["stellen"]) if isinstance(x["stellen"], list) else len(__import__("json").loads(x["stellen"])) for x in d["dokumente"])
        ehepaar, einzel = stellen("anschreiben", "protokoll-ehepaar"), stellen("anschreiben-einzel", "protokoll-einzel")
        self.assertEqual((ehepaar[0], einzel[0]), (2, 1))
        self.assertGreater(ehepaar[1], einzel[1])                                                           # Ehepaar hat mehr Unterschriftsstellen als die Einzelperson

    def test_demo_vorgang_mit_link_qr_und_code(self):
        r = self.c.post("/api/v1/demo/vorgang")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertTrue(j["link"].startswith("https://signnext.example/u#"))
        self.assertTrue(j["qr_svg"].startswith("<svg"))
        self.assertNotIn("<script", j["qr_svg"])
        self.assertEqual(len(j["code"]), 6)

    def test_testmail_nur_an_erlaubte_adresse_und_wirklich_gesendet(self):
        r = self.c.post("/api/v1/demo/testmail", json={"an": EXTERN.upper()})
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True, "ergebnis": "gesendet"}))
        self.assertEqual(len(self.srv.nachrichten), 1)
        self.assertEqual(self.srv.nachrichten[0]["an"], [EXTERN])
        r = self.c.post("/api/v1/demo/testmail", json={"an": "fremd@example.org"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "adresse_nicht_erlaubt"))
        self.assertEqual(len(self.srv.nachrichten), 1)                                                       # nichts an Fremde
        for schlecht in ({}, {"an": 5}, {"an": EXTERN, "x": 1}):
            self.assertEqual(self.c.post("/api/v1/demo/testmail", json=schlecht).status_code, 400)

    def test_testmail_mit_serverfehler_meldet_den_code(self):
        self.srv.mail_ablehnen = True
        r = self.c.post("/api/v1/demo/testmail", json={"an": EXTERN})
        self.assertEqual(r.status_code, 502)
        self.assertTrue(r.get_json()["error"].startswith("mail_"))

    def test_testmail_ohne_mail_zugang(self):
        with db.verbindung() as c:
            c.execute("DELETE FROM mail_zugang")
        r = self.c.post("/api/v1/demo/testmail", json={"an": EXTERN})
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "mail_zugang_fehlt"))

    def test_ohne_demo_schalter_gibt_es_die_endpunkte_nicht(self):
        with mock.patch.dict(os.environ, {"SIGNNEXT_DEMO": "0"}):
            for methode, pfad in (("get", "/api/v1/demo"), ("post", "/api/v1/demo/vorgang"), ("get", "/api/v1/demo/beispiel/anschreiben"), ("post", "/api/v1/demo/testmail")):
                self.assertEqual(getattr(self.c, methode)(pfad).status_code, 404, pfad)

    def test_ohne_anmeldung_401(self):
        self.c.delete_cookie("bst_session", domain="localhost")
        for methode, pfad in (("get", "/api/v1/demo"), ("post", "/api/v1/demo/vorgang"), ("post", "/api/v1/demo/testmail")):
            self.assertEqual(getattr(self.c, methode)(pfad).status_code, 401, pfad)


class TestVersandImDemo(DemoMail):
    def test_echte_mail_geht_raus_und_antwort_hat_link_qr_hinweis(self):
        vid = self.bereit()
        r = self.senden_ok(vid)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual(len(self.srv.nachrichten), 1)
        self.assertEqual(self.srv.nachrichten[0]["an"], [MITGLIED])
        _, text = self.mail_text(self.srv.nachrichten[0])
        self.assertIn(j["demo"]["link"], text)                                                                # Mail und Bildschirm zeigen denselben Link
        self.assertTrue(j["demo"]["qr_svg"].startswith("<svg"))
        self.assertFalse(j["demo"]["ohne_mail"])
        self.assertIn("Geburtsdatum", j["demo"]["geburtsdatum_hinweis"])
        self.assertNotIn("demo_link", j)

    def test_nicht_freigegebene_adresse_wird_nicht_gemailt_fallback_ohne_mail(self):
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND_TESTADRESSEN": EXTERN}):
            vid = self.bereit()
            r = self.senden_ok(vid)
            self.assertEqual(r.status_code, 409)
            self.assertEqual((r.get_json()["error"], r.get_json()["fallback"]), ("empfaenger_nicht_freigegeben", "ohne_mail"))
            self.assertEqual(len(self.srv.nachrichten), 0)
            r = self.c.post("/api/v1/vorgaenge/%d/versenden" % vid, json={"bestaetigt": True, "ohne_mail": True})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertTrue(r.get_json()["demo"]["ohne_mail"])
            self.assertTrue(r.get_json()["demo"]["link"].startswith("https://signnext.example/u#"))
            self.assertEqual(len(self.srv.nachrichten), 0)                                                  # wirklich keine Mail
            with db.verbindung() as c:
                self.assertEqual(c.execute("SELECT status FROM vorgang WHERE id=?", (vid,)).fetchone()[0], "versendet")

    def test_mailfehler_im_demo_bietet_fallback_an_und_der_link_geht_danach(self):
        self.srv.mail_ablehnen = True
        vid = self.bereit()
        r = self.senden_ok(vid)
        self.assertGreaterEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["fallback"], "ohne_mail")
        self.srv.mail_ablehnen = False
        r = self.c.post("/api/v1/vorgaenge/%d/versenden" % vid, json={"bestaetigt": True, "ohne_mail": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        link = r.get_json()["demo"]["link"]
        token = link.split("#", 1)[1]
        self.assertEqual(self.c.post("/api/v1/zugang/pruefen", json={"token": token, "code": "010260"}, headers={"Origin": "https://signnext.example"}).status_code, 200)

    def test_ohne_mail_nur_im_demo_und_nur_als_bool(self):
        vid = self.bereit()
        for body in ({"bestaetigt": True, "ohne_mail": "ja"}, {"bestaetigt": True, "ohne_mail": 1}, {"bestaetigt": True, "x": 1}):
            self.assertEqual(self.c.post("/api/v1/vorgaenge/%d/versenden" % vid, json=body).status_code, 400, body)
        with mock.patch.dict(os.environ, {"SIGNNEXT_DEMO": "0"}):
            r = self.c.post("/api/v1/vorgaenge/%d/versenden" % vid, json={"bestaetigt": True, "ohne_mail": True})
            self.assertEqual(r.status_code, 400)

    def test_andere_fehler_haben_keinen_fallback(self):
        vid = self.bereit()
        r = self.c.post("/api/v1/vorgaenge/%d/versenden" % vid, json={})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("fallback", r.get_json())


class TestAusserhalbDemo(DemoMail):
    DEMO = False

    def test_im_normalbetrieb_nie_ein_link_in_der_antwort_und_ohne_mail_gesperrt(self):
        vid = self.bereit()
        r = self.senden_ok(vid)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertNotIn("demo", r.get_json())
        self.assertNotIn("demo_link", r.get_json())
        self.assertNotIn("link", r.get_json())

    def test_pflichtabgleich_gilt_nur_im_demo(self):
        r = self.zugang(self.srv, benutzer="jemand@anderer-anbieter.example", absender_adresse="bst@example.org")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))


class TestAbsenderAbgleich(DemoMail):
    def test_domain_von_benutzer_und_absender_muss_passen(self):
        r = self.zugang(self.srv, benutzer="thomas@hilo.example", absender_adresse="thomas@fremd.example")
        self.assertEqual(r.status_code, 422, r.get_data(as_text=True))
        self.assertIn("DMARC", r.get_json()["meldung"])
        r = self.zugang(self.srv, benutzer="thomas@hilo.example", absender_adresse="Thomas.Ulbrich@HILO.example")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_benutzer_ohne_at_zeichen_wird_nicht_abgeglichen(self):
        self.assertEqual(self.zugang(self.srv, benutzer="tulbrich", absender_adresse="thomas@hilo.example").status_code, 200)

    def test_hinweis_wenn_benutzer_und_absender_verschieden_sind(self):
        self.assertEqual(self.zugang(self.srv, benutzer="a@hilo.example", absender_adresse="b@hilo.example").status_code, 200)
        self.assertEqual(len(self.c.get("/api/v1/mail-zugang").get_json()["hinweise"]), 1)
        self.assertEqual(self.zugang(self.srv, benutzer="a@hilo.example", absender_adresse="a@hilo.example").status_code, 200)
        self.assertEqual(self.c.get("/api/v1/mail-zugang").get_json()["hinweise"], [])


class TestAllowlist(unittest.TestCase):
    def test_datei_im_datenverzeichnis_wird_ohne_neustart_gelesen(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {"SIGNNEXT_DATEN": t, "SIGNNEXT_VERSAND_TESTADRESSEN": "a@example.org"}):
            self.assertEqual(config.versand_testadressen(), {"a@example.org"})
            with open(os.path.join(t, "testadressen.txt"), "w", encoding="utf-8") as f:
                f.write("# Demo\nB@Example.org  # catrin\n\nkeine adresse\nc d@example.org\n")
            self.assertEqual(config.versand_testadressen(), {"a@example.org", "b@example.org"})
            os.remove(os.path.join(t, "testadressen.txt"))
            self.assertEqual(config.versand_testadressen(), {"a@example.org"})


class TestQr(unittest.TestCase):
    def test_svg_ohne_aktive_inhalte(self):
        s = qr.svg("https://pi.test:8443/u#abc")
        self.assertTrue(s.startswith("<svg"))
        for verboten in ("<script", "onload", "xlink", "javascript:"):
            self.assertNotIn(verboten, s)

    def test_ungueltige_eingaben(self):
        for schlecht in ("", None, 5, "x" * 501):
            with self.assertRaises(ValueError):
                qr.svg(schlecht)


if __name__ == "__main__":
    unittest.main()
