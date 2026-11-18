"""Baustein 2: PDF-Eingang (Erkennung, Abgleich, Zusammenführen, Stellen, Ablehnung, Speicherung, Mandantentrennung, Löschung).
Nur erfundene PDFs (tests/pdfs.py). Optional: Messlauf gegen die echten Testdateien aus dem Sync (übersprungen, wenn nicht vorhanden)."""
import io
import json
import logging
import os
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from pypdf import PdfReader

import abgleich
import db
import pdf_analyse
import pdf_eingang
import pdf_pruefung
from pdf_pruefung import Abgelehnt
from tests import pdfs
from tests.test_baustein1 import Antwort, Basis, me


def analyse(daten):
    return pdf_analyse.analysiere(daten)


ARTEN = ("js", "embedded", "openaction", "aa", "launch", "annot_js", "annot_jskey", "xfa", "fileattach")


class TestPruefung(unittest.TestCase):
    def abgelehnt(self, daten, code):
        with self.assertRaises(Abgelehnt) as k:
            pdf_pruefung.pruefe(daten)
        self.assertEqual(k.exception.code, code)

    def test_gueltige_pdfs_werden_angenommen(self):
        for d in (pdfs.b_pdf(), pdfs.a_pdf()):
            pdf_pruefung.pruefe(d)

    def test_kein_pdf_defekt_leer(self):
        self.abgelehnt(b"", "kein_pdf")
        self.abgelehnt(b"Hallo Welt", "kein_pdf")
        self.abgelehnt(b"MZ" + b"\0" * 100, "kein_pdf")
        self.abgelehnt(b"%PDF-1.4\nkaputt kaputt", "defekt")
        self.abgelehnt(pdfs.b_pdf()[:3000], "defekt")

    def test_verschluesselt(self):
        self.abgelehnt(pdfs.mit_encrypt(pdfs.b_pdf()), "verschluesselt")

    def test_seitengrenze(self):
        pdf_pruefung.pruefe(pdfs.b_pdf(seiten=pdf_pruefung.MAX_SEITEN, bloecke=(1, pdf_pruefung.MAX_SEITEN)))
        self.abgelehnt(pdfs.b_pdf(seiten=pdf_pruefung.MAX_SEITEN + 1, bloecke=(1,)), "zu_viele_seiten")

    def test_groessengrenze(self):
        self.abgelehnt(b"%PDF-" + b"0" * pdf_pruefung.MAX_BYTES, "zu_gross")

    def test_aktive_inhalte(self):
        for art in ARTEN:
            self.abgelehnt(pdfs.mit_javascript(pdfs.b_pdf(), art), "aktive_inhalte")

    def test_strukturpruefung_allein(self):
        """Die Strukturprüfung trägt jede Art auch ohne die Klartext-Marken (die z. B. in komprimierten Objekten nichts finden würden)."""
        with mock.patch.object(pdf_pruefung, "ROH_MARKEN", ()):
            for art in ARTEN:
                if art == "embedded":
                    continue                                    # eingebettete Datei: im Namensbaum, siehe unten
                self.abgelehnt(pdfs.mit_javascript(pdfs.b_pdf(), art), "aktive_inhalte")
            self.abgelehnt(pdfs.mit_javascript(pdfs.b_pdf(), "embedded"), "aktive_inhalte")

    def test_rohe_marken(self):
        erwartet = (b"/JavaScript", b"/EmbeddedFile", b"/OpenAction", b"/Launch", b"/RichMedia")
        self.assertTrue(all(m in pdf_pruefung.ROH_MARKEN for m in erwartet))
        for marke in erwartet:
            self.abgelehnt(pdfs.b_pdf() + b"\n% " + marke + b"\n", "aktive_inhalte")

    def test_links_sind_keine_aktiven_inhalte(self):
        pdf_pruefung.pruefe(pdfs.mit_javascript(pdfs.b_pdf(), "link"))


