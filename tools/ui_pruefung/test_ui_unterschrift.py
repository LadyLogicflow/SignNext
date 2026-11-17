"""jsdom-Prüfung der SignNext-Unterschriftsseite (/u) und der Handy-Seite (/h): Zeichenfeld, Personen, Meldungen im Text, Handy-Übergabe per QR.
Die Schnittstelle wird vom Treiber (drive_sn.js) nach der Schnittstellenbeschreibung nachgespielt (siehe README.md in diesem Ordner)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent
DRIVE = HIER / "drive_sn.js"
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
QR_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#000"/></svg>'
SATZ = "Sie können am Computer mit der Maus oder am Smartphone oder Tablet mit dem Finger unterschreiben."
STRICH = [[10, 10], [50, 30], [90, 10]]
BEREIT = (shutil.which("node") is not None) and (HIER / "node_modules" / "jsdom").is_dir()


def lauf(**kw):
    p = subprocess.run(["node", str(DRIVE), json.dumps(kw)], capture_output=True, text=True, timeout=120, cwd=str(HIER))
    zeilen = [z for z in p.stdout.splitlines() if z.startswith("RESULT ")]
    if not zeilen:
        raise AssertionError(p.stdout[-400:] + p.stderr[-400:])
    o = json.loads(zeilen[-1][7:])
    if not o["ok"]:
        raise AssertionError(o["fehler"])
    return o


def bis_zum_feld(*weiter):
    """Geburtsdatum, alle Seiten angezeigt, 'Vollständig gelesen': danach steht der Schritt mit dem Zeichenfeld."""
    return [{"op": "code", "wert": "01.02.60", "warte": 700}, {"op": "seiten_geladen", "warte": 100}, {"op": "gelesen", "warte": 600}, *weiter]


def posts(schritt, pfad_ende):
    return [r for r in schritt["zustand"]["requests"] if r["m"] == "POST" and r["p"].endswith(pfad_ende)]


@unittest.skipUnless(BEREIT, "braucht Node und jsdom (siehe README.md): cd tools/ui_pruefung && npm install jsdom@24 --no-save")
class TestZeichenfeld(unittest.TestCase):
    def test_zeichenfeld_verhaelt_sich_wie_in_booknext(self):
        o = lauf(seite="/u", fragment="#tok", ops=bis_zum_feld(
            {"op": "tippen", "x": 40, "y": 40}, {"op": "haken"}, {"op": "unterschreiben"},
            {"op": "zeichne", "punkte": STRICH}, {"op": "loeschen"}, {"op": "zeichne", "punkte": STRICH}, {"op": "unterschreiben", "warte": 600}))
        feld = o["schritte"][2]["zustand"]
        self.assertEqual(feld["schritt"], ["step-unterschrift"])
        self.assertEqual(feld["fehlt"], "Bitte hier unterschreiben.")                           # roter Satz, solange nichts gezeichnet ist
        self.assertEqual(feld["hilfe"], "Mit Maus, Stift oder Finger unterschreiben.")
        self.assertEqual(feld["hinweis_oben"], SATZ)                                            # Satz von catrin (#19322) bleibt vor dem Feld
        self.assertEqual(feld["knoepfe"]["loeschen"]["text"], "löschen")
        self.assertEqual(feld["label"], "Unterschrift")
        tippen = o["schritte"][3]["zustand"]
        self.assertEqual(tippen["zeichnung"]["striche"], 0)                                     # leeres Antippen zählt nicht
        self.assertEqual(tippen["fehlt"], "Bitte hier unterschreiben.")
        ohne = o["schritte"][5]["zustand"]
        self.assertEqual(ohne["fehler_unterschrift"], "Bitte unterschreiben Sie im Feld")
        self.assertEqual(posts(o["schritte"][5], "/zugang/unterschrift"), [])                  # nichts an den Server
        gezeichnet = o["schritte"][6]["zustand"]
        self.assertIsNone(gezeichnet["fehlt"])                                                  # Hinweis weg, sobald ein Strich gezogen ist
        self.assertEqual(gezeichnet["zeichnung"]["striche"], 2)
        self.assertEqual(gezeichnet["zeichnung"]["stil"], {"breite": 2, "farbe": "#0b2545", "cap": "round", "join": "round"})
        self.assertGreaterEqual(gezeichnet["zeichnung"]["capture"], 1)                          # Pointer Events mit Capture
        self.assertIsNone(gezeichnet["fehler_unterschrift"])
        geleert = o["schritte"][7]["zustand"]
        self.assertEqual(geleert["fehlt"], "Bitte hier unterschreiben.")                        # löschen: Hinweis wieder da
        self.assertGreaterEqual(geleert["zeichnung"]["geleert"], 1)
        senden = posts(o["schritte"][9], "/zugang/unterschrift")[0]
        self.assertEqual(senden["body"], {"person": 1, "bild": PNG, "ich_unterschreibe": True, "haken": True, "text_hash": "h1"})
        self.assertEqual(o["schritte"][9]["zustand"]["schritt"], ["step-fertig"])
        for s in o["schritte"]:
            self.assertEqual(s["zustand"]["alerts"], [], s["op"])                               # nie ein Dialog

    def test_zwei_personen_sichtbar_und_ohne_alert(self):
        o = lauf(seite="/u", fragment="#tok", api={"personen": 2}, ops=bis_zum_feld(
            {"op": "haken"}, {"op": "zeichne", "punkte": STRICH}, {"op": "unterschreiben", "warte": 800},
            {"op": "haken"}, {"op": "zeichne", "punkte": STRICH}, {"op": "unterschreiben", "warte": 600}))
        self.assertEqual(o["schritte"][2]["zustand"]["label"], "Unterschrift Person 1 von 2")
        wechsel = o["schritte"][5]["zustand"]
        self.assertEqual(wechsel["label"], "Unterschrift Person 2 von 2")
        self.assertEqual(wechsel["person_info"], "Person 1 hat unterschrieben. Jetzt ist Person 2 an der Reihe.")
        self.assertEqual(wechsel["fehlt"], "Bitte hier unterschreiben.")                        # Feld ist leer
        self.assertFalse(wechsel["haken_an"])                                                   # Haken zurückgesetzt
        self.assertEqual(posts(o["schritte"][5], "/zugang/unterschrift")[0]["body"]["person"], 1)
        zweite = posts(o["schritte"][8], "/zugang/unterschrift")[0]
        self.assertEqual(zweite["body"]["person"], 2)
        self.assertEqual(o["schritte"][8]["zustand"]["schritt"], ["step-fertig"])
        for s in o["schritte"]:
            self.assertEqual(s["zustand"]["alerts"], [], s["op"])

    def test_meldungen_stehen_im_text_statt_in_dialogen(self):
        abgelaufen = lauf(seite="/u", fragment="#tok", api={"sitzungFehlt": "/api/v1/zugang/texte"}, ops=bis_zum_feld())
        z = abgelaufen["schritte"][2]["zustand"]
        self.assertIn("Sitzung ist abgelaufen", z["meldung"])
        self.assertTrue(z["neu_laden"])                                                         # Knopf "Seite neu laden" statt automatischem Neuladen
        self.assertEqual(z["alerts"], [])
        seiten = lauf(seite="/u", fragment="#tok", api={"seitenFehler": True}, ops=[{"op": "code", "wert": "01.02.60", "warte": 700}])
        self.assertTrue(seiten["schritte"][0]["zustand"]["meldung"].startswith("Fehler beim Laden der Seiten"))
        self.assertEqual(seiten["schritte"][0]["zustand"]["alerts"], [])
        server = lauf(seite="/u", fragment="#tok", api={"unterschriftFehler": {"status": 422, "error": "bild_leer", "meldung": "Das Bild ist leer."}},
                      ops=bis_zum_feld({"op": "haken"}, {"op": "zeichne", "punkte": STRICH}, {"op": "unterschreiben", "warte": 500}))
        s = server["schritte"][5]["zustand"]
        self.assertEqual(s["fehler_unterschrift"], "Das Bild ist leer.")
        self.assertEqual(s["alerts"], [])
        self.assertFalse(s["knoepfe"]["unterschreiben"]["gesperrt"])                            # nach dem Fehler wieder bedienbar
        code = lauf(seite="/u", fragment="#tok", ops=[{"op": "code", "wert": "01.01.01", "warte": 500}])
        self.assertEqual(code["schritte"][0]["zustand"]["fehler_code"], "Das Geburtsdatum stimmt nicht")


@unittest.skipUnless(BEREIT, "braucht Node und jsdom (siehe README.md)")
class TestHandyUebergabe(unittest.TestCase):
    def test_qr_wird_als_bild_gezeigt_und_wartet(self):
        o = lauf(seite="/u", fragment="#tok", api={"handoffStatus": ["wartet"]}, ops=bis_zum_feld({"op": "handy", "warte": 300}))
        s = o["schritte"][3]
        self.assertEqual(posts(s, "/zugang/handoff")[0]["body"], {"person": 1})
        z = s["zustand"]
        self.assertTrue(z["handy_box"])
        self.assertFalse(z["zeichenfeld_sichtbar"])
        self.assertEqual((z["qr"]["tag"], z["qr"]["sichtbar"]), ("IMG", True))
        self.assertTrue(z["qr"]["src"].startswith("data:image/svg+xml;charset=utf-8,"))
        from urllib.parse import unquote
        self.assertEqual(unquote(z["qr"]["src"].split(",", 1)[1]), QR_SVG)                      # das SVG kommt unverändert als Bild
        self.assertIn("QR-Code", z["handy_status"])
        self.assertRegex(z["handy_rest"], r"^Noch \d+:\d\d Minuten gültig\.$")
        self.assertEqual(z["alerts"], [])

    def test_verbunden_wird_angezeigt(self):
        o = lauf(seite="/u", fragment="#tok", api={"handoffStatus": ["verbunden"]}, ops=bis_zum_feld({"op": "handy", "warte": 300}))
        self.assertEqual(o["schritte"][3]["zustand"]["handy_status"], "Handy verbunden. Bitte dort unterschreiben.")

    def test_gezeichnetes_bild_wird_einmal_abgeholt_und_wie_gewohnt_gesendet(self):
        o = lauf(seite="/u", fragment="#tok", api={"handoffStatus": ["wartet", "verbunden", "gezeichnet"]},
                 ops=bis_zum_feld({"op": "handy", "warte": 900}, {"op": "warte", "warte": 400}, {"op": "haken"}, {"op": "unterschreiben", "warte": 600}))
        fertig = o["schritte"][3]["zustand"]
        abholen = [r for r in o["schritte"][3]["zustand"]["requests"] if r["p"].endswith("/abholen")]
        self.assertEqual(len(abholen), 1)                                                       # genau einmal
        self.assertFalse(fertig["handy_box"])
        self.assertTrue(fertig["zeichenfeld_sichtbar"])
        self.assertEqual(fertig["zeichnung"]["bilder"], 1)                                      # Bild liegt im Zeichenfeld (Vorschau)
        self.assertIsNone(fertig["fehlt"])                                                      # zählt wie eine gezeichnete Unterschrift
        self.assertIn("übernommen", fertig["meldung"])
        self.assertEqual(fertig["meldung_art"], "info-box")
        self.assertEqual([r for r in o["schritte"][4]["zustand"]["requests"] if r["p"].startswith("/api/v1/zugang/handoff/")], [])   # keine Abfragen mehr
        senden = posts(o["schritte"][6], "/zugang/unterschrift")[0]
        self.assertEqual(senden["body"], {"person": 1, "bild": PNG, "ich_unterschreibe": True, "haken": True, "text_hash": "h1"})
        self.assertEqual(o["schritte"][6]["zustand"]["schritt"], ["step-fertig"])

    def test_abbrechen_loescht_am_server_und_zeigt_wieder_das_feld(self):
        o = lauf(seite="/u", fragment="#tok", api={"handoffStatus": ["wartet"]}, ops=bis_zum_feld({"op": "handy", "warte": 200}, {"op": "handy_abbrechen", "warte": 300}, {"op": "warte", "warte": 200}))
        ab = o["schritte"][4]
        self.assertTrue(any(r["m"] == "DELETE" and r["p"] == "/api/v1/zugang/handoff/hid1" for r in ab["zustand"]["requests"]))
        self.assertFalse(ab["zustand"]["handy_box"])
        self.assertTrue(ab["zustand"]["zeichenfeld_sichtbar"])
        self.assertEqual([r for r in o["schritte"][5]["zustand"]["requests"] if r["m"] == "GET"], [])           # Abfragen enden

    def test_abgelaufen_bietet_neuen_qr_code_an(self):
        o = lauf(seite="/u", fragment="#tok", api={"handoffStatus": ["abgelaufen"]}, ops=bis_zum_feld({"op": "handy", "warte": 400}, {"op": "handy_neu", "warte": 400}))
        z = o["schritte"][3]["zustand"]
        self.assertIn("abgelaufen", z["handy_status"])
        self.assertFalse(z["qr"]["sichtbar"])
        self.assertTrue(z["knoepfe"]["handy_neu"]["sichtbar"])
        neu = o["schritte"][4]
        self.assertEqual([r["p"] for r in neu["zustand"]["requests"] if r["m"] == "POST" and r["p"].endswith("/handoff")], ["/api/v1/zugang/handoff"])

    def test_fehler_vom_server_stehen_im_text(self):
        start = lauf(seite="/u", fragment="#tok", api={"handoffStartFehler": {"status": 409, "error": "schritt_falsch", "meldung": "Das geht nur im Schritt Unterschreiben."}},
                     ops=bis_zum_feld({"op": "handy", "warte": 300}))
        z = start["schritte"][3]["zustand"]
        self.assertEqual(z["meldung"], "Das geht nur im Schritt Unterschreiben.")
        self.assertFalse(z["handy_box"])
        self.assertTrue(z["zeichenfeld_sichtbar"])
        self.assertEqual(z["alerts"], [])
        abholen = lauf(seite="/u", fragment="#tok", api={"handoffStatus": ["gezeichnet"], "handoffAbholFehler": {"status": 410, "error": "schon_abgeholt", "meldung": "Die Unterschrift wurde schon abgeholt."}},
                       ops=bis_zum_feld({"op": "handy", "warte": 700}))
        a = abholen["schritte"][3]["zustand"]
        self.assertIn("Die Unterschrift wurde schon abgeholt.", a["handy_status"])
        self.assertTrue(a["knoepfe"]["handy_neu"]["sichtbar"])

    def test_qr_svg_wird_nie_als_markup_eingefuegt(self):
        boese = '<svg xmlns="http://www.w3.org/2000/svg"><script>window.__x=1</script><rect width="1" height="1"/></svg>'
        o = lauf(seite="/u", fragment="#tok", api={"handoffStatus": ["wartet"], "qrSvg": boese}, ops=bis_zum_feld({"op": "handy", "warte": 300}))
        z = o["schritte"][3]["zustand"]
        self.assertEqual((z["qr"]["tag"], z["skripte_im_dom"], z["qr_ausgefuehrt"]), ("IMG", 0, False))
        kein = lauf(seite="/u", fragment="#tok", api={"handoffStatus": ["wartet"], "qrSvg": "javascript:alert(1)"}, ops=bis_zum_feld({"op": "handy", "warte": 300}))
        self.assertFalse(kein["schritte"][3]["zustand"]["qr"]["sichtbar"])                      # kein SVG: kein Bild


@unittest.skipUnless(BEREIT, "braucht Node und jsdom (siehe README.md)")
class TestHandySeite(unittest.TestCase):
    def test_token_nur_im_fragment_zeichnen_und_uebertragen(self):
        o = lauf(seite="/h", fragment="#geheim123", api={"handyInfo": {"person": 2, "personen": 2, "status": "verbunden"}},
                 ops=[{"op": "tippen", "x": 30, "y": 30}, {"op": "zeichne", "punkte": STRICH}, {"op": "uebertragen", "warte": 500}])
        start = o["start"]
        self.assertEqual((start["hash"], start["pfad"]), ("", "/h"))                            # Kennzeichen aus der Adresszeile entfernt
        self.assertEqual([r for r in start["requests"] if r["p"].endswith("/handy/info")][0]["body"], {"token": "geheim123"})
        self.assertEqual(start["label"], "Unterschrift Person 2 von 2")
        self.assertTrue(start["knoepfe"]["uebertragen"]["gesperrt"])
        self.assertEqual(start["fehlt"], "Bitte hier unterschreiben.")
        self.assertTrue(o["schritte"][0]["zustand"]["knoepfe"]["uebertragen"]["gesperrt"])      # leeres Antippen schaltet den Knopf nicht frei
        gezeichnet = o["schritte"][1]["zustand"]
        self.assertFalse(gezeichnet["knoepfe"]["uebertragen"]["gesperrt"])
        self.assertIsNone(gezeichnet["fehlt"])
        s = o["schritte"][2]
        self.assertEqual(posts(s, "/handy/unterschrift")[0]["body"], {"token": "geheim123", "bild": PNG})
        self.assertIn("Übertragen!", s["zustand"]["fertig"])
        for r in start["requests"] + s["zustand"]["requests"]:
            self.assertNotIn("geheim123", r["p"])                                               # nie in der Adresse an den Server

    def test_einzelne_person_hat_keine_nummer(self):
        o = lauf(seite="/h", fragment="#tok", ops=[])
        self.assertEqual(o["start"]["label"], "Unterschrift")

    def test_ungueltiger_link_zeigt_nur_die_meldung(self):
        ohne = lauf(seite="/h", fragment="", ops=[])
        self.assertEqual(ohne["start"]["meldung"], "Dieser Link gilt nicht mehr. Bitte am Computer einen neuen QR-Code anzeigen.")
        self.assertNotIn("step-zeichnen", ohne["start"]["schritt"])
        abgelaufen = lauf(seite="/h", fragment="#tok", api={"handyInfoFehler": {"status": 404, "error": "link_ungueltig", "meldung": "Dieser Link gilt nicht mehr (Server)."}}, ops=[])
        self.assertEqual(abgelaufen["start"]["meldung"], "Dieser Link gilt nicht mehr (Server).")
        self.assertNotIn("step-zeichnen", abgelaufen["start"]["schritt"])

    def test_fehler_beim_uebertragen_stehen_im_text(self):
        leer = lauf(seite="/h", fragment="#tok", api={"handyUnterschriftFehler": {"status": 422, "error": "bild_leer", "meldung": "Das Bild ist leer."}},
                    ops=[{"op": "zeichne", "punkte": STRICH}, {"op": "uebertragen", "warte": 500}])
        z = leer["schritte"][1]["zustand"]
        self.assertEqual(z["meldung"], "Das Bild ist leer.")
        self.assertIn("step-zeichnen", z["schritt"])                                            # das Feld bleibt zum Korrigieren
        self.assertFalse(z["knoepfe"]["uebertragen"]["gesperrt"])
        self.assertEqual(z["alerts"], [])
        schon = lauf(seite="/h", fragment="#tok", api={"handyUnterschriftFehler": {"status": 409, "error": "schon_gezeichnet", "meldung": "Hier wurde schon unterschrieben."}},
                     ops=[{"op": "zeichne", "punkte": STRICH}, {"op": "uebertragen", "warte": 500}])
        s = schon["schritte"][1]["zustand"]
        self.assertEqual(s["meldung"], "Hier wurde schon unterschrieben.")
        self.assertNotIn("step-zeichnen", s["schritt"])                                         # nichts mehr zum Bedienen
        viele = lauf(seite="/h", fragment="#tok", api={"handyUnterschriftFehler": {"status": 429, "error": "zu_viele_versuche", "meldung": "Zu viele Versuche."}},
                     ops=[{"op": "zeichne", "punkte": STRICH}, {"op": "uebertragen", "warte": 500}])
        self.assertEqual(viele["schritte"][1]["zustand"]["meldung"], "Zu viele Versuche.")
        self.assertIn("step-zeichnen", viele["schritte"][1]["zustand"]["schritt"])


if __name__ == "__main__":
    unittest.main()
