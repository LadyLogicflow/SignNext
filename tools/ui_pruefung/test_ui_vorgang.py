"""jsdom-Prüfung der Vorgangsseite (Konzept 17.11.2026, T4): Angaben prüfen, an das Mitglied senden, Stand sehen, Dokumente abholen. Der Schritt ergibt sich aus dem Stand des Vorgangs.
Die Schnittstelle spielt der Treiber nach den Antwortformen des Backends nach (api.py, pruefung.ansicht)."""
import unittest
from urllib.parse import unquote

from ui_hilfen import BEREIT, HINWEIS, lauf

SEITE = "/pruefung?id=1"
DEMO = {"status": 200, "body": {"email": "demo@example.org", "demo": True, "demo_banner": "DEMO – Testdaten, keine echten Personendaten"}}
QR_SVG = '<svg viewBox="0 0 10 10"><title>QR-Code</title><rect width="10" height="10" fill="#000"/></svg>'
QR_ALS_BILD = QR_SVG.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)


def seite(o, i=None):
    return (o["start"] if i is None else o["schritte"][i]["zustand"])["berater"]["vorgang"]


def anfragen(o, i, methode):
    return [(r["m"], r["p"], r["body"]) for r in o["schritte"][i]["zustand"]["requests"] if r["m"] == methode]


def stand(status, **mehr):
    return {"vorgang": dict({"status": status}, **mehr)}


FERTIG = {"status": "abgeschlossen", "unterschrieben_am": "2026-11-15T15:48:00Z", "abgeschlossen_am": "2026-11-15T15:50:00Z", "aufbewahrung_bis": "2027-11-15T12:00:00Z", "personen": 2}


@unittest.skipUnless(BEREIT, HINWEIS)
class TestSchrittNachStand(unittest.TestCase):
    def test_jeder_stand_zeigt_den_passenden_schritt_mit_klartext_titel(self):
        faelle = [
            ("bereit", "pruefen", "Angaben prüfen", "2"), ("bestaetigung_noetig", "pruefen", "Angaben prüfen", "2"),
            ("neu", "status", "Mustermann_Steuer2025 · Wird ausgewertet", "3"), ("versenden", "status", "Mustermann_Steuer2025 · Wird gesendet", "3"),
            ("versendet", "status", "Mustermann_Steuer2025 · Wartet auf Unterschrift", "3"), ("versand_unklar", "status", "Mustermann_Steuer2025 · Versand unklar", "3"),
            ("unterschrieben", "status", "Mustermann_Steuer2025 · Unterschrieben", "3"), ("abschluss_laeuft", "status", "Mustermann_Steuer2025 · Unterschrieben", "3"),
            ("abschluss_fehlgeschlagen", "status", "Mustermann_Steuer2025 · Fertigstellung hakt", "3"), ("abgeschlossen", "abholen", "Mustermann_Steuer2025 · Fertig", "4"),
            ("geloescht", "geloescht", "Vorgang gelöscht", None)]
        for status, panel, titel, aktuell in faelle:
            v = seite(lauf(seite=SEITE, api=stand(status), ops=[]))
            self.assertEqual((v["panel"], v["titel"], v["seitentitel"]), ([panel], titel, titel + " - SignNext"), status)
            if aktuell:
                self.assertEqual([s["nr"] for s in v["schritte"] if s["aktuell"]], [aktuell], status)
                self.assertEqual([s["marke"] for s in v["schritte"] if s["klasse"] == "fertig"], ["✓"] * (int(aktuell) - 1), status)
            self.assertNotRegex(v["titel"], r"_(?!Steuer)|abschluss|versand_", status)                       # nie ein Statusname aus dem Code
            self.assertEqual(v["leiste_sichtbar"], aktuell is not None, status)

    def test_ohne_oder_mit_falscher_nummer_steht_ein_satz_da(self):
        for adresse in ("/pruefung", "/pruefung?id=abc", "/pruefung?id=1%3Cb%3E", "/pruefung?id="):
            v = seite(lauf(seite=adresse, ops=[]))
            self.assertEqual(v["seite_fehler"]["text"], "Es wurde kein Vorgang ausgewählt. Bitte öffnen Sie ihn über die Liste Ihrer Vorgänge.", adresse)
            self.assertFalse(v["seite_fehler"]["nochmal"])

    def test_unbekannter_vorgang_und_serverfehler(self):
        o = lauf(seite=SEITE, api={"vorgangFehler": {"status": 404, "body": {"error": "nicht_gefunden"}}}, ops=[])
        self.assertEqual(seite(o)["seite_fehler"], {"text": "Dieser Vorgang wurde nicht gefunden. Vielleicht wurde er gelöscht.", "nochmal": False})
        o = lauf(seite=SEITE, api={"vorgangFehler": {"status": 500, "body": {"x": 1}}}, ops=[])
        self.assertEqual(seite(o)["seite_fehler"], {"text": "Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.", "nochmal": True})
        self.assertEqual(o["start"]["alerts"], [])                                                           # keine Browser-Meldung, nur Text


