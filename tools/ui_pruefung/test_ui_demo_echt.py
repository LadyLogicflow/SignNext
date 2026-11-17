"""jsdom-Prüfung der Berater-Seiten im Demo-Modus gegen das ECHTE Backend (ab v0.7.0 mit Demo-Endpunkten): echte Anmeldung (Portal-Ersatz der Tests), echter Mailserver-Ersatz,
echte Antworten von /api/v1/me, /demo, /demo/vorgang, /demo/testmail und /versenden. Läuft nur, wenn die Backend-Tests der Demo (tests/test_v070_demo.py) vorhanden sind."""
import os
import sys
import threading
import unittest
from unittest import mock
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ui_hilfen import BEREIT, HINWEIS, lauf

try:
    from werkzeug.serving import make_server
    from tests.test_v070_demo import EXTERN, MITGLIED, DemoMail
    BACKEND = True
except ImportError:
    DemoMail, BACKEND = unittest.TestCase, False

import db


@unittest.skipUnless(BEREIT, HINWEIS)
@unittest.skipUnless(BACKEND, "braucht das Demo-Backend (tests/test_v070_demo.py)")
class TestBeraterGegenEchtesBackend(DemoMail):
    def setUp(self):
        super().setUp()
        self.httpd = make_server("127.0.0.1", 0, self.app, threaded=True)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.basis = "http://localhost:%d" % self.httpd.server_port
        # Betrieb wie auf der Test-Instanz: Offen-Modus hinter einem Proxy, Berater ist die Demo-Beratungsstelle (Mandant "demo"), kein Portal-Cookie
        p = mock.patch.dict(os.environ, {"SIGNNEXT_DEMO_ANMELDUNG": "offen", "SIGNNEXT_PROXIES": "1"})
        p.start()
        self.addCleanup(p.stop)
        self.c.environ_base["HTTP_X_FORWARDED_FOR"] = "127.0.0.1"
        self.einstellungen_setzen("demo")
        self.assertEqual(self.zugang(self.srv).status_code, 200)
        self.markiere_geprueft("demo")

    def lauf(self, seite, ops):
        return lauf(basis=self.basis, kopf={"X-Forwarded-For": "127.0.0.1"}, seite=seite, ops=ops)

    def test_vorgangsliste_banner_beispiele_und_demo_vorgang(self):
        o = self.lauf("/vorgaenge", [{"op": "klick_id", "id": "btn-demo-vorgang", "warte": 3000}])
        b = o["start"]["berater"]
        self.assertEqual(b["banner"], "DEMO – Testdaten, keine echten Personendaten")
        self.assertTrue(b["demo_bereich"])
        self.assertEqual([a["href"] for a in b["pdf_links"]], ["/api/v1/demo/beispiel/" + n for n in ("anschreiben", "anschreiben-einzel", "protokoll-ehepaar", "protokoll-einzel")])
        neu = o["schritte"][0]["zustand"]["berater"]
        e = neu["demo_ergebnis"]
        self.assertTrue(e["link"].startswith("https://signnext.example/u#"), e)
        self.assertEqual(e["qr"]["tag"], "IMG")
        self.assertTrue(unquote(e["qr"]["src"].split(",", 1)[1]).startswith("<svg"))
        self.assertRegex(e["text"], r"Geburtsdatum \d\d\.\d\d\.\d{4} verlangt \(Eingabe: \d{6}\)")
        self.assertGreaterEqual(neu["vorgang_karten"], 1)                                          # die Liste zeigt den neuen Vorgang
        self.assertEqual(o["fehler_js"], [])

    def test_testmail_geht_wirklich_an_die_gewaehlte_erlaubte_adresse(self):
        o = self.lauf("/einstellungen", [{"op": "auswahl_id", "id": "demo-testadresse", "wert": EXTERN, "warte": 100}, {"op": "klick_id", "id": "btn-demo-testmail", "warte": 3000}])
        t = o["start"]["berater"]["testmail"]
        self.assertEqual(t["optionen"], sorted({MITGLIED, EXTERN}))
        self.assertIn("nur an die erlaubten Testadressen", t["info"])
        self.assertEqual(o["schritte"][1]["zustand"]["berater"]["testmail"]["erfolg"], "Die Testmail wurde an %s gesendet." % EXTERN)
        self.assertEqual([n["an"] for n in self.srv.nachrichten], [[EXTERN]])                       # wirklich eine Mail, nur an diese Adresse

    def test_versenden_zeigt_link_und_qr_und_die_mail_ging_raus(self):
        vid = self.bereit()
        o = self.lauf("/pruefung?id=%d" % vid, [{"op": "haken_id", "id": "versand-bestaetigung", "warte": 50}, {"op": "klick_id", "id": "btn-versenden", "warte": 3000}])
        box = o["schritte"][1]["zustand"]["berater"]["versand_box"]
        self.assertTrue(box["link"].startswith("https://signnext.example/u#"), box)
        self.assertEqual(box["qr"]["tag"], "IMG")
        self.assertIn("Geburtsdatum", box["text"])
        self.assertEqual(len(self.srv.nachrichten), 1)                                              # Mail UND Bildschirm
        self.assertIn(box["link"], self.mail_text(self.srv.nachrichten[0])[1])
        self.assertIsNone(o["schritte"][1]["zustand"]["berater"]["ohne_mail"])

    def test_mailfehler_bietet_ohne_mail_an_und_der_link_funktioniert_danach(self):
        self.srv.mail_ablehnen = True
        vid = self.bereit()
        o = self.lauf("/pruefung?id=%d" % vid, [{"op": "haken_id", "id": "versand-bestaetigung", "warte": 50}, {"op": "klick_id", "id": "btn-versenden", "warte": 3000},
                                                   {"op": "klick_id", "id": "btn-ohne-mail", "warte": 3000}])
        fehl = o["schritte"][1]["zustand"]["berater"]
        self.assertTrue(fehl["versand_fehler"])                                                     # echter Fehler des Servers
        self.assertEqual(fehl["ohne_mail"], {"knopf_gesperrt": False})                              # fallback: "ohne_mail" -> Angebot
        self.assertIsNone(fehl["versand_box"])
        ende = o["schritte"][2]["zustand"]["berater"]
        self.assertTrue(ende["versand_box"]["link"].startswith("https://signnext.example/u#"))
        self.assertIn("(ohne Mail)", ende["versand_box"]["text"])
        self.assertIsNone(ende["ohne_mail"])
        self.assertEqual(len(self.srv.nachrichten), 0)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT status FROM vorgang WHERE id=?", (vid,)).fetchone()[0], "versendet")
        token = ende["versand_box"]["link"].split("#", 1)[1]
        r = self.c.post("/api/v1/zugang/pruefen", json={"token": token, "code": "010260"}, headers={"Origin": "https://signnext.example"})
        self.assertEqual(r.status_code, 200)                                                        # der angezeigte Link öffnet den Vorgang

    def test_mail_zugang_zeigt_hinweis_und_fehler_am_feld(self):
        self.assertEqual(self.zugang(self.srv, benutzer="a@hilo.example", absender_adresse="b@hilo.example").status_code, 200)
        o = self.lauf("/mail-zugang", [{"op": "wert_id", "id": "port", "wert": "465"}, {"op": "wert_id", "id": "benutzer", "wert": "thomas@hilo.example"},
                                        {"op": "wert_id", "id": "absender-adresse", "wert": "thomas@fremd.example"}, {"op": "submit_id", "id": "form-mail", "warte": 3000}])
        self.assertEqual(len(o["start"]["berater"]["mail_hinweise"]), 1)                            # Benutzer und Absender sind verschieden: nur Hinweis
        fehler = o["schritte"][3]["zustand"]["berater"]
        self.assertIn("DMARC", fehler["absender_fehler"])                                           # 422 mit feld absender_adresse: genau am Feld
        self.assertIsNone(fehler["speichern_fehler"])


if __name__ == "__main__":
    unittest.main()