class TestErkennung(unittest.TestCase):
    def test_typ_b_mit_zwei_personen(self):
        e = analyse(pdfs.b_pdf())
        self.assertEqual((e["typ"], e["seiten"], e["personen"]), ("B", 4, 2))
        self.assertEqual(sorted((s["seite"], s["person"]) for s in e["stellen"]), [(1, 1), (1, 2), (4, 1), (4, 2)])
        self.assertEqual(e["hinweise"], [])

    def test_typ_b_einzelperson(self):
        e = analyse(pdfs.b_pdf(personen=1))
        self.assertEqual((e["personen"], len(e["stellen"])), (1, 2))

    def test_typ_a(self):
        e = analyse(pdfs.a_pdf())
        self.assertEqual((e["typ"], e["seiten"], e["personen"]), ("A", 12, None))
        self.assertEqual(sorted((s["seite"], s["person"]) for s in e["stellen"]), [(11, 1), (11, 2)])
        self.assertIn("a_nennt_ehegatten", e["hinweise"])
        self.assertNotIn("a_nennt_ehegatten", analyse(pdfs.a_pdf(ehegatte=False))["hinweise"])

    def test_stellen_liegen_ueber_der_linie_und_auf_der_seite(self):
        for d in (pdfs.b_pdf(), pdfs.a_pdf()):
            for s in analyse(d)["stellen"]:
                self.assertTrue(0 <= s["x0"] < s["x1"] <= 595, s)
                self.assertTrue(0 <= s["y0"] < s["y1"] <= 842, s)
                self.assertGreaterEqual(s["y1"] - s["y0"], 20)
                self.assertLessEqual(s["y1"] - s["y0"], pdf_analyse.HOEHE_STELLE + 0.5)
        # B Seite 1: Linie bei top 758 -> y0 ~ 842-758+Abstand; die Datumszeile (top 700..710) darf nicht überdeckt werden
        s = [s for s in analyse(pdfs.b_pdf())["stellen"] if s["seite"] == 1 and s["person"] == 1][0]
        self.assertLess(s["y1"], 842 - 710)

    def test_personen_sind_links_nach_rechts(self):
        st = [s for s in analyse(pdfs.b_pdf())["stellen"] if s["seite"] == 1]
        self.assertLess(st[0]["x1"], st[1]["x0"])
        self.assertEqual([s["person"] for s in st], [1, 2])

    def test_typ_nie_am_namen_sondern_am_aufbau(self):
        self.assertEqual(analyse(pdfs.a_pdf())["typ"], "A")
        self.assertEqual(analyse(pdfs.b_pdf())["typ"], "B")

    def test_unbekanntes_dokument(self):
        from reportlab.pdfgen import canvas
        p = io.BytesIO()
        c = canvas.Canvas(p)
        for i in range(12):
            c.drawString(50, 800 - i * 14, "Ein ganz gewöhnlicher Text ohne Unterschriftsblock, nur zum Füllen der Textebene dieses Dokuments.")
        c.showPage()
        c.save()
        with self.assertRaises(Abgelehnt) as k:
            analyse(p.getvalue())
        self.assertEqual(k.exception.code, "typ_unbekannt")

    def test_bild_pdf_wird_abgewiesen(self):
        with self.assertRaises(Abgelehnt) as k:
            analyse(pdfs.bild_pdf())
        self.assertEqual(k.exception.code, "keine_textebene")

    def test_stellen_unklar(self):
        for d in (pdfs.b_pdf(bloecke=(1, 2, 3, 4, 5), seiten=5),                 # zu viele Blöcke
                  pdfs.a_pdf(zweiter_block=True)):                                 # zwei A-Stellen
            with self.assertRaises(Abgelehnt) as k:
                analyse(d)
            self.assertEqual(k.exception.code, "stellen_unklar")

    def test_b_ohne_unterschriftsblock_ist_nicht_erkannt(self):
        with self.assertRaises(Abgelehnt) as k:
            analyse(pdfs.b_pdf(bloecke=()))
        self.assertEqual(k.exception.code, "typ_unbekannt")

    def test_a_ohne_mitwirkungsblock_ist_nicht_erkannt(self):
        with self.assertRaises(Abgelehnt) as k:
            analyse(pdfs.a_pdf(mitwirkung=False))
        self.assertEqual(k.exception.code, "typ_unbekannt")


class TestWeisseZeile(unittest.TestCase):
    def mail(self, **kw):
        e = analyse(pdfs.b_pdf(**kw))
        return e["werte"].get("mail"), e["hinweise"]

    def test_weiss_in_rgb_grau_cmyk_und_fast_weiss(self):
        for modus in ("rgb", "gray", "cmyk", "fast"):
            m, h = self.mail(weiss=modus)
            self.assertEqual(m, pdfs.MAIL, modus)
            self.assertEqual(h, [], modus)

    def test_nicht_weiss_wird_nicht_gelesen(self):
        for modus in ("hellgrau", "cover", "alpha"):                       # sichtbar bzw. nur per Deckfläche/Transparenz versteckt
            m, h = self.mail(weiss=modus)
            self.assertIsNone(m, modus)
            self.assertIn("mail_fehlt", h)

    def test_ohne_weisse_zeile(self):
        m, h = self.mail(weisse_zeile=None)
        self.assertIsNone(m)
        self.assertIn("mail_fehlt", h)

    def test_zwei_weisse_zeilen_sind_mehrdeutig(self):
        e = analyse(pdfs.b_pdf(zweite_weisse="1234567; Anna; Beispiel; anna@example.org"))
        for k in ("mail", "name", "vorname"):
            self.assertNotIn(k, e["werte"])

    def test_ungueltige_mail_wird_nicht_uebernommen(self):
        for z in ("{mnr}; {vorname}; {name}; x@y", "{mnr}; {vorname}; {name}; a b@c.de", "{mnr}; {vorname}; {name}; <a>@c.de",
                  "{mnr}; {vorname}; {name}; a@b@c.de", "{mnr}; {vorname}; {name}; ", "{mnr}; {vorname}; {name}; a@c.de; zweite@c.de"):
            m, _ = self.mail(weisse_zeile=z)
            self.assertIsNone(m, z)

    def test_mitgliedsnummer_unterschiedlich_in_weisser_zeile_und_text(self):
        e = analyse(pdfs.b_pdf(weisse_zeile="7654321; {vorname}; {name}; {mail}"))
        self.assertNotIn("mitgliedsnr", e["werte"])
        self.assertIn("mitgliedsnr_abweichend_in_b", e["hinweise"])

    def test_leerzeichen_ohne_eigene_farbe_bleiben_in_der_weissen_zeile(self):
        """Ein Leerzeichen kann in manchen Erzeugern ohne Farbangabe stehen; es gehört trotzdem zur weißen Zeile."""
        def z(text, x, farbe, top=35.0):
            return {"text": text, "x0": x, "x1": x + 5, "top": top, "non_stroking_color": farbe}
        weiss, schwarz = (1.0,), (0.0,)
        zeichen = [z("A", 0, weiss), z("n", 5, weiss), z(" ", 10, schwarz), z("M", 15, weiss), z("x", 20, schwarz, top=300.0)]
        seite = mock.Mock(chars=zeichen)
        self.assertEqual(pdf_analyse._weisse_zeilen(seite), ["An M"])

    def test_namen_mit_leerzeichen_bleiben_erhalten(self):
        e = analyse(pdfs.b_pdf(weisse_zeile="{mnr}; Anna Maria; von Beispiel; {mail}"))
        self.assertEqual((e["werte"]["vorname"], e["werte"]["name"]), ("Anna Maria", "von Beispiel"))

    def test_mail_wird_kleingeschrieben(self):
        e = analyse(pdfs.b_pdf(weisse_zeile="{mnr}; {vorname}; {name}; Zzyzx.Beispiel@Example.ORG"))
        self.assertEqual(e["werte"]["mail"], pdfs.MAIL)

    def test_ist_weiss_grenzen(self):
        w = pdf_analyse.ist_weiss
        self.assertTrue(all((w((1.0,)), w((1, 1, 1)), w((0, 0, 0, 0)), w(1), w((0.98, 0.99, 1)))))
        self.assertFalse(any((w(None), w((0,)), w((0.9,)), w((0, 0, 0, 1)), w("/P1"), w((1, 1)), w((1, 1, 0.5)), w((0, 0, 0, 0.5)), w(()))))


