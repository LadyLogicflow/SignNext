"""jsdom-Prüfung des Upload-Bereichs der Vorgangsliste (Konzept 17.11.2026, T2): Ablegen/Auswählen, Prüfung im Browser, Fortschritt, Fehler im Text, Erfolg mit Weiterleitung.
Die Schnittstelle (POST /api/v1/vorgaenge) spielt der Treiber nach den Antwortformen des Backends nach (pdf_eingang.anlegen, api._fehler)."""
import unittest

from ui_hilfen import BEREIT, HINWEIS, lauf

PDF_A = {"name": "Anschreiben_Mustermann.pdf", "size": 1200000}
PDF_B = {"name": "Protokoll_Mustermann.pdf", "size": 800000}
ERFOLG_TEXT = "„Mustermann_Steuer2025“ wurde angelegt. Erkannt: Anschreiben ✓ · Steuerprotokoll ✓ · 2 Unterschriften Weiter: Angaben prüfen"


def uploads(schritt):
    return [r for r in schritt["zustand"]["requests"] if r["m"] == "POST" and r["p"] == "/api/v1/vorgaenge"]


def up(schritt):
    return schritt["zustand"]["berater"]["upload"]


@unittest.skipUnless(BEREIT, HINWEIS)
class TestUploadWeg(unittest.TestCase):
    def test_zwei_pdfs_werden_sofort_angelegt_und_die_liste_aktualisiert(self):
        o = lauf(seite="/vorgaenge", ops=[{"op": "dateien", "dateien": [PDF_A, PDF_B], "warte": 700}])
        s = o["schritte"][0]
        self.assertEqual([u["dateien"] for u in uploads(s)], [[PDF_A["name"], PDF_B["name"]]])             # beide Dateien, ein Aufruf
        e = up(s)["ergebnis"]
        self.assertEqual(e["text"], ERFOLG_TEXT)
        self.assertEqual(e["weiter"], "/pruefung?id=2")
        self.assertIsNone(up(s)["laeuft"])
        self.assertFalse(up(s)["feld_gesperrt"] or up(s)["eingabe_gesperrt"])                           # sofort wieder frei für den nächsten Vorgang
        self.assertEqual(up(s)["dateien"], [])
        self.assertEqual(s["zustand"]["berater"]["vorgang_karten"], 2)                                  # Liste zeigt den neuen Vorgang
        self.assertEqual(o["fehler_js"], [])

    def test_ablegen_wirkt_wie_auswaehlen_und_der_browser_oeffnet_die_datei_nicht(self):
        o = lauf(seite="/vorgaenge", ops=[{"op": "ablegen", "dateien": [PDF_A, PDF_B], "warte": 700}])
        s = o["schritte"][0]
        self.assertEqual(len(uploads(s)), 1)
        self.assertEqual(up(s)["ergebnis"]["weiter"], "/pruefung?id=2")
        self.assertTrue(up(s)["drop_verhindert"] and up(s)["drag_verhindert"])                          # sonst würde der Browser das PDF öffnen

    def test_eine_datei_wartet_auf_die_zweite_oder_den_knopf(self):
        o = lauf(seite="/vorgaenge", ops=[{"op": "dateien", "dateien": [PDF_A], "warte": 200}, {"op": "klick_id", "id": "btn-upload", "warte": 700}])
        erst = o["schritte"][0]
        self.assertEqual(uploads(erst), [])                                                              # noch nichts gesendet
        self.assertTrue(up(erst)["auswahlbox"])
        self.assertEqual(up(erst)["dateien"], ["Anschreiben_Mustermann.pdf · 1,1 MB"])
        self.assertIn("zweites PDF", up(erst)["hinweis"])
        self.assertEqual([u["dateien"] for u in uploads(o["schritte"][1])], [[PDF_A["name"]]])
        self.assertIn("wurde angelegt", up(o["schritte"][1])["ergebnis"]["text"])

    def test_zweite_datei_nachlegen_startet_dann_von_selbst(self):
        o = lauf(seite="/vorgaenge", ops=[{"op": "dateien", "dateien": [PDF_A], "warte": 200}, {"op": "dateien", "dateien": [PDF_B], "warte": 700}])
        self.assertEqual(uploads(o["schritte"][0]), [])
        self.assertEqual([u["dateien"] for u in uploads(o["schritte"][1])], [[PDF_A["name"], PDF_B["name"]]])

    def test_entfernen_und_abbrechen_senden_nichts(self):
        o = lauf(seite="/vorgaenge", ops=[{"op": "dateien", "dateien": [PDF_A], "warte": 200}, {"op": "klick_sel", "sel": "#upload-dateien button", "warte": 100},
                                          {"op": "dateien", "dateien": [PDF_A], "warte": 200}, {"op": "klick_id", "id": "btn-upload-leeren", "warte": 100}])
        self.assertEqual(up(o["schritte"][1])["dateien"], [])
        self.assertFalse(up(o["schritte"][1])["auswahlbox"])
        self.assertFalse(up(o["schritte"][3])["auswahlbox"])
        self.assertEqual([uploads(s) for s in o["schritte"]], [[], [], [], []])

    def test_dieselbe_datei_zweimal_zaehlt_einmal(self):
        o = lauf(seite="/vorgaenge", ops=[{"op": "dateien", "dateien": [PDF_A], "warte": 200}, {"op": "dateien", "dateien": [PDF_A], "warte": 200}])
        self.assertEqual(len(up(o["schritte"][1])["dateien"]), 1)
        self.assertEqual(uploads(o["schritte"][1]), [])

    def test_ohne_name_vom_server_wird_nichts_geraten(self):
        antwort = {"status": 201, "body": {"vorgang_id": 9, "status": "bereit", "personen": 1, "dokumente": [{"rolle": "A", "seiten": 2, "stellen": 1}]}}
        o = lauf(seite="/vorgaenge", api={"upload": antwort}, ops=[{"op": "dateien", "dateien": [PDF_B], "warte": 200}, {"op": "klick_id", "id": "btn-upload", "warte": 600}])
        self.assertEqual(up(o["schritte"][1])["ergebnis"]["text"], "Vorgang 9 wurde angelegt. Erkannt: Steuerprotokoll ✓ · 1 Unterschrift Weiter: Angaben prüfen")

    def test_dokumenttypen_richtig_benannt_typ_a_ist_das_protokoll_typ_b_das_anschreiben(self):
        for rollen, erwartet in ((["A"], "Erkannt: Steuerprotokoll ✓"), (["B"], "Erkannt: Anschreiben ✓"), (["A", "B"], "Erkannt: Anschreiben ✓ · Steuerprotokoll ✓"), (["B", "A"], "Erkannt: Anschreiben ✓ · Steuerprotokoll ✓")):
            antwort = {"status": 201, "body": {"vorgang_id": 4, "status": "bereit", "personen": 2, "name": "X", "dokumente": [{"rolle": r} for r in rollen]}}
            o = lauf(seite="/vorgaenge", api={"upload": antwort}, ops=[{"op": "dateien", "dateien": [PDF_A], "warte": 200}, {"op": "klick_id", "id": "btn-upload", "warte": 500}])
            self.assertIn(erwartet + " · 2 Unterschriften", up(o["schritte"][1])["ergebnis"]["text"], rollen)

    def test_abweichung_zwischen_den_dokumenten_wird_gesagt(self):
        antwort = {"status": 201, "body": {"vorgang_id": 3, "status": "bestaetigung_noetig", "personen": 2, "dokumente": [{"rolle": "A"}, {"rolle": "B"}], "name": "Muster"}}
        o = lauf(seite="/vorgaenge", api={"upload": antwort}, ops=[{"op": "dateien", "dateien": [PDF_A, PDF_B], "warte": 600}])
        self.assertIn("Anschreiben und Protokoll weichen an einer Stelle voneinander ab", up(o["schritte"][0])["ergebnis"]["text"])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestUploadPruefungImBrowser(unittest.TestCase):
    def fehler(self, dateien):
        o = lauf(seite="/vorgaenge", ops=[{"op": "dateien", "dateien": dateien, "warte": 300}])
        s = o["schritte"][0]
        self.assertEqual(uploads(s), [], "es darf nichts gesendet werden")
        self.assertEqual(up(s)["dateien"], [])
        return up(s)["fehler"]

    def test_nur_pdf(self):
        self.assertEqual(self.fehler([{"name": "Brief.docx", "size": 5000, "type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}]),
                         "„Brief.docx“ ist keine PDF-Datei. Bitte legen Sie nur PDF-Dateien ab.")
        self.assertEqual(self.fehler([{"name": "bild.png", "size": 5000, "type": "image/png"}]), "„bild.png“ ist keine PDF-Datei. Bitte legen Sie nur PDF-Dateien ab.")

    def test_pdf_ohne_mime_typ_wird_an_der_endung_erkannt(self):
        o = lauf(seite="/vorgaenge", ops=[{"op": "dateien", "dateien": [{"name": "scan.PDF", "size": 5000, "type": ""}], "warte": 300}])
        self.assertTrue(up(o["schritte"][0])["auswahlbox"])

    def test_zu_gross_und_leer(self):
        self.assertEqual(self.fehler([{"name": "riesig.pdf", "size": 15 * 1024 * 1024 + 1}]), "„riesig.pdf“ ist zu groß. Erlaubt sind höchstens 15 MB je Datei.")
        o = lauf(seite="/vorgaenge", ops=[{"op": "dateien", "dateien": [{"name": "genau.pdf", "size": 15 * 1024 * 1024}], "warte": 300}])
        self.assertTrue(up(o["schritte"][0])["auswahlbox"])                                              # 15 MB selbst sind erlaubt
        self.assertEqual(self.fehler([{"name": "leer.pdf", "size": 0}]), "„leer.pdf“ ist leer.")

    def test_mehr_als_zwei_dateien_werden_nicht_zugeordnet(self):
        text = "Pro Vorgang sind höchstens 2 PDF-Dateien möglich (Anschreiben und Steuerprotokoll). Bitte legen Sie die Dateien für weitere Vorgänge nacheinander ab."
        self.assertEqual(self.fehler([PDF_A, PDF_B, {"name": "Dritte.pdf", "size": 1000}]), text)
        o = lauf(seite="/vorgaenge", ops=[{"op": "dateien", "dateien": [PDF_A], "warte": 200}, {"op": "dateien", "dateien": [PDF_B, {"name": "Dritte.pdf", "size": 1000}], "warte": 300}])
        self.assertEqual(up(o["schritte"][1])["fehler"], text)
        self.assertEqual(up(o["schritte"][1])["dateien"], ["Anschreiben_Mustermann.pdf · 1,1 MB"])        # die erste bleibt, nichts wurde geraten
        self.assertEqual(uploads(o["schritte"][1]), [])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestUploadFehlerUndFortschritt(unittest.TestCase):
    def lauf_mit(self, antwort):
        o = lauf(seite="/vorgaenge", api={"upload": antwort}, ops=[{"op": "dateien", "dateien": [PDF_A, PDF_B], "warte": 700}])
        s = o["schritte"][0]
        self.assertEqual(len(uploads(s)), 1)
        self.assertIsNone(up(s)["ergebnis"])
        self.assertEqual(up(s)["dateien"], [])                                                           # Auswahl geleert, bereit für einen neuen Versuch
        self.assertFalse(up(s)["feld_gesperrt"])
        self.assertEqual(o["fehler_js"], [])
        return up(s)["fehler"]

    def test_servertext_steht_im_text(self):
        t = "Die PDF-Datei enthält keinen Text (vermutlich ein Scan). Bitte die Datei direkt aus der Steuersoft bzw. Textverarbeitung als PDF erzeugen."
        self.assertEqual(self.lauf_mit({"status": 422, "body": {"error": "keine_textebene", "meldung": t}}), t)

    def test_ohne_lesbaren_servertext_ein_verstaendlicher_satz(self):
        self.assertEqual(self.lauf_mit({"status": 413, "body": {"error": "zu_gross"}}), "Die Datei ist zu groß. Erlaubt sind höchstens 15 MB je Datei.")
        self.assertIn("sehr viele Vorgänge angelegt", self.lauf_mit({"status": 429, "body": {"error": "zu_viele_uploads"}}))
        self.assertIn("Anmeldung ist abgelaufen", self.lauf_mit({"status": 401, "body": {"error": "nicht_angemeldet"}}))
        for kaputt in ({"status": 500, "body": None}, {"status": 502, "body": {"x": 1}}):
            text = self.lauf_mit(kaputt)
            self.assertEqual(text, "Der Upload hat nicht geklappt. Bitte prüfen Sie Ihre Verbindung und versuchen Sie es noch einmal.")
            self.assertNotRegex(text, r"\d{3}|<")                                                          # keine Statusnummer, kein Markup

    # Netzfehler (Verbindung bricht ab): jsdom meldet das nicht zurück, geprüft wird es in Chromium (Probe test_e_upload_in_chromium)

    def test_fortschritt_und_sperre_waehrend_des_laufs(self):
        o = lauf(seite="/vorgaenge", api={"uploadVerzoegerung": 700}, ops=[{"op": "dateien", "dateien": [PDF_A, PDF_B], "warte": 200},
                                                                         {"op": "ablegen", "dateien": [{"name": "Noch.pdf", "size": 1000}], "warte": 100}, {"op": "klick_id", "id": "btn-upload", "warte": 900}])
        mitten = up(o["schritte"][0])
        self.assertTrue(mitten["feld_gesperrt"] and mitten["eingabe_gesperrt"])
        self.assertIsNotNone(mitten["laeuft"])
        self.assertRegex(mitten["laeuft"]["text"], r"^Wird hochgeladen … \d+ %$|^Die Dokumente werden geprüft")
        self.assertEqual(len(uploads(o["schritte"][1])) + len(uploads(o["schritte"][2])), 0)             # während des Laufs wird nichts zweites gesendet
        self.assertIn("wurde angelegt", up(o["schritte"][2])["ergebnis"]["text"])


if __name__ == "__main__":
    unittest.main()