@unittest.skipUnless(BEREIT, HINWEIS)
class TestPruefen(unittest.TestCase):
    def test_angaben_und_hinweise(self):
        p = seite(lauf(seite=SEITE, ops=[]))["pruefen"]
        self.assertEqual(p["pdf"], "/api/v1/vorgaenge/1/dokument")
        self.assertEqual((p["name"], p["name_hinweis"]), ("Erika Muster", "Vorgang: Mustermann_Steuer2025"))
        self.assertEqual((p["personen"], p["pers_hinweis"]), ("2", "Aus dem Steuerprotokoll erkannt."))
        self.assertEqual((p["email"], p["email_hinweis"]), ("erika@example.org", "Aus dem Anschreiben übernommen."))
        self.assertEqual((p["geb"], p["geb_hinweis"]), ("01.02.1960", "Aus dem Protokoll übernommen."))
        self.assertFalse(p["haken"])
        self.assertEqual((p["haken_text"], p["knopf"]), ("Ja, die Angaben stimmen.", "Weiter zum Senden"))
        self.assertIsNone(p["abweichung"])

    def test_fehlende_und_manuelle_angaben_sind_erklaert(self):
        angaben = {"email": {"wert": None, "herkunft": "fehlt"}, "personen": {"wert": None, "herkunft": "fehlt"}, "geburtsdatum": {"wert": None, "herkunft": "manuell", "gesetzt": True}}
        p = seite(lauf(seite=SEITE, api={"angaben": angaben}, ops=[]))["pruefen"]
        self.assertEqual((p["email"], p["email_hinweis"], p["personen"], p["pers_hinweis"]), ("", "Fehlt noch – bitte eintragen.", "", "Bitte wählen."))
        self.assertEqual(p["geb"], "")
        self.assertEqual(p["geb_platz"], "eingetragen – zum Ändern neu eingeben")                              # das Datum wird nie angezeigt, nur dass es gesetzt ist
        self.assertIn("aus Datenschutzgründen nicht angezeigt", p["geb_hinweis"])

    def test_pflichtfelder_format_und_haken_nichts_wird_gesendet(self):
        angaben = {"email": {"wert": None, "herkunft": "fehlt"}, "personen": {"wert": None, "herkunft": "fehlt"}, "geburtsdatum": {"wert": None, "herkunft": "fehlt", "gesetzt": False}}
        o = lauf(seite=SEITE, api={"angaben": angaben}, ops=[{"op": "submit_id", "id": "form-angaben", "warte": 200},
                                                              {"op": "auswahl_id", "id": "personen", "wert": "2"}, {"op": "wert_id", "id": "email", "wert": "kein-mail"}, {"op": "wert_id", "id": "geburtsdatum", "wert": "1.2.60"},
                                                              {"op": "submit_id", "id": "form-angaben", "warte": 200}])
        leer = seite(o, 0)["pruefen"]["fehler"]
        self.assertEqual(leer, {"pers": "Bitte wählen Sie, wer unterschreibt.", "email": "Bitte tragen Sie die E-Mail-Adresse des Mitglieds ein.",
                                "geb": "Bitte tragen Sie das Geburtsdatum ein (TT.MM.JJJJ).", "haken": "Bitte bestätigen Sie, dass die Angaben stimmen.", "allgemein": None})
        falsch = seite(o, 4)["pruefen"]["fehler"]
        self.assertEqual((falsch["pers"], falsch["email"]), (None, "Das ist keine gültige E-Mail-Adresse."))
        self.assertEqual(falsch["geb"], "Bitte im Format TT.MM.JJJJ eingeben, zum Beispiel 01.02.1960.")
        self.assertEqual([a for i in (0, 4) for a in anfragen(o, i, "PUT")], [])
        self.assertEqual(o["fehler_js"], [])

    def test_weiter_ohne_aenderung_speichert_nichts_und_zeigt_das_senden(self):
        o = lauf(seite=SEITE, ops=[{"op": "haken_id", "id": "angaben-stimmen", "warte": 50}, {"op": "submit_id", "id": "form-angaben", "warte": 700}])
        self.assertEqual(anfragen(o, 1, "PUT"), [])
        v = seite(o, 1)
        self.assertEqual((v["panel"], v["titel"]), (["senden"], "An das Mitglied senden"))
        self.assertEqual(v["senden"]["an"], "erika@example.org")
        self.assertEqual([s["nr"] for s in v["schritte"] if s["aktuell"]], ["3"])

    def test_nur_geaenderte_angaben_werden_gespeichert(self):
        ops = lambda *w: [{"op": "haken_id", "id": "angaben-stimmen", "warte": 50}] + list(w) + [{"op": "submit_id", "id": "form-angaben", "warte": 700}]
        o = lauf(seite=SEITE, ops=ops({"op": "wert_id", "id": "email", "wert": "neu@example.org"}))
        self.assertEqual(anfragen(o, 2, "PUT"), [("PUT", "/api/v1/vorgaenge/1/angaben", {"email": "neu@example.org"})])
        o = lauf(seite=SEITE, ops=ops({"op": "wert_id", "id": "email", "wert": "neu@example.org"}, {"op": "wert_id", "id": "geburtsdatum", "wert": "03.04.1950"}, {"op": "auswahl_id", "id": "personen", "wert": "1"}))
        self.assertEqual(anfragen(o, 4, "PUT"), [("PUT", "/api/v1/vorgaenge/1/angaben", {"email": "neu@example.org", "geburtsdatum": "03.04.1950", "personen": 1})])
        self.assertEqual(seite(o, 4)["senden"]["an"], "neu@example.org")                                    # danach gilt die neue Adresse
        nur_datum = lauf(seite=SEITE, ops=ops({"op": "wert_id", "id": "geburtsdatum", "wert": "03.04.1950"}))
        self.assertEqual(anfragen(nur_datum, 2, "PUT")[0][2], {"geburtsdatum": "03.04.1950"})                 # die unveränderte E-Mail wird nicht noch einmal gesendet
        nur_personen = lauf(seite=SEITE, ops=ops({"op": "auswahl_id", "id": "personen", "wert": "1"}))
        self.assertEqual(anfragen(nur_personen, 2, "PUT")[0][2], {"personen": 1})

    def test_abweichung_muss_ausdruecklich_bestaetigt_werden(self):
        ab = {"abgleich": {"status": "bestaetigung_noetig", "treffer": ["mitgliedsnummer"], "abweichend": ["steuernummer"], "fehlend": []}}
        o = lauf(seite=SEITE, api=dict(stand("bestaetigung_noetig", **ab)), ops=[{"op": "haken_id", "id": "angaben-stimmen", "warte": 50}, {"op": "submit_id", "id": "form-angaben", "warte": 800}])
        p = seite(o)["pruefen"]
        self.assertEqual(p["abweichung"], "Bitte kurz prüfen: Anschreiben und Steuerprotokoll weichen bei „Steuernummer“ voneinander ab. Gehören beide zu demselben Mitglied?")
        self.assertEqual(p["haken_text"], "Ja, die Angaben stimmen und beide Dokumente gehören zu diesem Mitglied.")
        bestaetigt = anfragen(o, 1, "POST")
        self.assertEqual(bestaetigt, [("POST", "/api/v1/vorgaenge/1/abgleich-bestaetigen", {"bestaetigt": True})])
        self.assertIs(bestaetigt[0][2]["bestaetigt"], True)
        self.assertEqual(seite(o, 1)["panel"], ["senden"])
        ohne_liste = lauf(seite=SEITE, api=stand("bestaetigung_noetig"), ops=[])
        self.assertIn("lassen sich nicht sicher demselben Mitglied zuordnen", seite(ohne_liste)["pruefen"]["abweichung"])
        ohne_haken = lauf(seite=SEITE, api=stand("bestaetigung_noetig", **ab), ops=[{"op": "submit_id", "id": "form-angaben", "warte": 200}])
        self.assertEqual(anfragen(ohne_haken, 0, "POST"), [])                                                # ohne Haken wird nichts bestätigt

    def test_fehler_des_servers_am_feld_oder_im_text(self):
        am_feld = {"angabenFehler": {"status": 422, "body": {"error": "email_ungueltig", "meldung": "Die Mailadresse ist ungültig.", "feld": "email"}}}
        o = lauf(seite=SEITE, api=am_feld, ops=[{"op": "haken_id", "id": "angaben-stimmen", "warte": 50}, {"op": "wert_id", "id": "email", "wert": "neu@example.org"}, {"op": "submit_id", "id": "form-angaben", "warte": 500}])
        v = seite(o, 2)
        self.assertEqual((v["panel"], v["pruefen"]["fehler"]["email"], v["pruefen"]["fehler"]["allgemein"]), (["pruefen"], "Die Mailadresse ist ungültig.", None))
        im_text = {"angabenFehler": {"status": 409, "body": {"error": "zustand_passt_nicht", "meldung": "Der Vorgang befindet sich nicht in diesem Zustand."}}}
        o = lauf(seite=SEITE, api=im_text, ops=[{"op": "haken_id", "id": "angaben-stimmen", "warte": 50}, {"op": "wert_id", "id": "email", "wert": "neu@example.org"}, {"op": "submit_id", "id": "form-angaben", "warte": 500}])
        self.assertEqual(seite(o, 2)["pruefen"]["fehler"]["allgemein"], "Der Vorgang befindet sich nicht in diesem Zustand.")
        self.assertEqual(o["schritte"][2]["zustand"]["alerts"], [])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestSenden(unittest.TestCase):
    ZUM_SENDEN = [{"op": "haken_id", "id": "angaben-stimmen", "warte": 50}, {"op": "submit_id", "id": "form-angaben", "warte": 700}]

    def test_mailzugang_bestimmt_ob_gesendet_werden_kann(self):
        gut = seite(lauf(seite=SEITE, ops=self.ZUM_SENDEN), 1)["senden"]
        self.assertEqual((gut["mailhinweis"], gut["versenden_gesperrt"]), (None, False))
        fehlt = seite(lauf(seite=SEITE, api={"mailZugang": {"konfiguriert": False}}, ops=self.ZUM_SENDEN), 1)["senden"]
        self.assertEqual((fehlt["mailhinweis"], fehlt["versenden_gesperrt"]), ("Ihr Mail-Zugang fehlt noch. Richten Sie ihn ein, dann können Sie den Link per E-Mail senden.", True))
        ungeprueft = seite(lauf(seite=SEITE, api={"mailZugang": {"konfiguriert": True, "geprueft_am": None}}, ops=self.ZUM_SENDEN), 1)["senden"]
        self.assertEqual((ungeprueft["mailhinweis"], ungeprueft["versenden_gesperrt"]), ("Ihr Mail-Zugang ist noch nicht geprüft. Senden Sie zuerst eine Testmail an sich selbst.", True))

    def test_vor_ort_gibt_es_nur_im_demo(self):
        self.assertFalse(seite(lauf(seite=SEITE, ops=self.ZUM_SENDEN), 1)["senden"]["vor_ort"])
        self.assertTrue(seite(lauf(seite=SEITE, api={"me": DEMO}, ops=self.ZUM_SENDEN), 1)["senden"]["vor_ort"])

    def test_senden_per_mail_zeigt_danach_den_stand(self):
        o = lauf(seite=SEITE, ops=self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-versenden", "warte": 700}])
        senden = anfragen(o, 2, "POST")
        self.assertEqual(senden, [("POST", "/api/v1/vorgaenge/1/versenden", {"bestaetigt": True})])
        self.assertIs(senden[0][2]["bestaetigt"], True)
        v = seite(o, 2)
        self.assertEqual((v["panel"], v["titel"]), (["status"], "Mustermann_Steuer2025 · Wartet auf Unterschrift"))
        self.assertEqual(v["meldung"], "Der Link wurde per E-Mail an erika@example.org gesendet.")
        self.assertEqual([(z["text"], z["offen"]) for z in v["status"]["zeitstrahl"]][-2:], [("Link an das Mitglied gesendet", False), ("Wartet auf die Unterschrift", True)])
        self.assertEqual(o["schritte"][2]["zustand"]["alerts"], [])

    def test_im_demo_stehen_link_und_qr_am_bildschirm(self):
        demo = {"link": "https://sn.example/u#abc", "qr_svg": QR_SVG, "geburtsdatum_hinweis": "Geburtsdatum für den Link: 01.02.60", "ohne_mail": False}
        o = lauf(seite=SEITE, api={"me": DEMO, "versenden": {"status": 200, "body": {"ok": True, "demo": demo}}}, ops=self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-versenden", "warte": 700}])
        box = seite(o, 2)["status"]["link_box"]
        self.assertEqual((box["link"], box["link_nur_lesen"]), ("https://sn.example/u#abc", True))
        self.assertIn("Geburtsdatum für den Link: 01.02.60", box["text"])
        self.assertEqual((box["qr"]["tag"], unquote(box["qr"]["src"].split(",", 1)[1])), ("IMG", QR_ALS_BILD))      # nur als Bild, nie als Markup
        ohne = lauf(seite=SEITE, ops=self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-versenden", "warte": 700}])
        self.assertIsNone(seite(ohne, 2)["status"]["link_box"])

    def test_scheitert_die_mail_wird_ohne_mail_angeboten_nur_wenn_der_server_es_sagt(self):
        mail_weg = {"status": 502, "body": {"error": "mail_verbindung", "meldung": "Der Mailserver ist nicht erreichbar.", "fallback": "ohne_mail"}}
        demo = {"link": "https://sn.example/u#ohne", "qr_svg": QR_SVG, "geburtsdatum_hinweis": "Geburtsdatum für den Link: 01.02.60", "ohne_mail": True}
        o = lauf(seite=SEITE, api={"me": DEMO, "versenden": mail_weg, "versendenOhneMail": {"status": 200, "body": {"ok": True, "demo": demo}}},
                 ops=self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-versenden", "warte": 500}, {"op": "klick_id", "id": "btn-ohne-mail", "warte": 700}])
        fehl = seite(o, 2)["senden"]
        self.assertEqual((fehl["versand_fehler"], fehl["ohne_mail"], fehl["versenden_gesperrt"]), ("Der Mailserver ist nicht erreichbar.", True, False))
        ende = seite(o, 3)
        ohne = anfragen(o, 3, "POST")
        self.assertEqual(ohne, [("POST", "/api/v1/vorgaenge/1/versenden", {"bestaetigt": True, "ohne_mail": True})])
        self.assertIs(ohne[0][2]["ohne_mail"], True)
        self.assertEqual(ende["panel"], ["status"])
        self.assertIn("ohne E-Mail", ende["meldung"])
        self.assertIn("(ohne E-Mail)", ende["status"]["link_box"]["text"])
        anders = lauf(seite=SEITE, api={"versenden": {"status": 409, "body": {"error": "mail_zugang_ungeprueft", "meldung": "Bitte zuerst den Mail-Zugang prüfen (Testmail)."}}},
                      ops=self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-versenden", "warte": 500}])
        s = seite(anders, 2)["senden"]
        self.assertEqual((s["versand_fehler"], s["ohne_mail"]), ("Bitte zuerst den Mail-Zugang prüfen (Testmail).", False))

    def test_fehler_beim_ohne_mail_bleibt_im_text(self):
        o = lauf(seite=SEITE, api={"versenden": {"status": 502, "body": {"error": "mail_verbindung", "meldung": "Der Mailserver ist nicht erreichbar.", "fallback": "ohne_mail"}},
                                   "versendenOhneMail": {"status": 409, "body": {"error": "zustand_passt_nicht", "meldung": "Der Vorgang befindet sich nicht in diesem Zustand."}}},
                 ops=self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-versenden", "warte": 500}, {"op": "klick_id", "id": "btn-ohne-mail", "warte": 500}])
        s = seite(o, 3)["senden"]
        self.assertEqual(s["versand_fehler"], "Der Vorgang befindet sich nicht in diesem Zustand.")
        self.assertEqual(seite(o, 3)["panel"], ["senden"])

    def test_vor_ort_zeigt_link_und_qr_ohne_mail(self):
        demo = {"link": "https://sn.example/u#vorort", "qr_svg": QR_SVG, "geburtsdatum_hinweis": "Geburtsdatum für den Link: 01.02.60", "ohne_mail": True}
        o = lauf(seite=SEITE, api={"me": DEMO, "versendenOhneMail": {"status": 200, "body": {"ok": True, "demo": demo}}}, ops=self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-vor-ort", "warte": 700}])
        self.assertEqual(anfragen(o, 2, "POST"), [("POST", "/api/v1/vorgaenge/1/versenden", {"bestaetigt": True, "ohne_mail": True})])
        self.assertEqual(seite(o, 2)["status"]["link_box"]["link"], "https://sn.example/u#vorort")
        fehler = lauf(seite=SEITE, api={"me": DEMO, "versendenOhneMail": {"status": 404, "body": {"error": "nicht_gefunden"}}}, ops=self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-vor-ort", "warte": 500}])
        self.assertEqual(seite(fehler, 2)["senden"]["vor_ort_fehler"], "Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.")

    def test_zurueck_zu_den_angaben(self):
        o = lauf(seite=SEITE, ops=self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-zurueck-pruefen", "warte": 200}])
        self.assertEqual(seite(o, 2)["panel"], ["pruefen"])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestStand(unittest.TestCase):
    def test_hinweis_und_knopf_je_stand(self):
        faelle = [
            (stand("versendet", link_status="abgelaufen"), "hinweis-gelb", "Neuen Link senden", "Der Link ist abgelaufen."),
            (stand("versendet", link_status="gesperrt"), "hinweis-gelb", "Neuen Link senden", "Der Link ist gesperrt, weil das Geburtsdatum zu oft falsch eingegeben wurde."),
            (stand("versand_unklar"), "hinweis-rot", "Neuen Link senden", "Wir wissen nicht, ob die E-Mail angekommen ist."),
            (stand("unterschrieben"), "hinweis-blau", None, "Die Unterschrift ist eingegangen."),
            (stand("abschluss_laeuft"), "hinweis-blau", None, "Die Unterschrift ist eingegangen."),
            (stand("abschluss_fehlgeschlagen"), "hinweis-rot", None, "Die Fertigstellung hat nicht geklappt."),
            (stand("versenden"), "hinweis-blau", None, "Der Link wird gerade gesendet"),
            (stand("neu"), "hinweis-blau", None, "Der Vorgang wird noch ausgewertet.")]
        for api, klasse, aktion, text in faelle:
            h = seite(lauf(seite=SEITE, api=api, ops=[]))["status"]["hinweis"]
            self.assertEqual((h["klasse"], h["aktion"]), (klasse, aktion), api)
            self.assertIn(text, h["text"], api)
        self.assertIsNone(seite(lauf(seite=SEITE, api=stand("versendet", link_status="aktiv"), ops=[]))["status"]["hinweis"])      # alles in Ordnung: kein Hinweis

    def test_knoepfe_je_stand(self):
        aktiv = seite(lauf(seite=SEITE, api=stand("versendet", link_status="aktiv"), ops=[]))["status"]
        self.assertEqual((aktiv["neuer_link"], aktiv["erinnerungen"], aktiv["loeschen"]), (True, True, True))
        abgelaufen = seite(lauf(seite=SEITE, api=stand("versendet", link_status="abgelaufen"), ops=[]))["status"]
        self.assertEqual((abgelaufen["neuer_link"], abgelaufen["erinnerungen"]), (False, True))                 # der Hauptknopf steht schon im Hinweis
        for laeuft in ("abschluss_laeuft", "versenden"):
            self.assertFalse(seite(lauf(seite=SEITE, api=stand(laeuft), ops=[]))["status"]["loeschen"], laeuft)       # wird gerade bearbeitet: nicht löschen
        self.assertIsNone(seite(lauf(seite=SEITE, api=stand("unterschrieben"), ops=[]))["status"]["erinnerungen"])

    def test_zeitstrahl(self):
        v = stand("versendet", link_status="aktiv", versendet_am="2026-11-16T08:21:00Z", bestaetigt_am="2026-11-16T08:20:00Z")
        z = seite(lauf(seite=SEITE, api=v, ops=[]))["status"]["zeitstrahl"]
        self.assertEqual([(x["text"], x["zeit"], x["offen"]) for x in z], [
            ("Hochgeladen", "17.11.2026, 14:05", False), ("Angaben bestätigt", "16.11.2026, 09:20", False), ("Link an das Mitglied gesendet", "16.11.2026, 09:21", False),
            ("Wartet auf die Unterschrift", "Der Link ist gültig.", True)])

    def test_neuer_link_erst_nach_rueckfrage(self):
        api = stand("versendet", link_status="abgelaufen")
        o = lauf(seite=SEITE, api=api, ops=[{"op": "klick_id", "id": "btn-status-aktion", "warte": 100}, {"op": "klick_id", "id": "btn-rueckfrage-nein", "warte": 100},
                                             {"op": "klick_id", "id": "btn-status-aktion", "warte": 100}, {"op": "klick_id", "id": "btn-rueckfrage-ja", "warte": 700}])
        frage = seite(o, 0)["status"]["rueckfrage"]
        self.assertEqual(frage, {"text": "Einen neuen Link per E-Mail an das Mitglied senden? Der bisherige Link funktioniert dann nicht mehr.", "ja": "Ja, neuen Link senden"})
        self.assertEqual([a for a in anfragen(o, 0, "POST") if a[1].endswith("neuer-link")], [])
        self.assertIsNone(seite(o, 1)["status"]["rueckfrage"])
        post = [a for a in anfragen(o, 3, "POST") if a[1].endswith("neuer-link")]
        self.assertEqual(post, [("POST", "/api/v1/vorgaenge/1/neuer-link", {"bestaetigt": True})])
        self.assertIs(post[0][2]["bestaetigt"], True)
        v = seite(o, 3)
        self.assertEqual(v["meldung"], "Ein neuer Link wurde per E-Mail an das Mitglied gesendet.")
        self.assertIsNone(v["status"]["hinweis"])                                                            # Link ist wieder aktiv

    def test_neuer_link_scheitert_text_im_feld(self):
        o = lauf(seite=SEITE, api=dict(stand("versendet", link_status="abgelaufen"), neuerLink={"status": 409, "body": {"error": "mail_zugang_fehlt", "meldung": "Für Ihre Beratungsstelle ist noch kein Mail-Zugang hinterlegt."}}),
                 ops=[{"op": "klick_id", "id": "btn-status-aktion", "warte": 100}, {"op": "klick_id", "id": "btn-rueckfrage-ja", "warte": 500}])
        s = seite(o, 1)["status"]
        self.assertEqual(s["fehler"], "Für Ihre Beratungsstelle ist noch kein Mail-Zugang hinterlegt.")
        self.assertIsNone(s["rueckfrage"])

    def test_erinnerungen_umschalten_und_zurueckrollen(self):
        o = lauf(seite=SEITE, api=stand("versendet", link_status="aktiv"), ops=[{"op": "klick_id", "id": "erinnerungen-aktiv", "warte": 300}])
        put = anfragen(o, 0, "PUT")
        self.assertEqual(put, [("PUT", "/api/v1/vorgaenge/1/erinnerungen", {"an": False})])
        self.assertIs(put[0][2]["an"], False)
        self.assertFalse(seite(o, 0)["status"]["erinnerungen"])
        kaputt = lauf(seite=SEITE, api=dict(stand("versendet", link_status="aktiv"), erinnerungenFehler={"status": 500, "body": {"x": 1}}), ops=[{"op": "klick_id", "id": "erinnerungen-aktiv", "warte": 300}])
        s = seite(kaputt, 0)["status"]
        self.assertTrue(s["erinnerungen"])                                                                   # Schalter springt zurück
        self.assertEqual(s["fehler"], "Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.")

    def test_loeschen_mit_rueckfrage(self):
        api = stand("versendet", link_status="aktiv")
        o = lauf(seite=SEITE, api=api, ops=[{"op": "klick_id", "id": "btn-loeschen", "warte": 100}, {"op": "klick_id", "id": "btn-rueckfrage-nein", "warte": 100},
                                             {"op": "klick_id", "id": "btn-loeschen", "warte": 100}, {"op": "klick_id", "id": "btn-rueckfrage-ja", "warte": 500}])
        self.assertEqual(seite(o, 0)["status"]["rueckfrage"]["text"], "„Mustermann_Steuer2025“ endgültig löschen? Die Dokumente und der Vorgang werden entfernt. Das kann nicht rückgängig gemacht werden.")
        self.assertEqual(seite(o, 0)["status"]["rueckfrage"]["ja"], "Ja, löschen")
        self.assertEqual(anfragen(o, 1, "DELETE"), [])
        loesch = anfragen(o, 3, "DELETE")
        self.assertEqual(loesch, [("DELETE", "/api/v1/vorgaenge/1", {"bestaetigt": True})])
        self.assertIs(loesch[0][2]["bestaetigt"], True)
        v = seite(o, 3)
        self.assertEqual((v["panel"], v["geloescht"], v["leiste_sichtbar"]), (["geloescht"], "„Mustermann_Steuer2025“ wurde gelöscht. Die Dokumente sind entfernt.", False))

    def test_loeschen_scheitert_der_grund_steht_im_text(self):
        meldung = "Der Vorgang wird gerade bearbeitet und kann jetzt nicht gelöscht werden. Bitte in einigen Minuten erneut versuchen."
        o = lauf(seite=SEITE, api=dict(stand("versendet", link_status="aktiv"), loeschen={"status": 409, "body": {"error": "abschluss_laeuft", "meldung": meldung}}),
                 ops=[{"op": "klick_id", "id": "btn-loeschen", "warte": 100}, {"op": "klick_id", "id": "btn-rueckfrage-ja", "warte": 500}])
        self.assertEqual((seite(o, 1)["panel"], seite(o, 1)["status"]["fehler"]), (["status"], meldung))

    def test_aktualisieren_laedt_neu_und_raeumt_den_link_auf(self):
        demo = {"link": "https://sn.example/u#abc", "qr_svg": QR_SVG, "geburtsdatum_hinweis": "Hinweis", "ohne_mail": False}
        o = lauf(seite=SEITE, api={"me": DEMO, "versenden": {"status": 200, "body": {"ok": True, "demo": demo}}},
                 ops=[{"op": "haken_id", "id": "angaben-stimmen", "warte": 50}, {"op": "submit_id", "id": "form-angaben", "warte": 700}, {"op": "klick_id", "id": "btn-versenden", "warte": 700},
                      {"op": "klick_id", "id": "btn-aktualisieren", "warte": 400}])
        self.assertIsNotNone(seite(o, 2)["status"]["link_box"])
        self.assertTrue([a for a in anfragen(o, 3, "GET") if a[1] == "/api/v1/vorgaenge/1"])
        self.assertIsNone(seite(o, 3)["status"]["link_box"])                                                 # der Link wurde nur einmal gezeigt


@unittest.skipUnless(BEREIT, HINWEIS)
class TestAbholen(unittest.TestCase):
    def test_hinweis_frist_und_downloads(self):
        o = lauf(seite=SEITE, api=stand(**FERTIG), ops=[{"op": "klick_id", "id": "btn-dl-dokument", "warte": 400}, {"op": "klick_id", "id": "btn-dl-nachweis", "warte": 400}])
        a = seite(o)["abholen"]
        self.assertEqual(a["hinweis"], "Beide Personen haben unterschrieben (15.11.2026, 16:48). Das Dokument ist fertig.")
        self.assertEqual(a["frist"], "Beide Dateien bleiben bis 15.11.2027 hier abrufbar und werden danach automatisch gelöscht.")
        self.assertEqual(seite(o, 1)["downloads"], ["dokument_1.pdf", "nachweis_1.pdf"])
        einzel = seite(lauf(seite=SEITE, api=stand(**dict(FERTIG, personen=1, aufbewahrung_bis=None)), ops=[]))["abholen"]
        self.assertTrue(einzel["hinweis"].startswith("Die Unterschrift ist eingegangen (15.11.2026, 16:48)."))
        self.assertIn("nach Ablauf der Aufbewahrungsfrist automatisch gelöscht", einzel["frist"])

    def test_download_fehler_steht_im_text(self):
        o = lauf(seite=SEITE, api=dict(stand(**FERTIG), downloadFehler={"status": 404, "body": {"error": "nicht_gefunden"}}), ops=[{"op": "klick_id", "id": "btn-dl-dokument", "warte": 400}])
        self.assertEqual(seite(o, 0)["abholen"]["fehler"], "Der Download hat nicht geklappt. Bitte versuchen Sie es noch einmal.")
        self.assertEqual(o["schritte"][0]["zustand"]["alerts"], [])

    def test_loeschen_nach_rueckfrage(self):
        o = lauf(seite=SEITE, api=stand(**FERTIG), ops=[{"op": "klick_id", "id": "btn-abholen-loeschen", "warte": 100}, {"op": "klick_id", "id": "btn-abholen-nein", "warte": 100},
                                                         {"op": "klick_id", "id": "btn-abholen-loeschen", "warte": 100}, {"op": "klick_id", "id": "btn-abholen-ja", "warte": 500}])
        self.assertEqual(seite(o, 0)["abholen"]["rueckfrage"], "„Mustermann_Steuer2025“ endgültig löschen? Die Dokumente und der Nachweis werden entfernt. Das kann nicht rückgängig gemacht werden.")
        self.assertEqual(anfragen(o, 1, "DELETE"), [])
        self.assertEqual(anfragen(o, 3, "DELETE"), [("DELETE", "/api/v1/vorgaenge/1", {"bestaetigt": True})])
        self.assertEqual(seite(o, 3)["panel"], ["geloescht"])


if __name__ == "__main__":
    unittest.main()