class TestWerte(unittest.TestCase):
    def test_werte_a_nur_aus_hauptvordruck_und_lage(self):
        w = analyse(pdfs.a_pdf())["werte"]
        self.assertEqual(w["steuernummern"], [pdfs.STNR_ZIFFERN])
        self.assertEqual(w["ids"], sorted([pdfs.ID_A, pdfs.ID_B]))
        self.assertNotIn("99999999999", w["ids"])                         # Anlage Kind zählt nicht
        self.assertEqual((w["geburtsdatum"], w["name"], w["vorname"], w["mitgliedsnr"]), (pdfs.DOB, pdfs.NAME, pdfs.VORNAME, pdfs.MNR))

    def test_werte_a_einzelperson_nur_eine_id(self):
        self.assertEqual(analyse(pdfs.a_pdf(ehegatte=False))["werte"]["ids"], [pdfs.ID_A])

    def test_mitgliedsnummer_nur_ueber_die_lage(self):
        self.assertNotIn("mitgliedsnr", analyse(pdfs.a_pdf(mnr="12345"))["werte"])                   # keine 7 Ziffern
        self.assertNotIn("mitgliedsnr", analyse(pdfs.a_pdf(mnr="1234567X"))["werte"])
        self.assertNotIn("mitgliedsnr", analyse(pdfs.a_pdf(mnr="12345678"))["werte"])                # 8 Ziffern
        e = analyse(pdfs.a_pdf(doppelt=True))                                                         # zwei 7-stellige Zeilen im Block: mehrdeutig
        self.assertNotIn("mitgliedsnr", e["werte"])

    def test_mitgliedsnummer_beide_layouts(self):
        """Ehepaar-Beispiel (Zeile \"15720 FV NRW\" davor), Einzelperson-Beispiel (zwei Zahlen davor), und ganz ohne Zeile davor: immer die einzelne 7-stellige Zeile."""
        for davor in ("15720 FV NRW", "12345 7654321", "99999 FV XYZ", None):
            e = analyse(pdfs.a_pdf(fv_zeile=davor))
            self.assertEqual(e["werte"].get("mitgliedsnr"), pdfs.MNR, davor)

    def test_mitgliedsnummer_nur_direkt_vor_dem_satz(self):
        """Eine 7-stellige Zeile, die nicht direkt vor 'Die Steuererklärung wurde unter Mitwirkung' steht, zählt nicht."""
        self.assertNotIn("mitgliedsnr", analyse(pdfs.a_pdf(nach_der_nummer="Noch eine Zeile dazwischen"))["werte"])

    def test_mitgliedsnummer_fehlt(self):
        self.assertNotIn("mitgliedsnr", analyse(pdfs.a_pdf(mnr=""))["werte"])
        self.assertNotIn("mitgliedsnr", analyse(pdfs.a_pdf(mitwirkung_satz=False))["werte"])      # Satz danach fehlt: Lage nicht belegt

    def test_werte_b(self):
        w = analyse(pdfs.b_pdf())["werte"]
        self.assertEqual((w["mitgliedsnr"], w["mail"], w["name"], w["vorname"]), (pdfs.MNR, pdfs.MAIL, pdfs.NAME, pdfs.VORNAME))
        self.assertEqual(w["steuernummern"], sorted([pdfs.STNR_ZIFFERN, pdfs.STNR_EHE_ZIFFERN]))
        self.assertEqual(w["ids"], sorted([pdfs.ID_A, pdfs.ID_B]))

    def test_b_ohne_mitgliedsnummer_im_text_nimmt_die_weisse_zeile(self):
        self.assertEqual(analyse(pdfs.b_pdf(text_mnr=False))["werte"]["mitgliedsnr"], pdfs.MNR)


def _w(**kw):
    basis = {"steuernummern": ["123456701"], "ids": ["98765432101", "98765432102"], "mitgliedsnr": "1234567"}
    basis.update(kw)
    return {k: v for k, v in basis.items() if v is not None}


