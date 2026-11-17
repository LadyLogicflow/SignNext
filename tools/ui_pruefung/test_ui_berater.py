"""jsdom-Prüfung der Berater-Seiten im Demo-Modus (v0.7.0): Banner, Sperrseite außerhalb des Heimnetzes, Beispiel-PDFs, Demo-Vorgang, Testmail an erlaubte Adresse,
Link und QR am Bildschirm beim Versenden. Die Schnittstelle spielt der Treiber (drive_sn.js) nach der Schnittstellenbeschreibung nach."""
import unittest
from urllib.parse import unquote

from ui_hilfen import BEREIT, HINWEIS, lauf

DEMO = {"status": 200, "body": {"email": "demo@example.org", "demo": True, "demo_banner": "DEMO – Testdaten, keine echten Personendaten"}}
QR_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#000"/></svg>'
SEITEN = ["/vorgaenge", "/einstellungen", "/pruefung?id=1", "/mail-zugang"]


def api_aufrufe(zustand):
    return [(r["m"], r["p"]) for r in zustand["requests"] if r["p"].startswith("/api/")]


@unittest.skipUnless(BEREIT, HINWEIS)
class TestDemoRahmen(unittest.TestCase):
    def test_banner_auf_jeder_berater_seite_nur_im_demo(self):
        for seite in SEITEN:
            demo = lauf(seite=seite, api={"me": DEMO}, ops=[])
            self.assertEqual(demo["start"]["berater"]["banner"], "DEMO – Testdaten, keine echten Personendaten", seite)
            self.assertEqual(demo["start"]["berater"]["banner_rolle"], "status", seite)
            normal = lauf(seite=seite, ops=[])
            self.assertIsNone(normal["start"]["berater"]["banner"], seite)                     # Produktion: kein Banner
        ohne_text = lauf(seite="/vorgaenge", api={"me": {"status": 200, "body": {"demo": True}}}, ops=[])
        self.assertEqual(ohne_text["start"]["berater"]["banner"], "DEMO – Testdaten, keine echten Personendaten")           # Standardtext, falls der Server keinen liefert

    def test_nicht_erlaubte_demo_zeigt_eigene_seite_und_laedt_nichts(self):
        sperre = {"status": 403, "body": {"error": "forbidden", "grund": "demo_nicht_erlaubt"}}
        for seite in SEITEN:
            o = lauf(seite=seite, api={"me": sperre}, ops=[])
            b = o["start"]["berater"]
            self.assertEqual(b["nur_heimnetz"], "Diese Demo ist nur im Heimnetz erreichbar.", seite)
            self.assertIsNone(b["banner"], seite)
            self.assertEqual(o["start"]["pfad"], seite.split("?")[0], seite)                      # keine Weiterleitung zum Portal
            self.assertEqual(api_aufrufe(o["start"]), [("GET", "/api/v1/me")], seite)             # die Seite lädt sonst nichts
            self.assertEqual(o["start"]["alerts"], [], seite)
            self.assertEqual(o["fehler_js"], [], seite)

    def test_anderes_403_oder_fehler_beim_me_laesst_die_seite_normal_arbeiten(self):
        o = lauf(seite="/vorgaenge", api={"me": {"status": 403, "body": {"error": "forbidden"}}}, ops=[])
        self.assertIsNone(o["start"]["berater"]["nur_heimnetz"])                                   # Anmeldung im Portal ist Sache des Servers, nicht dieser Seite
        self.assertIn(("GET", "/api/v1/vorgaenge"), api_aufrufe(o["start"]))


