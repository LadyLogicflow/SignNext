"""v0.7.0 Download-Dateinamen (catrin 18.11.2026): <Mitgliedsnummer>_<Nachname>_FZ.pdf (Protokoll, A) / _VE.pdf (Vollständigkeitserklärung, B), Nachweis mit _NWU.
Mitgliedsnummer und Nachname liegen in eigenen Feldern am Vorgang (wie vorgang.name): nie in Audit, Log, Mail, Dokument-Tabelle; geleert beim Löschen; nur eigener Mandant.
Fehlt etwas oder ist es ein Altvorgang: der bisherige Name. Nur erfundene Daten."""
import inspect
import re
from urllib.parse import unquote

import db
import dokumente
import download
import loeschen
import pdf_eingang
from tests import pdfs
from tests.test_baustein5 import B5

DL = "/api/v1/vorgaenge/%d/download/%s?dokument=%s"


def dateiname_aus_kopf(antwort) -> str:
    kopf = antwort.headers["Content-Disposition"]
    return unquote(re.search(r"filename\*=UTF-8''([^;]+)", kopf).group(1))


class TestNamensbildung(B5):
    """Reine Funktionen, ohne Vorgang."""
    runTest = lambda self: None

    def test_regelfall(self):
        self.assertEqual(dokumente.dateinamen(5, "A", 2, "1234567", "Muster"), ("1234567_Muster_FZ.pdf", "1234567_Muster_FZ_NWU.pdf"))
        self.assertEqual(dokumente.dateinamen(5, "B", 2, "1234567", "Muster"), ("1234567_Muster_VE.pdf", "1234567_Muster_VE_NWU.pdf"))

    def test_einzeldokument_gilt_genauso(self):
        self.assertEqual(dokumente.dateinamen(5, "B", 1, "1234567", "Muster")[0], "1234567_Muster_VE.pdf")

    def test_umlaute_bleiben_sonderzeichen_gehen(self):
        self.assertEqual(dokumente.dateinamen(5, "B", 2, "1234567", "Müller-Öztürk")[0], "1234567_Müller-Öztürk_VE.pdf")
        self.assertEqual(dokumente.dateinamen(5, "B", 2, "1234567", "../a/b\\c:d*e?f\"g<h>i|j_k\n")[0], "1234567_a b c d e f g h i j k_VE.pdf")
        self.assertEqual(dokumente.dateinamen(5, "A", 2, "1234567", "  van   der  Berg. ")[0], "1234567_van der Berg_FZ.pdf")
        self.assertEqual(dokumente.dateinamen(5, "A", 2, "1234567", "A" * 300)[0], "1234567_" + "A" * 60 + "_FZ.pdf")

    def test_ersatz_wie_bisher(self):
        ersatz2 = {"B": ("vorgang-5-anschreiben-dokument.pdf", "vorgang-5-anschreiben-nachweis.pdf"), "A": ("vorgang-5-protokoll-dokument.pdf", "vorgang-5-protokoll-nachweis.pdf")}
        for rolle in ("A", "B"):
            for nr, name in ((None, "Muster"), ("1234567", None), ("1234567", ""), ("1234567", "///"), ("123456", "Muster"), ("12345678", "Muster"), ("12345a7", "Muster")):
                with self.subTest(rolle=rolle, nr=nr, name=name):
                    self.assertEqual(dokumente.dateinamen(5, rolle, 2, nr, name), ersatz2[rolle])
        self.assertEqual(dokumente.dateinamen(5, "B", 1, None, None), ("vorgang-5-dokument.pdf", "vorgang-5-nachweis.pdf"))
        self.assertEqual(dokumente.dateinamen(5, "M", 1, "1234567", "Muster"), ("vorgang-5-dokument.pdf", "vorgang-5-nachweis.pdf"))     # Altvorgang (zusammengeführt)

    def test_anzeige_titel_neu_nachweis_und_ersatz_bleiben(self):
        """catrin 18.11. 14:47: Anzeige 'Vollständigkeitserklärung'/'Freizeichnungsprotokoll'; die Nachweis-Zeile und die Ersatz-Dateinamen behalten den alten Wortlaut, bis catrin freigibt."""
        self.assertEqual(dokumente.TITEL, {"B": "Vollständigkeitserklärung", "A": "Freizeichnungsprotokoll", "M": "Dokument"})
        self.assertEqual(dokumente.NACHWEIS_TITEL, {"B": "Anschreiben", "A": "Protokoll"})
        self.assertEqual(dokumente.ERSATZ_NAME, {"B": "anschreiben", "A": "protokoll"})

    def test_felder_aus_dem_pdf(self):
        self.assertEqual(pdf_eingang.dateiname_felder({"mitgliedsnr": "1234567", "name": "Muster"}, {"mitgliedsnr": "7654321", "name": "Andere"}), ("1234567", "Muster"))
        self.assertEqual(pdf_eingang.dateiname_felder({}, {"mitgliedsnr": "7654321", "name": " Andere\n"}), ("7654321", "Andere"))          # Anschreiben als Ersatz
        self.assertEqual(pdf_eingang.dateiname_felder({"mitgliedsnr": "12345", "name": ""}, {"name": None}), (None, None))
        self.assertEqual(pdf_eingang.dateiname_felder({}, {}), (None, None))

    def test_kopfzeile_ascii_mit_utf8_namen(self):
        kopf = download._content_disposition("1234567_Müller-Öztürk_VE.pdf")
        kopf.encode("ascii")                                                                   # keine Nicht-ASCII-Zeichen im Header
        self.assertIn('filename="1234567_Mueller-Oeztuerk_VE.pdf"', kopf)
        self.assertIn("filename*=UTF-8''1234567_M%C3%BCller-%C3%96zt%C3%BCrk_VE.pdf", kopf)
        self.assertEqual(download._content_disposition("dokument.pdf"), 'attachment; filename="dokument.pdf"; filename*=UTF-8\'\'dokument.pdf')