class TestAbgleich(unittest.TestCase):
    def test_drei_von_drei(self):
        r = abgleich.abgleichen(_w(), _w())
        self.assertEqual((r["status"], r["treffer"], r["abweichend"], r["fehlend"]), ("automatisch", list(abgleich.KRITERIEN), [], []))

    def test_zwei_von_drei(self):
        for kw, abw in (({"ids": ["99999999999"]}, "steuer_id"), ({"mitgliedsnr": "7654321"}, "mitgliedsnummer"), ({"steuernummern": ["999999999"]}, "steuernummer")):
            r = abgleich.abgleichen(_w(), _w(**kw))
            self.assertEqual((r["status"], r["abweichend"]), ("automatisch", [abw]), kw)

    def test_ein_treffer_braucht_bestaetigung(self):
        r = abgleich.abgleichen(_w(), _w(ids=["99999999999"], mitgliedsnr="7654321"))
        self.assertEqual((r["status"], r["treffer"]), ("bestaetigung_noetig", ["steuernummer"]))

    def test_fehlendes_zaehlt_nicht(self):
        r = abgleich.abgleichen(_w(mitgliedsnr=None), _w())
        self.assertEqual((r["status"], r["fehlend"], len(r["treffer"])), ("automatisch", ["mitgliedsnummer"], 2))
        r = abgleich.abgleichen(_w(mitgliedsnr=None, ids=None), _w())
        self.assertEqual((r["status"], r["treffer"]), ("bestaetigung_noetig", ["steuernummer"]))
        self.assertEqual(abgleich.abgleichen({}, {})["status"], "bestaetigung_noetig")

    def test_alle_ids_muessen_uebereinstimmen(self):
        r = abgleich.abgleichen(_w(), _w(ids=["98765432101"]))               # B nennt nur eine von zwei IDs
        self.assertIn("steuer_id", r["abweichend"])
        r = abgleich.abgleichen(_w(ids=["98765432101"]), _w(ids=["98765432101"]))
        self.assertIn("steuer_id", r["treffer"])

    def test_steuernummer_von_a_muss_unter_denen_von_b_stehen(self):
        b = _w(steuernummern=["999999999", "123456701"])
        self.assertIn("steuernummer", abgleich.abgleichen(_w(steuernummern=["123456701"]), b)["treffer"])
        self.assertIn("steuernummer", abgleich.abgleichen(_w(steuernummern=["888888888"]), b)["abweichend"])
        self.assertNotIn("steuernummer", abgleich.abgleichen(_w(steuernummern=["123456701", "999999999"]), b)["treffer"])   # A mehrdeutig

    def test_ergebnis_enthaelt_keine_personendaten(self):
        r = json.dumps(abgleich.abgleichen(_w(), _w()))
        for v in ("98765432101", "1234567", "123456701"):
            self.assertNotIn(v, r)


class TestZusammenfuehren(unittest.TestCase):
    def test_seiten_reihenfolge_und_versatz(self):
        b, a = pdfs.b_pdf(), pdfs.a_pdf()
        eb, ea = analyse(b), analyse(a)
        daten, stellen = abgleich.zusammenfuehren(b, a, eb["stellen"], ea["stellen"])
        r = PdfReader(io.BytesIO(daten))
        self.assertEqual(len(r.pages), 16)
        self.assertIn("Mitglieds-Nr.", r.pages[0].extract_text())
        self.assertIn("Vorschau", r.pages[4].extract_text())
        self.assertEqual([(s["dokument"], s["seite"]) for s in stellen if s["dokument"] == "A"], [("A", 15), ("A", 15)])
        self.assertEqual([s["seite"] for s in stellen if s["dokument"] == "B"], [1, 1, 4, 4])
        # die verschobenen Stellen liegen wirklich auf der Unterschriftsseite des zusammengeführten PDFs
        self.assertIn("Datum, Unterschrift", r.pages[14].extract_text())
        self.assertIn("Neuss", r.pages[3].extract_text())

    def test_aktive_stellen_ehepaar_sechs_einzeln_drei(self):
        b, a = pdfs.b_pdf(), pdfs.a_pdf()
        _, stellen = abgleich.zusammenfuehren(b, a, analyse(b)["stellen"], analyse(a)["stellen"])
        self.assertEqual(len(abgleich.aktive_stellen(stellen, 2)), 6)
        self.assertEqual(len(abgleich.aktive_stellen(stellen, 1)), 3)
        with self.assertRaises(ValueError):
            abgleich.aktive_stellen(stellen, None)
        with self.assertRaises(ValueError):
            abgleich.aktive_stellen(stellen, 3)

    def test_zusammengefuehrtes_pdf_ist_wieder_auswertbar_ohne_aktive_inhalte(self):
        b, a = pdfs.b_pdf(), pdfs.a_pdf()
        daten, _ = abgleich.zusammenfuehren(b, a, [], [])
        pdf_pruefung.pruefe(daten)


