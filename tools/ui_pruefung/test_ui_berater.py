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
    def test_kein_testdaten_balken_auf_keiner_seite(self):
        # catrin 17.11.2026: der Balken "DEMO - Testdaten ..." entfällt, auch wenn der Server den Demo-Marker und den Text weiter mitschickt
        for seite in SEITEN + ["/u"]:
            for api in ({"me": DEMO}, {}, {"me": {"status": 200, "body": {"demo": True}}}):
                b = lauf(seite=seite, fragment="#tok" if seite == "/u" else "", api=api, ops=[])["start"]["berater"]
                self.assertIsNone(b["banner"], (seite, api))
                self.assertFalse(b["banner_in_leiste"], (seite, api))
                self.assertFalse(b["testdaten_im_text"], (seite, api))                                 # auch nirgends sonst im Seitentext

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


@unittest.skipUnless(BEREIT, HINWEIS)
class TestRahmen(unittest.TestCase):
    """Gemeinsamer Rahmen aller Berater-Seiten (Konzept 17.11.2026): Wortmarke, Angemeldet-als, Menü, Hinweisleiste."""
    MENUE = [("Vorgänge", "/vorgaenge"), ("Mail-Zugang", "/mail-zugang"), ("Einstellungen", "/einstellungen")]

    def test_menue_ist_auf_jeder_seite_gleich_und_markiert_die_aktuelle(self):
        erwartet = {"/vorgaenge": "/vorgaenge", "/pruefung?id=7": "/vorgaenge", "/mail-zugang": "/mail-zugang", "/einstellungen": "/einstellungen"}
        for seite, aktiv in erwartet.items():
            o = lauf(seite=seite, ops=[])
            menue = o["start"]["berater"]["menue"]
            self.assertEqual([(m["text"], m["href"]) for m in menue], self.MENUE, seite)
            self.assertEqual([m["href"] for m in menue if m["aktuell"]], [aktiv], seite)

    def test_wortmarke_ist_die_offizielle_und_zweifarbig(self):
        w = lauf(seite="/vorgaenge", ops=[])["start"]["berater"]["wortmarke"]
        self.assertEqual(w["titel"], "BSt Next")
        self.assertEqual([f for f in w["farben"] if f != "none"], ["#A3E635", "#FFFFFF"])                                  # "BSt" weiß, "Next" und Chevron Lime - nie einfarbig

    def test_angemeldet_als_und_portal_link(self):
        o = lauf(seite="/vorgaenge", api={"me": {"status": 200, "body": {"email": "berater@example.org", "rolle": "admin", "demo": False}}}, ops=[])["start"]["berater"]
        self.assertEqual(o["angemeldet"], "Angemeldet als: berater@example.org (Administrator)")
        self.assertIsNone(o["portal_link"])                                                    # ohne Adresse vom Server kein Link
        mit = lauf(seite="/vorgaenge", api={"me": {"status": 200, "body": {"email": "b@example.org", "portal_url": "https://portal.example/"}}}, ops=[])["start"]["berater"]
        self.assertEqual(mit["portal_link"], "https://portal.example/")
        boese = lauf(seite="/vorgaenge", api={"me": {"status": 200, "body": {"email": "b@example.org", "portal_url": "javascript:alert(1)"}}}, ops=[])["start"]["berater"]
        self.assertIsNone(boese["portal_link"])                                                # nur https-Adressen
        html = lauf(seite="/vorgaenge", api={"me": {"status": 200, "body": {"email": "<b>x</b>@example.org"}}}, ops=[])["start"]["berater"]
        self.assertEqual(html["angemeldet"], "Angemeldet als: <b>x</b>@example.org")           # als Text, nicht als Markup
        demo = lauf(seite="/vorgaenge", api={"me": DEMO}, ops=[])["start"]["berater"]
        self.assertEqual(demo["angemeldet"], "Angemeldet als: Demo-Beratungsstelle")
        self.assertIsNone(demo["banner"])                                                      # kein Demo-Balken mehr (catrin 17.11.)

    def test_hinweisleiste_nur_wenn_etwas_fehlt(self):
        ok = lauf(seite="/vorgaenge", ops=[])["start"]["berater"]["hinweise"]
        self.assertEqual(ok, [])
        kein_mail = lauf(seite="/vorgaenge", api={"mailZugang": {"konfiguriert": False}}, ops=[])["start"]["berater"]["hinweise"]
        self.assertEqual(kein_mail, [{"text": "Ihr Mail-Zugang fehlt noch. Ohne ihn können keine E-Mails an Mitglieder gesendet werden.", "knopf": "Mail-Zugang einrichten", "href": "/mail-zugang"}])
        ungeprueft = lauf(seite="/vorgaenge", api={"mailZugang": {"konfiguriert": True, "geprueft_am": None}}, ops=[])["start"]["berater"]["hinweise"]
        self.assertEqual([(h["knopf"], h["href"]) for h in ungeprueft], [("Testmail senden", "/mail-zugang")])
        keine_angaben = lauf(seite="/vorgaenge", api={"einstellungen": {"gesetzt": False}}, ops=[])["start"]["berater"]["hinweise"]
        self.assertEqual([(h["knopf"], h["href"]) for h in keine_angaben], [("Angaben ergänzen", "/einstellungen")])
        beides = lauf(seite="/vorgaenge", api={"mailZugang": {"konfiguriert": False}, "einstellungen": {"gesetzt": False}}, ops=[])["start"]["berater"]["hinweise"]
        self.assertEqual([h["href"] for h in beides], ["/mail-zugang", "/einstellungen"])

    def test_auf_der_eigenen_seite_kein_hinweis_darauf(self):
        fehlt = {"mailZugang": {"konfiguriert": False}, "einstellungen": {"gesetzt": False}}
        mail = lauf(seite="/mail-zugang", api=fehlt, ops=[])["start"]["berater"]["hinweise"]
        self.assertEqual([h["href"] for h in mail], ["/einstellungen"])                        # auf der Mail-Zugang-Seite nur der andere Hinweis
        einst = lauf(seite="/einstellungen", api=fehlt, ops=[])["start"]["berater"]["hinweise"]
        self.assertEqual([h["href"] for h in einst], ["/mail-zugang"])

    def test_fehler_beim_laden_der_hinweise_stoert_die_seite_nicht(self):
        o = lauf(seite="/vorgaenge", api={"hinweisFehler": True}, ops=[])
        self.assertEqual(o["start"]["berater"]["hinweise"], [])
        self.assertEqual(o["fehler_js"], [])
        self.assertEqual(o["start"]["berater"]["ueberschrift"], "Unterschriften einholen")