class TestVorgangUndDownload(B5):
    def abgeschlossen(self):
        self.unterschreiben()
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")

    def detail(self):
        return self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_json()

    def test_namen_in_api_und_download_stimmen_ueberein(self):
        self.abgeschlossen()
        d = {x["rolle"]: x for x in self.detail()["dokumente_fortschritt"]}
        self.assertEqual((d["A"]["dateiname"], d["A"]["nachweis_dateiname"]), ("%s_%s_FZ.pdf" % (pdfs.MNR, pdfs.NAME), "%s_%s_FZ_NWU.pdf" % (pdfs.MNR, pdfs.NAME)))
        self.assertEqual((d["B"]["dateiname"], d["B"]["nachweis_dateiname"]), ("%s_%s_VE.pdf" % (pdfs.MNR, pdfs.NAME), "%s_%s_VE_NWU.pdf" % (pdfs.MNR, pdfs.NAME)))
        for rolle in ("A", "B"):
            for art, feld in (("dokument", "dateiname"), ("nachweis", "nachweis_dateiname")):
                with self.subTest(rolle=rolle, art=art):
                    r = self.c.get(DL % (self.vid, art, rolle))
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(dateiname_aus_kopf(r), d[rolle][feld])

    def test_titel_in_der_api_und_nachweis_zeile(self):
        self.abgeschlossen()
        d = {x["rolle"]: x for x in self.detail()["dokumente_fortschritt"]}
        self.assertEqual((d["B"]["titel"], d["A"]["titel"]), ("Vollständigkeitserklärung", "Freizeichnungsprotokoll"))
        import io
        from pypdf import PdfReader
        for rolle, alt in (("B", "Anschreiben"), ("A", "Protokoll")):
            text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(self.c.get(DL % (self.vid, "nachweis", rolle)).data)).pages)
            self.assertIn("Dokument: %s" % alt, text)                              # Nachweis-Wortlaut unverändert bis zur Freigabe

    def test_upload_fehlermeldungen_mit_neuen_titeln(self):
        import api
        for code in ("typ_unbekannt", "typen_passen_nicht"):
            self.assertIn("Freizeichnungsprotokoll", api.MELDUNGEN[code])
            self.assertIn("Vollständigkeitserklärung", api.MELDUNGEN[code])
            self.assertNotIn("Steuerprotokoll", api.MELDUNGEN[code])

    def test_namen_schon_vor_dem_abschluss_immer_strings(self):
        self.starten()
        for x in self.detail()["dokumente_fortschritt"]:
            self.assertIsInstance(x["dateiname"], str)
            self.assertIsInstance(x["nachweis_dateiname"], str)
            self.assertTrue(x["dateiname"].endswith(".pdf") and x["nachweis_dateiname"].endswith("_NWU.pdf"))

    def test_nummer_und_nachname_selbst_gehen_nicht_nach_aussen(self):
        self.abgeschlossen()
        liste = self.c.get("/api/v1/vorgaenge").get_data(as_text=True)
        self.assertNotIn(pdfs.MNR, liste)                                                       # (der Vorgangsname 'Vorname Name' steht dort schon seit 18.11. gewollt)
        for feld in ('"mitgliedsnr"', '"nachname"', "dateiname"):
            self.assertNotIn(feld, liste)
        j = self.detail()
        self.assertNotIn("mitgliedsnr", j)
        self.assertNotIn("nachname", j)
        roh = self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_data(as_text=True)
        self.assertNotIn('"mitgliedsnr"', roh)
        self.assertNotIn('"nachname"', roh)

    def test_felder_sind_gespeichert_und_nur_fuer_den_eigenen_mandanten_lesbar(self):
        self.abgeschlossen()
        self.assertEqual(db.dateiname_quellen("1001", self.vid), (pdfs.MNR, pdfs.NAME))
        self.assertEqual(db.dateiname_quellen("2002", self.vid), (None, None))

    def test_altvorgang_ohne_felder_bekommt_die_bisherigen_namen(self):
        self.abgeschlossen()
        with db.verbindung() as c:
            c.execute("UPDATE vorgang SET mitgliedsnr=NULL, nachname=NULL WHERE id=?", (self.vid,))
        d = {x["rolle"]: x for x in self.detail()["dokumente_fortschritt"]}
        self.assertEqual(d["B"]["dateiname"], "vorgang-%d-anschreiben-dokument.pdf" % self.vid)
        self.assertEqual(d["A"]["nachweis_dateiname"], "vorgang-%d-protokoll-nachweis.pdf" % self.vid)
        r = self.c.get(DL % (self.vid, "nachweis", "A"))
        self.assertEqual(dateiname_aus_kopf(r), "vorgang-%d-protokoll-nachweis.pdf" % self.vid)

    def test_nur_nachname_fehlt_ersatz(self):
        self.abgeschlossen()
        with db.verbindung() as c:
            c.execute("UPDATE vorgang SET nachname=NULL WHERE id=?", (self.vid,))
        self.assertEqual(dateiname_aus_kopf(self.c.get(DL % (self.vid, "dokument", "B"))), "vorgang-%d-anschreiben-dokument.pdf" % self.vid)

    def test_umlaut_nachname_im_download(self):
        self.abgeschlossen()
        with db.verbindung() as c:
            c.execute("UPDATE vorgang SET nachname=? WHERE id=?", ("Müller-Öztürk", self.vid))
        r = self.c.get(DL % (self.vid, "dokument", "A"))
        self.assertEqual(dateiname_aus_kopf(r), "%s_Müller-Öztürk_FZ.pdf" % pdfs.MNR)
        r.headers["Content-Disposition"].encode("ascii")

    def test_loeschen_leert_die_felder(self):
        self.abgeschlossen()
        self.assertIsNotNone(loeschen.vorgang_loeschen("1001", self.vid, "storno"))
        with db.verbindung() as c:
            z = c.execute("SELECT mitgliedsnr, nachname, name FROM vorgang WHERE id=?", (self.vid,)).fetchone()
        self.assertEqual((z["mitgliedsnr"], z["nachname"], z["name"]), (None, None, None))

    def test_vertragsende_leert_die_felder(self):
        quelle = inspect.getsource(db)
        self.assertIn("mitgliedsnr=NULL, nachname=NULL WHERE mandant_ref=? AND gezaehlt_am IS NOT NULL", quelle)

    def test_felder_stehen_nicht_in_oeffentlichen_vorgangsfeldern(self):
        self.assertNotIn("mitgliedsnr", db.VORGANG_OEFFENTLICH)
        self.assertNotIn("nachname", db.VORGANG_OEFFENTLICH)

    def test_audit_und_log_ohne_nummer_und_nachname(self):
        self.abgeschlossen()
        self.c.get(DL % (self.vid, "dokument", "B"))
        gesamt = self.logtext.getvalue() if hasattr(self, "logtext") else ""
        with db.verbindung() as c:
            gesamt += " ".join("%s %s" % (z["aktion"], z["details"]) for z in c.execute("SELECT aktion, details FROM audit_log"))
        for wert in (pdfs.MNR, pdfs.NAME):
            self.assertNotIn(wert, gesamt)
