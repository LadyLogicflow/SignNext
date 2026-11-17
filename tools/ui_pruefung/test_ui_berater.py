"""jsdom-Prüfung der Berater-Seiten im Demo-Modus (v0.7.0): Banner, Sperrseite außerhalb des Heimnetzes, Beispiel-PDFs, Demo-Vorgang, Testmail an erlaubte Adresse,
Link und QR am Bildschirm beim Versenden. Die Schnittstelle spielt der Treiber (drive_sn.js) nach der Schnittstellenbeschreibung nach."""
import unittest
from urllib.parse import unquote

from ui_hilfen import BEREIT, HINWEIS, lauf

DEMO = {"status": 200, "body": {"email": "demo@example.org", "demo": True, "demo_banner": "DEMO – Testdaten, keine echten Personendaten"}}
QR_SVG = '<svg viewBox="0 0 10 10"><title>QR-Code</title><rect width="10" height="10" fill="#000"/></svg>'                      # wie das Backend: ohne Namensraum
QR_ALS_BILD = QR_SVG.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)                                               # so wird er als Bild gezeigt (sonst zeichnet ihn der Browser nicht)
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
        self.assertEqual([(a["href"], a["download"], a["text"]) for a in b["pdf_links"]],                       # die Liste kommt vom Server (GET /api/v1/demo)
                         [("/api/v1/demo/beispiel/anschreiben", True, "Anschreiben"), ("/api/v1/demo/beispiel/anschreiben-einzel", True, "Anschreiben Einzelperson"),
                          ("/api/v1/demo/beispiel/protokoll-ehepaar", True, "Protokoll Ehepaar"), ("/api/v1/demo/beispiel/protokoll-einzel", True, "Protokoll Einzelperson")])
        self.assertEqual(b["vorgang_karten"], 1)
        neu = o["schritte"][0]["zustand"]
        self.assertEqual([r for r in neu["requests"] if r["m"] == "POST"][0]["p"], "/api/v1/demo/vorgang")
        ergebnis = neu["berater"]["demo_ergebnis"]
        self.assertEqual((ergebnis["link"], ergebnis["link_nur_lesen"]), ("https://sn.example/u#demo8", True))
        self.assertEqual(ergebnis["qr"]["tag"], "IMG")                                             # Link auch als QR (qr_svg vom Server)
        self.assertEqual(unquote(ergebnis["qr"]["src"].split(",", 1)[1]), QR_ALS_BILD)
        self.assertIn("Demo-Vorgang 8 angelegt", ergebnis["text"])
        self.assertIn("Geburtsdatum 01.02.1960", ergebnis["text"])
        self.assertIn("Eingabe: 010260", ergebnis["text"])
        self.assertEqual(neu["berater"]["vorgang_karten"], 2)                                      # die Liste wurde neu geladen
        kopiert = o["schritte"][1]["zustand"]
        self.assertEqual(kopiert["zwischenablage"], ["https://sn.example/u#demo8"])
        self.assertEqual(kopiert["berater"]["demo_ergebnis"]["kopieren"], "Kopiert!")

    def test_nur_einfache_namen_werden_zu_beispiel_links(self):
        info = {"demo": True, "versand": "aus", "testadressen": [], "mail_zugang_geprueft": True, "beispiele": ["neues-beispiel", "../geheim", "x y", "<b>", "ok-2"]}
        o = lauf(seite="/vorgaenge", api={"me": DEMO, "demoInfo": info}, ops=[])
        self.assertEqual([(a["href"], a["text"]) for a in o["start"]["berater"]["pdf_links"]],
                         [("/api/v1/demo/beispiel/neues-beispiel", "neues-beispiel"), ("/api/v1/demo/beispiel/ok-2", "ok-2")])

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
        self.assertEqual(unquote(box["qr"]["src"].split(",", 1)[1]), QR_ALS_BILD)                      # nur als Bild, nie als Markup
        self.assertIn("Link wurde versendet!", o["schritte"][1]["zustand"]["alerts"])             # die bisherige Bestätigung bleibt

    def test_ohne_demo_feld_keine_box(self):
        o = lauf(seite="/pruefung?id=1", ops=self.VERSENDEN)
        self.assertIsNone(o["schritte"][1]["zustand"]["berater"]["versand_box"])

    def test_scheitert_die_mail_im_demo_wird_ohne_mail_fortfahren_angeboten(self):
        fehler = {"status": 502, "body": {"error": "mail_fehler", "meldung": "Die Mail konnte nicht gesendet werden.", "fallback": "ohne_mail"}}
        ohne = {"status": 200, "body": {"ok": True, "demo": {"link": "https://sn.example/u#ohne", "qr_svg": QR_SVG, "ohne_mail": True, "geburtsdatum_hinweis": "Geburtsdatum für den Link: 01.02.60"}}}
        o = lauf(seite="/pruefung?id=1", api={"me": DEMO, "versenden": fehler, "versendenOhneMail": ohne},
                 ops=self.VERSENDEN + [{"op": "klick_id", "id": "btn-ohne-mail", "warte": 600}])
        z = o["schritte"][1]["zustand"]["berater"]
        self.assertEqual(z["versand_fehler"], "Die Mail konnte nicht gesendet werden.")           # der Fehler wie bisher
        self.assertEqual(z["ohne_mail"], {"knopf_gesperrt": False})                                # und das Angebot "Ohne Mail fortfahren"
        self.assertIsNone(z["versand_box"])                                                        # noch kein Link, es wurde nichts freigegeben
        gesendet = [r for r in o["schritte"][1]["zustand"]["requests"] if r["p"].endswith("/versenden")]
        self.assertEqual(gesendet[0]["body"], {"bestaetigt": True})
        danach = o["schritte"][2]
        zweiter = [r for r in danach["zustand"]["requests"] if r["p"].endswith("/versenden")][0]
        self.assertEqual((zweiter["m"], zweiter["body"]), ("POST", {"bestaetigt": True, "ohne_mail": True}))     # erneut senden mit ohne_mail
        b = danach["zustand"]["berater"]
        self.assertEqual(b["versand_box"]["link"], "https://sn.example/u#ohne")
        self.assertIn("(ohne Mail)", b["versand_box"]["text"])
        self.assertEqual(unquote(b["versand_box"]["qr"]["src"].split(",", 1)[1]), QR_ALS_BILD)
        self.assertIsNone(b["ohne_mail"])                                                          # Angebot verschwindet
        self.assertIsNone(b["versand_fehler"])
        self.assertIn("Ohne Mail freigegeben", b["versand_hinweise"])
        self.assertTrue(b["versenden_gesperrt"])                                                   # nicht ein zweites Mal senden

    def test_anderer_fehler_im_demo_bietet_kein_ohne_mail_an(self):
        o = lauf(seite="/pruefung?id=1", api={"me": DEMO, "versenden": {"status": 409, "body": {"error": "versand_gesperrt", "meldung": "Der Versand ist gesperrt."}}}, ops=self.VERSENDEN)
        z = o["schritte"][1]["zustand"]["berater"]
        self.assertEqual(z["versand_fehler"], "Der Versand ist gesperrt.")
        self.assertIsNone(z["ohne_mail"])
        self.assertIsNone(z["versand_box"])

    def test_fehler_beim_ohne_mail_bleibt_im_text(self):
        fehler = {"status": 502, "body": {"error": "mail_fehler", "meldung": "Die Mail konnte nicht gesendet werden.", "fallback": "ohne_mail"}}
        o = lauf(seite="/pruefung?id=1", api={"me": DEMO, "versenden": fehler, "versendenOhneMail": {"status": 409, "body": {"error": "x", "meldung": "Ohne Mail nicht möglich."}}},
                 ops=self.VERSENDEN + [{"op": "klick_id", "id": "btn-ohne-mail", "warte": 500}])
        b = o["schritte"][2]["zustand"]["berater"]
        self.assertEqual(b["versand_fehler"], "Ohne Mail nicht möglich.")
        self.assertEqual(b["ohne_mail"], {"knopf_gesperrt": False})                                # man kann es erneut versuchen
        self.assertIsNone(b["versand_box"])
        self.assertEqual(o["schritte"][2]["zustand"]["alerts"], [])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestDemoMailZugang(unittest.TestCase):
    SPEICHERN = [{"op": "submit_id", "id": "form-mail", "warte": 600}]

    def test_hinweise_aus_dem_server_werden_nur_angezeigt(self):
        zugang = {"konfiguriert": True, "host": "smtp.example", "port": 587, "benutzer": "konto", "absender_name": "BSt", "absender_adresse": "noreply@example.org",
                  "hinweise": ["Der Benutzername weicht von der Absenderadresse ab.", "Bitte prüfen Sie die Domain."]}
        o = lauf(seite="/mail-zugang", api={"me": DEMO, "mailZugang": zugang}, ops=[])
        self.assertEqual(o["start"]["berater"]["mail_hinweise"], ["Der Benutzername weicht von der Absenderadresse ab.", "Bitte prüfen Sie die Domain."])
        ohne = lauf(seite="/mail-zugang", ops=[])
        self.assertIsNone(ohne["start"]["berater"]["mail_hinweise"])                               # ohne hinweise: nichts

    def test_422_mit_feld_steht_am_feld(self):
        fehler = {"status": 422, "body": {"error": "eingabe_ungueltig", "feld": "absender_adresse", "meldung": "Die Absenderadresse muss zur Domain des Benutzers passen."}}
        o = lauf(seite="/mail-zugang", api={"me": DEMO, "mailPut": fehler}, ops=self.SPEICHERN)
        z = o["schritte"][0]["zustand"]
        self.assertEqual([r["m"] for r in z["requests"] if r["p"].endswith("/mail-zugang")], ["PUT"])
        self.assertEqual(z["berater"]["absender_fehler"], "Die Absenderadresse muss zur Domain des Benutzers passen.")      # genau am Feld
        self.assertIsNone(z["berater"]["speichern_fehler"])

    def test_fehler_ohne_feld_bleibt_im_formularfehler(self):
        fehler = {"status": 503, "body": {"error": "nicht_bereit", "meldung": "Der Dienst ist nicht vollständig eingerichtet."}}
        o = lauf(seite="/mail-zugang", api={"me": DEMO, "mailPut": fehler}, ops=self.SPEICHERN)
        b = o["schritte"][0]["zustand"]["berater"]
        self.assertEqual(b["speichern_fehler"], "Der Dienst ist nicht vollständig eingerichtet.")
        self.assertIsNone(b["absender_fehler"])


if __name__ == "__main__":
    unittest.main()
