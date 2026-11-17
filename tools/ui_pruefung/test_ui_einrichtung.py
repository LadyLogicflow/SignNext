"""jsdom-Prüfung der Seiten Mail-Zugang und Einstellungen (Konzept 17.11.2026, T5): Texte im Klartext, Rückmeldungen im Text statt in Browser-Meldungen,
Löschen erst nach Rückfrage. Schnittstelle nachgespielt nach api.py (mail_zugang.sicht, einstellungen)."""
import unittest

from ui_hilfen import BEREIT, HINWEIS, lauf

MAIL = "/mail-zugang"
EINST = "/einstellungen"
FEHLER = "Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal."


def f(o, i=None):
    return (o["start"] if i is None else o["schritte"][i]["zustand"])["berater"]["formular"]


def anfragen(o, i, methode, pfad=None):
    return [(r["m"], r["p"], r["body"]) for r in o["schritte"][i]["zustand"]["requests"] if r["m"] == methode and (pfad is None or r["p"] == pfad)]


def alerts(o, i):
    return o["schritte"][i]["zustand"]["alerts"]


@unittest.skipUnless(BEREIT, HINWEIS)
class TestMailZugang(unittest.TestCase):
    def test_laden_fuellt_die_felder_und_der_hinweis_zum_passwort_stimmt(self):
        o = lauf(seite=MAIL, ops=[])
        w = f(o)["werte"]
        self.assertEqual((w["host"], w["port"], w["benutzer"], w["passwort"]), ("smtp.example", "587", "konto", ""))
        self.assertEqual(f(o)["passwort_hinweis"], "Bleibt das Feld leer, wird das bisherige Passwort beibehalten.")
        neu = lauf(seite=MAIL, api={"mailZugang": {"konfiguriert": False}}, ops=[])
        self.assertEqual(f(neu)["passwort_hinweis"], "Beim ersten Einrichten bitte eintragen.")
        self.assertEqual(f(neu)["werte"]["host"], "")

    def test_ladefehler_steht_im_text(self):
        o = lauf(seite=MAIL, api={"hinweisFehler": True}, ops=[])
        self.assertEqual(f(o)["seite_fehler"], "Der Mail-Zugang konnte nicht geladen werden. Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.")
        self.assertEqual(o["start"]["alerts"], [])

    def test_leere_angaben_werden_erklaert_und_nichts_gesendet(self):
        o = lauf(seite=MAIL, api={"mailZugang": {"konfiguriert": False}}, ops=[{"op": "submit_id", "id": "form-mail", "warte": 200}])
        fe = f(o, 0)["fehler"]
        self.assertEqual(fe["host-error"], "Bitte tragen Sie den Server Ihres E-Mail-Anbieters ein.")
        self.assertEqual(fe["port-error"], "Bitte wählen Sie einen Port.")
        self.assertEqual(fe["benutzer-error"], "Bitte tragen Sie den Benutzernamen ein.")
        self.assertEqual(fe["absender-name-error"], "Bitte tragen Sie den Namen ein, der beim Mitglied als Absender erscheint.")
        self.assertEqual(fe["absender-adresse-error"], "Bitte tragen Sie die Absenderadresse ein.")
        self.assertEqual(fe["passwort-error"], "Beim ersten Einrichten ist das Passwort nötig.")
        self.assertEqual(fe["save-error"], "Bitte korrigieren Sie die markierten Angaben.")
        self.assertEqual(anfragen(o, 0, "PUT"), [])
        self.assertEqual(alerts(o, 0), [])

    def test_formatfehler(self):
        o = lauf(seite=MAIL, ops=[{"op": "wert_id", "id": "host", "wert": "smtp bad!"}, {"op": "wert_id", "id": "absender-adresse", "wert": "keine-adresse"}])
        fe = f(o, 1)["fehler"]
        self.assertIn("nur Buchstaben, Ziffern, Punkt und Bindestrich", fe["host-error"])
        self.assertEqual(fe["absender-adresse-error"], "Das ist keine gültige E-Mail-Adresse.")

    def test_speichern_meldet_im_text_und_sendet_das_passwort_nur_wenn_eingegeben(self):
        ops = [{"op": "wert_id", "id": "host", "wert": "smtp.neu.example"}, {"op": "submit_id", "id": "form-mail", "warte": 500},
               {"op": "wert_id", "id": "passwort", "wert": "geheim-neu"}, {"op": "submit_id", "id": "form-mail", "warte": 500}]
        o = lauf(seite=MAIL, ops=ops)
        erst = anfragen(o, 1, "PUT", "/api/v1/mail-zugang")[0][2]
        self.assertEqual(erst, {"host": "smtp.neu.example", "port": 587, "benutzer": "konto", "absender_name": "Beratungsstelle", "absender_adresse": "noreply@example.org", "signatur": None, "bcc_an_absender": False})
        self.assertNotIn("passwort", erst)
        self.assertIs(erst["bcc_an_absender"], False)
        self.assertEqual(f(o, 1)["meldung"], "Der Mail-Zugang wurde gespeichert. Senden Sie jetzt eine Testmail, um ihn zu prüfen.")
        self.assertEqual(anfragen(o, 3, "PUT", "/api/v1/mail-zugang")[0][2]["passwort"], "geheim-neu")
        self.assertEqual(f(o, 3)["werte"]["passwort"], "")                                               # nach dem Speichern steht das Passwort nicht mehr im Feld
        self.assertEqual([alerts(o, i) for i in range(4)], [[], [], [], []])

    def test_serverfehler_am_feld_oder_im_text(self):
        am_feld = {"mailPut": {"status": 422, "body": {"error": "absender_adresse_domain", "meldung": "Die Absenderadresse passt nicht zu Ihrem Mailserver.", "feld": "absender_adresse"}}}
        o = lauf(seite=MAIL, api=am_feld, ops=[{"op": "submit_id", "id": "form-mail", "warte": 400}])
        self.assertEqual(f(o, 0)["fehler"], {"absender-adresse-error": "Die Absenderadresse passt nicht zu Ihrem Mailserver."})
        im_text = {"mailPut": {"status": 500, "body": {"x": 1}}}
        o = lauf(seite=MAIL, api=im_text, ops=[{"op": "submit_id", "id": "form-mail", "warte": 400}])
        self.assertEqual(f(o, 0)["fehler"], {"save-error": FEHLER})

    def test_testmail_ergebnis_im_text_mit_technischen_angaben(self):
        o = lauf(seite=MAIL, ops=[{"op": "klick_id", "id": "btn-test", "warte": 400}])
        t = f(o, 0)["test"]
        self.assertTrue(t["erfolg"])
        self.assertEqual(t["text"], ["✓ Die Testmail wurde gesendet", "Sie ging an die Absenderadresse noreply@example.org. Kommt sie dort an, ist alles eingerichtet."])
        self.assertEqual(t["technik"], ["Verschlüsselung (TLS)=TLSv1.3", "Verfahren=TLS_AES_256_GCM_SHA384", "Zertifikat gültig bis=2027-01-01", "Aussteller=Beispiel CA"])
        self.assertEqual(anfragen(o, 0, "POST", "/api/v1/mail-zugang/pruefen"), [("POST", "/api/v1/mail-zugang/pruefen", {})])
        self.assertEqual(alerts(o, 0), [])                                                               # kein confirm mehr

    def test_testmail_fehler_und_ohne_gespeicherten_zugang(self):
        fehl = {"mailPruefen": {"status": 502, "body": {"ok": False, "error": "mail_verbindung", "meldung": "Der Mailserver ist nicht erreichbar."}}}
        o = lauf(seite=MAIL, api=fehl, ops=[{"op": "klick_id", "id": "btn-test", "warte": 400}])
        t = f(o, 0)["test"]
        self.assertFalse(t["erfolg"])
        self.assertEqual(t["text"], ["✗ Die Testmail konnte nicht gesendet werden", "Der Mailserver ist nicht erreichbar."])
        ohne = lauf(seite=MAIL, api={"mailZugang": {"konfiguriert": False}}, ops=[{"op": "klick_id", "id": "btn-test", "warte": 200}])
        self.assertEqual(f(ohne, 0)["fehler"]["save-error"], "Bitte speichern Sie zuerst den Mail-Zugang, dann können Sie eine Testmail senden.")
        self.assertEqual(anfragen(ohne, 0, "POST", "/api/v1/mail-zugang/pruefen"), [])

    def test_loeschen_erst_nach_rueckfrage(self):
        o = lauf(seite=MAIL, ops=[{"op": "klick_id", "id": "btn-delete", "warte": 100}, {"op": "klick_id", "id": "btn-loeschen-nein", "warte": 100},
                                  {"op": "klick_id", "id": "btn-delete", "warte": 100}, {"op": "klick_id", "id": "btn-loeschen-ja", "warte": 400}])
        self.assertTrue(f(o, 0)["rueckfrage"])
        self.assertEqual(anfragen(o, 0, "DELETE"), [])
        self.assertFalse(f(o, 1)["rueckfrage"])
        self.assertEqual(anfragen(o, 1, "DELETE"), [])
        self.assertEqual(anfragen(o, 3, "DELETE"), [("DELETE", "/api/v1/mail-zugang", None)])
        self.assertEqual(f(o, 3)["meldung"], "Der Mail-Zugang wurde gelöscht.")
        self.assertEqual(f(o, 3)["werte"]["host"], "")                                                   # Formular ist leer
        self.assertEqual(f(o, 3)["passwort_hinweis"], "Beim ersten Einrichten bitte eintragen.")
        self.assertEqual([alerts(o, i) for i in range(4)], [[], [], [], []])

    def test_loeschen_scheitert_text_im_formular(self):
        o = lauf(seite=MAIL, api={"mailDelete": {"status": 500, "body": {"x": 1}}}, ops=[{"op": "klick_id", "id": "btn-delete", "warte": 100}, {"op": "klick_id", "id": "btn-loeschen-ja", "warte": 400}])
        self.assertEqual(f(o, 1)["fehler"], {"save-error": FEHLER})
        self.assertFalse(f(o, 1)["rueckfrage"])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestEinstellungen(unittest.TestCase):
    def test_laden_und_ladefehler(self):
        o = lauf(seite=EINST, ops=[])
        self.assertEqual((f(o)["werte"]["verantwortlicher"], f(o)["werte"]["datenschutz-link"], f(o)["werte"]["aufbewahrung-tage"], f(o)["download"]),
                         ("Beratungsstelle Beispiel, Musterstr. 1", "https://beispiel.example/datenschutz", "90", False))
        kaputt = lauf(seite=EINST, api={"hinweisFehler": True}, ops=[])
        self.assertEqual(f(kaputt)["seite_fehler"], "Die Einstellungen konnten nicht geladen werden. " + FEHLER)
        self.assertEqual(kaputt["start"]["alerts"], [])

    def test_pruefung_der_eingaben_im_klartext(self):
        faelle = [
            ({"verantwortlicher": "ab"}, "verantwortlicher-error", "Bitte tragen Sie Name, Anschrift und Kontakt Ihrer Beratungsstelle ein (mindestens 5 Zeichen)."),
            ({"datenschutz-link": ""}, "link-error", "Bitte tragen Sie den Link zu Ihrer Datenschutzerklärung ein."),
            ({"datenschutz-link": "http://unsicher.example"}, "link-error", "Die Adresse muss mit https:// beginnen."),
            ({"aufbewahrung-tage": "10"}, "tage-error", "Die Dokumente müssen mindestens 30 Tage gespeichert bleiben."),
            ({"aufbewahrung-tage": "400"}, "tage-error", "Die Dokumente dürfen höchstens 365 Tage gespeichert bleiben."),
            ({"aufbewahrung-tage": ""}, "tage-error", "Bitte geben Sie eine Zahl ein.")]
        for wert, fehlerfeld, text in faelle:
            ops = [{"op": "wert_id", "id": k, "wert": v} for k, v in wert.items()] + [{"op": "submit_id", "id": "form-einstellungen", "warte": 200}]
            o = lauf(seite=EINST, ops=ops)
            letzter = len(ops) - 1
            self.assertEqual(f(o, letzter)["fehler"][fehlerfeld], text, wert)
            self.assertEqual(f(o, letzter)["fehler"]["save-error"], "Bitte korrigieren Sie die markierten Angaben.", wert)
            self.assertEqual(anfragen(o, letzter, "PUT"), [], wert)
            self.assertEqual(alerts(o, letzter), [], wert)

    def test_speichern_meldet_im_text(self):
        o = lauf(seite=EINST, ops=[{"op": "wert_id", "id": "aufbewahrung-tage", "wert": "120"}, {"op": "klick_id", "id": "mitglied-download-link", "warte": 50}, {"op": "submit_id", "id": "form-einstellungen", "warte": 500}])
        put = anfragen(o, 2, "PUT", "/api/v1/einstellungen")
        self.assertEqual(put, [("PUT", "/api/v1/einstellungen", {"verantwortlicher": "Beratungsstelle Beispiel, Musterstr. 1", "datenschutz_link": "https://beispiel.example/datenschutz", "aufbewahrung_tage": 120, "mitglied_download_link": True})])
        self.assertEqual(f(o, 2)["meldung"], "Die Einstellungen wurden gespeichert.")
        self.assertEqual(alerts(o, 2), [])

    def test_serverfehler_beim_speichern_im_text(self):
        o = lauf(seite=EINST, api={"einstellungenPut": {"status": 422, "body": {"error": "eingabe_ungueltig", "meldung": "Die Eingabe ist ungültig."}}}, ops=[{"op": "submit_id", "id": "form-einstellungen", "warte": 400}])
        self.assertEqual(f(o, 0)["fehler"], {"save-error": "Die Eingabe ist ungültig."})
        self.assertIsNone(f(o, 0)["meldung"])


if __name__ == "__main__":
    unittest.main()