@unittest.skipUnless(BEREIT, HINWEIS)
class TestDemoVorgaenge(unittest.TestCase):
    def test_beispiel_pdfs_und_demo_vorgang_mit_link_und_code(self):
        o = lauf(seite="/vorgaenge", api={"me": DEMO}, ops=[{"op": "klick_id", "id": "btn-demo-vorgang", "warte": 700}, {"op": "klick_sel", "sel": "#demo-ergebnis .link-kopieren", "warte": 200}])
        b = o["start"]["berater"]
        self.assertTrue(b["demo_bereich"])
        self.assertEqual([(a["href"], a["download"]) for a in b["pdf_links"]],
                         [("/api/v1/demo/beispiel/anschreiben", True), ("/api/v1/demo/beispiel/protokoll-ehepaar", True), ("/api/v1/demo/beispiel/protokoll-einzel", True)])
        self.assertEqual(b["vorgang_karten"], 1)
        neu = o["schritte"][0]["zustand"]
        self.assertEqual([r for r in neu["requests"] if r["m"] == "POST"][0]["p"], "/api/v1/demo/vorgang")
        ergebnis = neu["berater"]["demo_ergebnis"]
        self.assertEqual((ergebnis["link"], ergebnis["link_nur_lesen"]), ("https://sn.example/u#demo8", True))
        self.assertIn("Demo-Vorgang 8 angelegt", ergebnis["text"])
        self.assertIn("Geburtsdatum 01.02.1960", ergebnis["text"])
        self.assertIn("Eingabe: 010260", ergebnis["text"])
        self.assertEqual(neu["berater"]["vorgang_karten"], 2)                                      # die Liste wurde neu geladen
        kopiert = o["schritte"][1]["zustand"]
        self.assertEqual(kopiert["zwischenablage"], ["https://sn.example/u#demo8"])
        self.assertEqual(kopiert["berater"]["demo_ergebnis"]["kopieren"], "Kopiert!")

    def test_ohne_demo_gibt_es_den_bereich_nicht(self):
        o = lauf(seite="/vorgaenge", ops=[])
        self.assertFalse(o["start"]["berater"]["demo_bereich"])
        self.assertNotIn(("GET", "/api/v1/demo"), api_aufrufe(o["start"]))


@unittest.skipUnless(BEREIT, HINWEIS)
class TestDemoTestmail(unittest.TestCase):
    def test_testmail_geht_nur_an_eine_erlaubte_adresse(self):
        o = lauf(seite="/einstellungen", api={"me": DEMO, "demoInfo": {"demo": True, "versand": "testadressen", "testadressen": ["a@example.org", "b@example.org"], "mail_zugang_geprueft": False}},
                 ops=[{"op": "auswahl_id", "id": "demo-testadresse", "wert": "b@example.org", "warte": 100}, {"op": "klick_id", "id": "btn-demo-testmail", "warte": 500}])
        t = o["start"]["berater"]["testmail"]
        self.assertTrue(t["sichtbar"])
        self.assertEqual(t["optionen"], ["a@example.org", "b@example.org"])
        self.assertIn("nur an die erlaubten Testadressen", t["info"])
        self.assertIn("Mail-Zugang wurde noch nicht erfolgreich geprüft", t["info"])
        self.assertFalse(t["knopf_gesperrt"])
        gesendet = o["schritte"][1]["zustand"]
        post = [r for r in gesendet["requests"] if r["m"] == "POST"][0]
        self.assertEqual((post["p"], post["body"]), ("/api/v1/demo/testmail", {"an": "b@example.org"}))                      # genau die gewählte Adresse
        self.assertEqual(gesendet["berater"]["testmail"]["erfolg"], "Die Testmail wurde an b@example.org gesendet.")
        self.assertIsNone(gesendet["berater"]["testmail"]["fehler"])

    def test_fehler_der_pruefung_steht_im_text_und_ohne_adresse_kein_knopf(self):
        fehler = lauf(seite="/einstellungen", api={"me": DEMO, "testmailFehler": {"status": 502, "error": "mail_fehler", "meldung": "Der Mailserver hat die Testmail abgelehnt."}},
                      ops=[{"op": "klick_id", "id": "btn-demo-testmail", "warte": 500}])
        t = fehler["schritte"][0]["zustand"]["berater"]["testmail"]
        self.assertEqual(t["fehler"], "Der Mailserver hat die Testmail abgelehnt.")
        self.assertIsNone(t["erfolg"])
        self.assertFalse(t["knopf_gesperrt"])
        self.assertEqual(fehler["schritte"][0]["zustand"]["alerts"], [])
        leer = lauf(seite="/einstellungen", api={"me": DEMO, "demoInfo": {"demo": True, "versand": "aus", "testadressen": [], "mail_zugang_geprueft": True}}, ops=[])
        b = leer["start"]["berater"]["testmail"]
        self.assertTrue(b["knopf_gesperrt"])
        self.assertIn("keine Testadresse erlaubt", b["info"])
        self.assertIn("ausgeschaltet", b["info"])

    def test_ohne_demo_keine_testmail_funktion(self):
        o = lauf(seite="/einstellungen", ops=[])
        self.assertFalse(o["start"]["berater"]["testmail"]["sichtbar"])
        self.assertNotIn(("GET", "/api/v1/demo"), api_aufrufe(o["start"]))


