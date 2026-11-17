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

    def test_gegen_das_echte_backend_im_demo_kein_balken_keine_demo_karten(self):
        o = self.lauf("/vorgaenge", [])
        b = o["start"]["berater"]
        self.assertEqual((b["demo_elemente"], b["testversion_woerter"]), (0, False))                  # der Server meldet Demo, die Seite zeigt nichts davon
        e = self.lauf("/einstellungen", [])["start"]["berater"]
        self.assertEqual((e["demo_elemente"], e["testversion_woerter"]), (0, False))
        self.assertEqual(o["fehler_js"], [])

    ZUM_SENDEN = [{"op": "haken_id", "id": "angaben-stimmen", "warte": 50}, {"op": "submit_id", "id": "form-angaben", "warte": 3000}]

    def test_versenden_zeigt_link_und_qr_und_die_mail_ging_raus(self):
        vid = self.bereit()
        o = self.lauf("/pruefung?id=%d" % vid, self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-versenden", "warte": 3000}])
        self.assertEqual(o["schritte"][1]["zustand"]["berater"]["vorgang"]["panel"], ["senden"])
        ende = o["schritte"][2]["zustand"]["berater"]["vorgang"]
        self.assertEqual(ende["panel"], ["status"])
        box = ende["status"]["link_box"]
        self.assertTrue(box["link"].startswith("https://signnext.example/u#"), box)
        self.assertEqual(box["qr"]["tag"], "IMG")
        self.assertIn("Geburtsdatum", box["text"])
        self.assertEqual(len(self.srv.nachrichten), 1)                                              # Mail UND Bildschirm
        self.assertIn(box["link"], self.mail_text(self.srv.nachrichten[0])[1])

    def test_mailfehler_bietet_ohne_mail_an_und_der_link_funktioniert_danach(self):
        self.srv.mail_ablehnen = True
        vid = self.bereit()
        o = self.lauf("/pruefung?id=%d" % vid, self.ZUM_SENDEN + [{"op": "klick_id", "id": "btn-versenden", "warte": 3000}, {"op": "klick_id", "id": "btn-ohne-mail", "warte": 3000}])
        fehl = o["schritte"][2]["zustand"]["berater"]["vorgang"]
        self.assertEqual(fehl["panel"], ["senden"])
        self.assertTrue(fehl["senden"]["versand_fehler"])                                           # echter Fehler des Servers
        self.assertTrue(fehl["senden"]["ohne_mail"])                                                # fallback: "ohne_mail" -> Angebot
        ende = o["schritte"][3]["zustand"]["berater"]["vorgang"]
        self.assertEqual(ende["panel"], ["status"])
        self.assertTrue(ende["status"]["link_box"]["link"].startswith("https://signnext.example/u#"))
        self.assertIn("(ohne E-Mail)", ende["status"]["link_box"]["text"])
        self.assertEqual(len(self.srv.nachrichten), 0)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT status FROM vorgang WHERE id=?", (vid,)).fetchone()[0], "versendet")
        zugangstext = ende["status"]["link_box"]["link"].split("#", 1)[1]
        r = self.c.post("/api/v1/zugang/pruefen", json={"token": zugangstext, "code": "010260"}, headers={"Origin": "https://signnext.example"})
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