class TestWorker(unittest.TestCase):
    def test_kindprozess_mit_leerer_umgebung_und_ohne_cookie(self):
        gesehen = {}
        echt = subprocess.run

        def spion(*a, **kw):
            gesehen.update(kw)
            return echt(*a, **kw)
        with mock.patch("pdf_eingang.subprocess.run", side_effect=spion):
            self.assertEqual(pdf_eingang.auswerten(pdfs.b_pdf())["typ"], "B")
        self.assertTrue(set(gesehen["env"]) <= {"PATH", "LANG", "PYTHONDONTWRITEBYTECODE"})
        self.assertEqual(gesehen["cwd"], tempfile.gettempdir())
        self.assertLessEqual(gesehen["timeout"], 60)

    def test_ablehnungen_kommen_mit_code_an(self):
        for daten, code in ((b"kein pdf", "kein_pdf"), (pdfs.bild_pdf(), "keine_textebene"), (pdfs.mit_javascript(pdfs.b_pdf(), "js"), "aktive_inhalte"),
                            (b"%PDF-" + b"0" * (pdf_pruefung.MAX_BYTES + 1), "zu_gross")):
            with self.assertRaises(pdf_eingang.Fehler) as k:
                pdf_eingang.auswerten(daten)
            self.assertEqual(k.exception.code, code)

    def test_zeitueberschreitung(self):
        with mock.patch("pdf_eingang.TIMEOUT_SEKUNDEN", 0.001):
            with self.assertRaises(pdf_eingang.Fehler) as k:
                pdf_eingang.auswerten(pdfs.b_pdf())
        self.assertEqual(k.exception.code, "zeitueberschreitung")

    def test_abgestuerzter_kindprozess(self):
        gueltig = b'{"ok": true, "typ": "B", "seiten": 1, "stellen": [], "personen": 1, "werte": {}, "hinweise": []}'
        for out in (b"", gueltig):                                   # auch gültige Ausgabe zählt nicht, wenn der Prozess abgestürzt ist
            class P:
                returncode, stdout, stderr = -9, out, b""
            with mock.patch("pdf_eingang.subprocess.run", return_value=P):
                with self.assertRaises(pdf_eingang.Fehler) as k:
                    pdf_eingang.auswerten(pdfs.b_pdf())
            self.assertEqual(k.exception.code, "analyse_fehlgeschlagen")

    def test_unsinnige_ausgabe(self):
        for out in (b"keine json", b"[]", b'{"ok": false}', b"x" * (pdf_eingang.MAX_AUSGABE + 1)):
            class P:
                returncode, stdout, stderr = 0, out, b""
            with mock.patch("pdf_eingang.subprocess.run", return_value=P):
                with self.assertRaises(pdf_eingang.Fehler):
                    pdf_eingang.auswerten(pdfs.b_pdf())

    def test_worker_setzt_grenzen(self):
        with open(pdf_eingang.WORKER, encoding="utf-8") as f:
            quelle = f.read()
        self.assertIn("RLIMIT_AS", quelle)
        self.assertIn("RLIMIT_CPU", quelle)


def hochladen(c, *dateien, namen=None):
    daten = {"dateien": [(io.BytesIO(d), (namen or ["datei%d.pdf" % i for i in range(len(dateien))])[i]) for i, d in enumerate(dateien)]}
    return c.post("/api/v1/vorgaenge", data=daten, content_type="multipart/form-data")


class RouteBasis(Basis):
    def setUp(self):
        super().setUp()
        self.als("ta", me(ref="1001"))

    def alle_dateien(self):
        wurzel = db.dokumente_verzeichnis("1001").parent
        return sorted(str(p.relative_to(wurzel)) for p in wurzel.rglob("*") if p.is_file()) if wurzel.is_dir() else []

    def db_text(self):
        with db.verbindung() as c:
            return json.dumps([[dict(z) for z in c.execute("SELECT * FROM %s" % t)] for t in ("vorgang", "dokument", "audit_log")], default=str)