@unittest.skipUnless(BEREIT, HINWEIS)
class TestDemoVersenden(unittest.TestCase):
    VERSENDEN = [{"op": "haken_id", "id": "versand-bestaetigung", "warte": 50}, {"op": "klick_id", "id": "btn-versenden", "warte": 600}]

    def test_im_demo_stehen_link_und_qr_am_bildschirm(self):
        demo = {"link": "https://sn.example/u#abc", "qr_svg": QR_SVG, "geburtsdatum_hinweis": "Geburtsdatum für den Link: 01.02.60"}
        o = lauf(seite="/pruefung?id=1", api={"me": DEMO, "versenden": {"status": 200, "body": {"ok": True, "demo": demo}}}, ops=self.VERSENDEN)
        box = o["schritte"][1]["zustand"]["berater"]["versand_box"]
        self.assertEqual((box["link"], box["link_nur_lesen"]), ("https://sn.example/u#abc", True))
        self.assertIn("Geburtsdatum für den Link: 01.02.60", box["text"])
        self.assertEqual(box["qr"]["tag"], "IMG")
        self.assertEqual(unquote(box["qr"]["src"].split(",", 1)[1]), QR_SVG)                      # nur als Bild, nie als Markup
        self.assertIn("Link wurde versendet!", o["schritte"][1]["zustand"]["alerts"])             # die bisherige Bestätigung bleibt

    def test_ohne_demo_feld_keine_box(self):
        o = lauf(seite="/pruefung?id=1", ops=self.VERSENDEN)
        self.assertIsNone(o["schritte"][1]["zustand"]["berater"]["versand_box"])

    def test_scheitert_die_mail_im_demo_bleibt_der_fehler_und_der_link_steht_trotzdem_da(self):
        fehler = {"status": 502, "body": {"error": "mail_fehler", "meldung": "Die Mail konnte nicht gesendet werden.", "fallback": {"link": "https://sn.example/u#fallback", "qr_svg": QR_SVG}}}
        o = lauf(seite="/pruefung?id=1", api={"me": DEMO, "versenden": fehler}, ops=self.VERSENDEN)
        z = o["schritte"][1]["zustand"]["berater"]
        self.assertEqual(z["versand_fehler"], "Die Mail konnte nicht gesendet werden.")           # der Fehler wie bisher
        self.assertEqual(z["versand_box"]["link"], "https://sn.example/u#fallback")
        self.assertEqual(z["versand_box"]["qr"]["tag"], "IMG")
        ohne = lauf(seite="/pruefung?id=1", api={"me": DEMO, "versenden": {"status": 502, "body": {"error": "mail_fehler", "meldung": "Die Mail konnte nicht gesendet werden."}}}, ops=self.VERSENDEN)
        self.assertIsNone(ohne["schritte"][1]["zustand"]["berater"]["versand_box"])                # ohne fallback keine Box


if __name__ == "__main__":
    unittest.main()
