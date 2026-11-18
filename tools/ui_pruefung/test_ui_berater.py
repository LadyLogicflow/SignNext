"""jsdom-Prüfung der Berater-Seiten im Demo-Modus (v0.7.0): Banner, Sperrseite außerhalb des Heimnetzes, Beispiel-PDFs, Demo-Vorgang, Testmail an erlaubte Adresse,
Link und QR am Bildschirm beim Versenden. Die Schnittstelle spielt der Treiber (drive_sn.js) nach der Schnittstellenbeschreibung nach."""
import unittest
from pathlib import Path
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
            self.assertEqual(b["nur_heimnetz"], "Diese Seite ist nur im Heimnetz erreichbar.", seite)                          # die Sperre bleibt, nur ohne das Wort Demo
            self.assertEqual(b["ueberschrift"], "SignNext", seite)
            self.assertFalse(b["testversion_woerter"], seite)
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
class TestKeinTestversionAnschein(unittest.TestCase):
    """catrin 17.11.2026: alles, was den Anschein einer Testversion erweckt, entfällt (Demo-Karten, Testdaten-Balken, Demo-Namen). Echte Sperren und Fehler bleiben."""

    def test_keine_demo_karten_und_keine_demo_woerter_auf_den_berater_seiten(self):
        for seite in SEITEN:
            for api in ({"me": DEMO}, {}):
                o = lauf(seite=seite, api=api, ops=[])
                b = o["start"]["berater"]
                self.assertEqual(b["demo_elemente"], 0, (seite, api))                                   # weder Demo-Karte noch Testmail-Karte noch Balken im DOM
                self.assertFalse(b["testversion_woerter"], (seite, api))
                self.assertEqual(o["fehler_js"], [], (seite, api))

    def test_im_demo_ruft_die_seite_keine_demo_schnittstelle_mehr_auf(self):
        for seite in ("/vorgaenge", "/einstellungen"):
            o = lauf(seite=seite, api={"me": DEMO}, ops=[])
            self.assertEqual([p for _, p in api_aufrufe(o["start"]) if p.startswith("/api/v1/demo")], [], seite)

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

    def test_wortmarke_ist_die_produkt_wortmarke_signnext(self):
        # catrin 18.11.2026: im Navy-Band steht SignNext (nicht "BSt Next"): "Sign" + "Next" (Lime) + Chevron, Unterzeile bleibt
        for seite in ("/vorgaenge", "/pruefung?id=1", "/einstellungen", "/mail-zugang"):
            w = lauf(seite=seite, ops=[])["start"]["berater"]["wortmarke"]
            self.assertEqual(w["text"], "SignNext", seite)
            self.assertEqual(w["aria"], "SignNext", seite)
            self.assertEqual([(t[0], t[1], t[2]) for t in w["teile"][:2]], [("span", "wortmarke-sign", "Sign"), ("span", "wortmarke-next", "Next")], seite)
            self.assertEqual((w["chevron_pfad"], w["chevron_farbe"], w["chevron_versteckt"]), ("M4 4 L15 14 L4 24", "#A3E635", "true"), seite)

    def test_unterzeile_bleibt_und_bst_next_steht_nicht_mehr_im_kopf(self):
        ui = Path(__file__).resolve().parents[2] / "ui"
        for name in ("vorgaenge", "pruefung", "einstellungen", "mail-zugang"):
            html = (ui / (name + ".html")).read_text(encoding="utf-8")
            self.assertIn('<p class="band-tag">Unterschriften einholen &amp; nachweisen</p>', html, name)
            self.assertIn('id="wortmarke" class="wortmarke" role="img" aria-label="SignNext"', html, name)
            self.assertNotIn('aria-label="BSt Next"', html, name)

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
        self.assertIsNone(demo["angemeldet"])                                                  # ohne Anmeldung gibt es nichts zu zeigen, kein "Demo-Beratungsstelle"
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