class TestUpload(RouteBasis):
    def test_a_und_b_automatisch_zusammengefuehrt(self):
        r = hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf())
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual((j["status"], j["abgleich"]["status"], j["personen"], j["mail_erkannt"]), ("bereit", "automatisch", 2, True))
        self.assertEqual([(d["rolle"], d["seiten"], d["stellen"]) for d in j["dokumente"]], [("A", 12, 2), ("B", 4, 4)])
        v = self.c.get("/api/v1/vorgaenge/%d" % j["vorgang_id"]).get_json()
        self.assertEqual(sorted(d["rolle"] for d in v["dokumente"]), ["A", "B"])                  # jedes Dokument bleibt einzeln (kein zusammengeführtes M mehr)
        self.assertEqual([(d["rolle"], d["seiten"], len(d["stellen"])) for d in sorted(v["dokumente"], key=lambda d: d["rolle"])], [("A", 12, 2), ("B", 4, 4)])
        self.assertEqual(len(self.alle_dateien()), 2)

    def test_dateinamen_zaehlen_nicht(self):
        r = hochladen(self.c, pdfs.a_pdf(), pdfs.b_pdf(), namen=["anschreiben_b.pdf", "elster_a.pdf"])
        self.assertEqual(r.get_json()["status"], "bereit")
        for pfad in self.alle_dateien():
            self.assertRegex(os.path.basename(pfad), r"^[0-9a-f]{32}\.pdf$")

    def test_nur_a_nur_b(self):
        for d, rolle in ((pdfs.a_pdf(), "A"), (pdfs.b_pdf(), "B")):
            j = hochladen(self.c, d).get_json()
            self.assertEqual((j["status"], j["abgleich"]["status"], [x["rolle"] for x in j["dokumente"]]), ("bereit", "einzeln", [rolle]))
        self.assertEqual(len(self.alle_dateien()), 2)                    # kein zusammengeführtes PDF
        self.assertIsNone(hochladen(self.c, pdfs.a_pdf()).get_json()["personen"])
        self.assertEqual(hochladen(self.c, pdfs.b_pdf(personen=1)).get_json()["personen"], 1)

    def test_falsche_kombinationen(self):
        for ds, code, st in (((pdfs.a_pdf(), pdfs.a_pdf()), "typen_passen_nicht", 422), ((pdfs.b_pdf(), pdfs.b_pdf()), "typen_passen_nicht", 422),
                             ((), "dateizahl", 400), ((pdfs.a_pdf(), pdfs.b_pdf(), pdfs.b_pdf()), "dateizahl", 400)):
            r = hochladen(self.c, *ds)
            self.assertEqual((r.status_code, r.get_json()["error"]), (st, code), code)
        self.assertEqual(self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"], [])
        self.assertEqual(self.alle_dateien(), [])

    def test_ablehnungen_legen_nichts_an(self):
        for d, code in ((b"hallo", "kein_pdf"), (pdfs.bild_pdf(), "keine_textebene"), (pdfs.mit_encrypt(pdfs.b_pdf()), "verschluesselt"),
                        (pdfs.mit_javascript(pdfs.b_pdf(), "openaction"), "aktive_inhalte"), (pdfs.b_pdf(seiten=31, bloecke=(1,)), "zu_viele_seiten"),
                        (pdfs.b_pdf()[:2000], "defekt")):
            for ds in ((d,), (d, pdfs.a_pdf()), (pdfs.b_pdf(), d)):
                r = hochladen(self.c, *ds)
                self.assertEqual((r.status_code, r.get_json()["error"]), (422, code), code)
        self.assertEqual(self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"], [])
        self.assertEqual(self.alle_dateien(), [])
        self.assertTrue(self.audit_zeilen("upload_abgelehnt"))

    def test_keine_personendaten_in_datenbank_antwort_audit_und_logs(self):
        log = io.StringIO()
        h = logging.StreamHandler(log)
        logging.getLogger().addHandler(h)
        self.addCleanup(logging.getLogger().removeHandler, h)
        r = hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf())
        hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf(), namen=["x.pdf", "y.pdf"])
        hochladen(self.c, b"kaputt")
        gesamt = r.get_data(as_text=True) + self.db_text() + log.getvalue()
        gesamt += json.dumps(self.c.get("/api/v1/vorgaenge/%d" % r.get_json()["vorgang_id"]).get_json())
        gesamt = pdfs.ohne_vorgangsname(gesamt)                                            # der Name des Mitglieds steht gewollt im Feld 'name' des Vorgangs (nur dort)
        for wert in pdfs.PERSONENDATEN:
            self.assertNotIn(wert, gesamt, wert)
        # der Dateiinhalt selbst liegt natürlich als PDF auf der Platte, aber nur im Mandantenordner
        self.assertTrue(all(p.startswith("1001/") for p in self.alle_dateien()))

    def test_upload_audit_ohne_inhalte(self):
        hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf())
        z = self.audit_zeilen("vorgang_angelegt")
        self.assertEqual(len(z), 1)
        self.assertEqual((z[0]["mandant_ref"], z[0]["nutzer"]), ("1001", "a@test.example"))
        self.assertRegex(z[0]["details"], r"^id=\d+ status=bereit dokumente=A\+B abgleich=")

    def test_stundenlimit(self):
        with mock.patch("pdf_eingang.UPLOADS_JE_STUNDE", 2):
            self.assertEqual(hochladen(self.c, pdfs.b_pdf()).status_code, 201)
            self.assertEqual(hochladen(self.c, pdfs.b_pdf()).status_code, 201)
            r = hochladen(self.c, pdfs.b_pdf())
            self.assertEqual((r.status_code, r.get_json()["error"]), (429, "zu_viele_uploads"))
            self.als("tb", me(email="b@test.example", ref="2002"))              # anderer Mandant ist nicht betroffen
            self.assertEqual(hochladen(self.c, pdfs.b_pdf()).status_code, 201)

    def test_ohne_anmeldung_und_ohne_produkt(self):
        self.c.delete_cookie("bst_session", domain="localhost")
        self.assertEqual(hochladen(self.c, pdfs.b_pdf()).status_code, 401)
        self.als("tx", me(produkte=[]))
        self.assertEqual(hochladen(self.c, pdfs.b_pdf()).status_code, 403)
        self.assertEqual(self.alle_dateien(), [])

    def test_mitarbeiter_duerfen_hochladen(self):
        self.als("tm", me(rolle="mitarbeiter", ref="1001"))
        self.assertEqual(hochladen(self.c, pdfs.b_pdf()).status_code, 201)

    def test_aufraeumen_wenn_das_speichern_scheitert(self):
        echt = db.dokument_speichern
        zaehler = {"n": 0}

        def kaputt(*a, **kw):
            zaehler["n"] += 1
            if zaehler["n"] == 2:
                raise RuntimeError("Platte voll")
            return echt(*a, **kw)
        with mock.patch("db.dokument_speichern", side_effect=kaputt):
            with self.assertRaises(RuntimeError):
                pdf_eingang.anlegen("1001", [pdfs.b_pdf(), pdfs.a_pdf()])
        self.assertEqual(db.vorgaenge_liste("1001"), [])
        self.assertEqual(self.alle_dateien(), [])
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dokument").fetchone()[0], 0)

    def test_grosse_anfragen(self):
        import app as app_modul
        r = self.c.post("/api/v1/vorgaenge", data=b"x" * (app_modul.UPLOAD_MAX + 10), content_type="multipart/form-data; boundary=x")
        self.assertEqual(r.status_code, 413)
        r = self.c.post("/api/v1/vorgaenge/1/abgleich-bestaetigen", data=b"x" * 400000, content_type="application/json")
        self.assertEqual(r.status_code, 413)


class TestBestaetigung(RouteBasis):
    def abweichend(self):
        a = pdfs.a_pdf(stnr="999/9999/9999", id_a="11111111111", id_b="22222222222", mnr="7654321")
        return hochladen(self.c, pdfs.b_pdf(), a).get_json()

    def test_weniger_als_zwei_treffer_braucht_bestaetigung(self):
        j = self.abweichend()
        self.assertEqual((j["status"], j["abgleich"]["status"], j["abgleich"]["treffer"]), ("bestaetigung_noetig", "bestaetigung_noetig", []))
        self.assertEqual(sorted(j["abgleich"]["abweichend"]), sorted(abgleich.KRITERIEN))
        self.assertEqual(len(self.alle_dateien()), 2)                        # noch kein zusammengeführtes PDF
        v = self.c.get("/api/v1/vorgaenge/%d" % j["vorgang_id"]).get_json()
        self.assertEqual(sorted(d["rolle"] for d in v["dokumente"]), ["A", "B"])
        self.assertIsNone(v["bestaetigt_von"])

    def test_bestaetigen(self):
        j = self.abweichend()
        url = "/api/v1/vorgaenge/%d/abgleich-bestaetigen" % j["vorgang_id"]
        for body in (None, {}, {"bestaetigt": "ja"}, {"bestaetigt": False}, {"bestaetigt": 1}):
            r = self.c.post(url, json=body) if body is not None else self.c.post(url)
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % j["vorgang_id"]).get_json()["status"], "bestaetigung_noetig")
        r = self.c.post(url, json={"bestaetigt": True})
        self.assertEqual((r.status_code, r.get_json()["status"]), (200, "bereit"))
        v = self.c.get("/api/v1/vorgaenge/%d" % j["vorgang_id"]).get_json()
        self.assertEqual((v["status"], v["bestaetigt_von"]), ("bereit", "a@test.example"))
        self.assertTrue(v["bestaetigt_am"])
        self.assertEqual(sorted(d["rolle"] for d in v["dokumente"]), ["A", "B"])                  # nach der Bestätigung bleiben beide Dokumente einzeln
        self.assertEqual(len(self.audit_zeilen("abgleich_bestaetigt")), 1)
        self.assertEqual(self.c.post(url, json={"bestaetigt": True}).status_code, 409)       # nur einmal

    def test_bei_automatischem_abgleich_ist_nichts_zu_bestaetigen(self):
        j = hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf()).get_json()
        r = self.c.post("/api/v1/vorgaenge/%d/abgleich-bestaetigen" % j["vorgang_id"], json={"bestaetigt": True})
        self.assertEqual(r.status_code, 409)
        j = hochladen(self.c, pdfs.a_pdf()).get_json()
        self.assertEqual(self.c.post("/api/v1/vorgaenge/%d/abgleich-bestaetigen" % j["vorgang_id"], json={"bestaetigt": True}).status_code, 409)

    def test_fremder_mandant_kann_nicht_bestaetigen(self):
        j = self.abweichend()
        self.als("tb", me(email="b@test.example", ref="2002"))
        url = "/api/v1/vorgaenge/%d/abgleich-bestaetigen" % j["vorgang_id"]
        self.assertEqual(self.c.post(url, json={"bestaetigt": True}).status_code, 404)
        self.als("ta", me(ref="1001"))
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % j["vorgang_id"]).get_json()["status"], "bestaetigung_noetig")

    def test_unbekannter_vorgang(self):
        self.assertEqual(self.c.post("/api/v1/vorgaenge/99999/abgleich-bestaetigen", json={"bestaetigt": True}).status_code, 404)


class TestMandantentrennungUndLoeschung(RouteBasis):
    def test_fremder_mandant_sieht_nichts(self):
        j = hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf()).get_json()
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % j["vorgang_id"]).status_code, 404)
        self.assertEqual(self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"], [])
        self.assertEqual(db.dokumente_liste("2002", j["vorgang_id"]), [])
        for rolle in ("A", "B"):
            self.assertIsNone(db.dokument_bytes("2002", j["vorgang_id"], rolle))
            self.assertIsNotNone(db.dokument_bytes("1001", j["vorgang_id"], rolle))

    def test_dateien_liegen_nur_im_ordner_des_mandanten(self):
        hochladen(self.c, pdfs.b_pdf())
        self.als("tb", me(email="b@test.example", ref="2002"))
        hochladen(self.c, pdfs.a_pdf())
        wurzel = db.dokumente_verzeichnis("1001").parent
        self.assertEqual(sorted(p.name for p in wurzel.iterdir()), ["1001", "2002"])
        self.assertEqual(len(list((wurzel / "1001").rglob("*.pdf"))), 1)
        self.assertEqual(len(list((wurzel / "2002").rglob("*.pdf"))), 1)

    def test_aendern_nur_im_eigenen_mandanten(self):
        j = hochladen(self.c, pdfs.b_pdf()).get_json()
        self.assertFalse(db.vorgang_aendern("2002", j["vorgang_id"], status="gesperrt"))
        self.assertEqual(db.vorgang_lesen("1001", j["vorgang_id"])["status"], "bereit")
        self.assertTrue(db.vorgang_aendern("1001", j["vorgang_id"], status="x"))
        with self.assertRaises(ValueError):
            db.vorgang_aendern("1001", j["vorgang_id"], erstellt="2002")

    def test_ungueltiger_mandant_im_pfad(self):
        for ref in ("../x", "a/b", "", "x" * 65, ".."):
            with self.assertRaises(ValueError):
                db.dokumente_verzeichnis(ref)

    def test_loeschung_entfernt_dateien_und_zeilen_nur_dieses_mandanten(self):
        hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf())
        self.als("tb", me(email="b@test.example", ref="2002"))
        hochladen(self.c, pdfs.b_pdf())
        zahlen = db.mandant_loeschen("1001")
        self.assertEqual((zahlen["dateien"], zahlen["dokument"], zahlen["vorgang"]), (2, 2, 1))
        self.assertFalse(db.dokumente_verzeichnis("1001").exists())
        self.assertTrue(db.dokumente_verzeichnis("2002").is_dir())
        self.assertEqual(len(db.vorgaenge_liste("2002")), 1)
        self.assertEqual(len(db.dokumente_liste("2002", db.vorgaenge_liste("2002")[0]["id"])), 1)
        with db.verbindung() as c:
            for t in db.MANDANTENTABELLEN:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM %s WHERE mandant_ref='1001'" % t).fetchone()[0], 0, t)
        self.assertEqual(db.mandant_loeschen("1001"), {"download_log": 0, "download_link": 0, "erinnerung": 0, "sitzung": 0, "unterschrift": 0, "zugriff_log": 0, "dokument": 0, "link": 0, "versand": 0, "mail_zugang": 0, "stelle_einstellung": 0, "stelle_tarif": 0, "zaehleintraege": 0, "vorgang": 0, "audit_log": 0, "handoff": 0, "abholung": 0, "dateien": 0})

    def test_migration_alter_datenbank(self):
        pfad = os.path.join(self.tmp.name, "alt.db")
        c = sqlite3.connect(pfad)
        c.executescript("CREATE TABLE vorgang (id INTEGER PRIMARY KEY AUTOINCREMENT, mandant_ref TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'neu', erstellt TEXT NOT NULL);"
                        "INSERT INTO vorgang(mandant_ref, erstellt) VALUES ('1001', 'z');")
        c.commit()
        c.close()
        db.init(pfad)
        v = db.vorgang_lesen("1001", 1)
        self.assertEqual((v["status"], v["personen"], v["abgleich"]), ("neu", None, None))


ECHT = "/home/sysop/Documents/Pandora/catrin-58c8bb22/SignNext/Allgemein/uploads/"


@unittest.skipUnless(os.path.isfile(ECHT + "Test Unterschrift VE.pdf") and os.path.isfile(ECHT + "Test ELSTER_Steuerdaten.pdf"),
                     "echte Testdateien nicht vorhanden (optionaler Messlauf)")
class TestEchteTestdateien(unittest.TestCase):
    """Optionaler Messlauf: gibt nichts aus, prüft nur, dass Regeln und Grenzen die echten Beispiele nicht treffen."""

    def test_beide_werden_angenommen_und_richtig_erkannt(self):
        with open(ECHT + "Test Unterschrift VE.pdf", "rb") as f:
            b = f.read()
        with open(ECHT + "Test ELSTER_Steuerdaten.pdf", "rb") as f:
            a = f.read()
        self.assertLess(len(b), pdf_pruefung.MAX_BYTES)
        self.assertLess(len(a), pdf_pruefung.MAX_BYTES)
        eb, ea = pdf_eingang.auswerten(b), pdf_eingang.auswerten(a)
        self.assertEqual((eb["typ"], eb["seiten"], eb["personen"], len(eb["stellen"])), ("B", 4, 2, 4))
        self.assertEqual((ea["typ"], ea["seiten"], len(ea["stellen"])), ("A", 12, 2))
        self.assertLessEqual(eb["seiten"], pdf_pruefung.MAX_SEITEN)
        self.assertLessEqual(ea["seiten"], pdf_pruefung.MAX_SEITEN)
        self.assertIn("mail", eb["werte"])
        self.assertEqual(abgleich.abgleichen(ea["werte"], eb["werte"])["status"], "automatisch")


import glob

EINZEL = glob.glob("/workspace/room-files/roomfile-025761cd-3b07-45e9-ae57-e1bf36fab1f4_94307009-851a-46ff-97dd-df98db4fb3b5_31d98a.pdf")


@unittest.skipUnless(EINZEL, "echtes Einzelperson-Beispiel nicht vorhanden (optionaler Messlauf)")
class TestEchtesEinzelpersonBeispiel(unittest.TestCase):
    """Optional, nur lokal, gibt nichts aus: Das Einzelperson-Beispiel zu Dokument A wird angenommen, richtig erkannt, die Mitgliedsnummer gefunden."""

    def test_erkennung(self):
        with open(EINZEL[0], "rb") as f:
            e = pdf_eingang.auswerten(f.read())
        self.assertEqual((e["typ"], e["seiten"], e["personen"]), ("A", 6, None))
        self.assertEqual(sorted((s["seite"], s["person"]) for s in e["stellen"]), [(5, 1), (5, 2)])
        self.assertNotIn("a_nennt_ehegatten", e["hinweise"])
        self.assertEqual(len(e["werte"]["ids"]), 1)
        self.assertRegex(e["werte"].get("mitgliedsnr", ""), r"^\d{7}$")


if __name__ == "__main__":
    unittest.main()
