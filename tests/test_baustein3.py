"""Baustein 3: Verschlüsselung, Mailer (TLS-Pflicht), Mail-Zugang, Prüfansicht, Angaben, Versand.
Alles mit erfundenen Daten und einem lokalen Test-Mailserver (tests/fake_smtp.py, 127.0.0.1, eigene Test-CA). Die Auswertung der PDFs wird hier zwischengespeichert
(gleiche Bytes = gleiches Ergebnis), die echte Auswertung im Kindprozess prüft test_baustein2."""
import email
import hashlib
import io
import json
import logging
import os
import re
import socket
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import config
import db
import mail_zugang
import mailer
import pdf_eingang
import schluessel
import schluesselwechsel
import versand
from tests import fake_smtp, pdfs
from tests.test_baustein1 import Basis, me
from tests.test_baustein2 import hochladen

HK = "Test-Hauptschluessel-nur-fuer-Tests-0123456789"
HK2 = "Zweiter-Test-Hauptschluessel-fuer-Wechsel-9876543210"
PW = "Test-Passwort-ohne-Bedeutung-1"
ABSENDER = "bst@example.org"
HOST = "mail.test.example"

_ZERT_DIR = tempfile.TemporaryDirectory()
ZERT = fake_smtp.zertifikate(_ZERT_DIR.name)
ZERT_FALSCH = fake_smtp.zertifikate(_ZERT_DIR.name, hostnamen=("anderer-name.example",), name="falsch")
_CACHE = {}
_echt_auswerten = pdf_eingang.auswerten
_echt_ssl_kontext = mailer._ssl_kontext


def _auswerten_gemerkt(daten):
    k = hashlib.sha256(daten).hexdigest()
    if k not in _CACHE:
        _CACHE[k] = _echt_auswerten(daten)
    return json.loads(json.dumps(_CACHE[k]))


def vertrauen():
    return __import__("ssl").create_default_context(cafile=ZERT[0])


class MailBasis(Basis):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK, "SIGNNEXT_BASIS_URL": "https://signnext.example", "SIGNNEXT_VERSAND": "an"}),
                  mock.patch("pdf_eingang.auswerten", side_effect=_auswerten_gemerkt)):
            p.start()
            self.addCleanup(p.stop)
        self.server = []
        self.logtext = io.StringIO()
        h = logging.StreamHandler(self.logtext)
        h.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(h)
        self._alt_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)
        self.addCleanup(logging.getLogger().removeHandler, h)
        self.addCleanup(logging.getLogger().setLevel, self._alt_level)
        self.als("ta", me(ref="1001"))
        self.einstellungen_setzen()

    def einstellungen_setzen(self, ref="1001"):
        with db.verbindung() as c:
            c.execute("INSERT OR REPLACE INTO stelle_einstellung(mandant_ref, verantwortlicher, datenschutz_link, geaendert_am, geaendert_von) VALUES (?,?,?,?,?)",
                      (ref, "Beratungsstelle Beispiel, Musterweg 1, 12345 Musterstadt, kontakt@bst.example", "https://bst.example/datenschutz", db.jetzt(), "test"))

    def markiere_geprueft(self, ref="1001"):
        with db.verbindung() as c:
            c.execute("UPDATE mail_zugang SET geprueft_am=? WHERE mandant_ref=?", (db.jetzt(), ref))

    def starte(self, tls="implicit", zert=None, **kw):
        srv = fake_smtp.FakeSmtp(tls=tls, zert=zert or ZERT, **kw).start()
        self.addCleanup(srv.stop)
        for p in (mock.patch("mailer._aufloesen", return_value=["127.0.0.1"]), mock.patch("mailer._oeffentlich", return_value=True),
                  mock.patch("mailer.ERLAUBTE_PORTS", (465, 587, srv.port)), mock.patch("mailer._implizit_tls", side_effect=lambda port: srv.tls == "implicit"),
                  mock.patch("mailer._ssl_kontext", side_effect=vertrauen)):
            p.start()
            self.addCleanup(p.stop)
        return srv

    def zugang(self, srv, **kw):
        daten = {"host": HOST, "port": srv.port, "benutzer": srv.benutzer, "passwort": srv.passwort, "absender_adresse": ABSENDER,
                 "absender_name": "Beratungsstelle Beispiel", "signatur": "Ihre Beratungsstelle Beispiel"}
        daten.update(kw)
        return self.c.put("/api/v1/mail-zugang", json=daten)

    def db_bytes(self):
        pfad = os.path.join(self.tmp.name, "t.db")
        out = b""
        for endung in ("", "-wal", "-shm"):
            if os.path.isfile(pfad + endung):
                with open(pfad + endung, "rb") as f:
                    out += f.read()
        return out

    def db_text(self):
        with db.verbindung() as c:
            tabellen = [z["name"] for z in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return json.dumps({t: [dict(z) for z in c.execute("SELECT * FROM %s" % t)] for t in tabellen}, default=str)

    def bereit(self, a=True, b=True, **kw):
        ds = [d for d, ok in ((pdfs.b_pdf(**kw), b), (pdfs.a_pdf(), a)) if ok]
        r = hochladen(self.c, *ds)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["vorgang_id"]

    def senden_ok(self, vid):
        return self.c.post("/api/v1/vorgaenge/%d/versenden" % vid, json={"bestaetigt": True})

    def mail_text(self, nachricht):
        m = email.message_from_bytes(nachricht["daten"])
        teil = m.get_payload(decode=True).decode("utf-8")
        return m, teil


# ======================================================================================================== Schlüssel

class TestSchluessel(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK})
        p.start()
        self.addCleanup(p.stop)

    def test_verschluesseln_hin_und_zurueck(self):
        t = schluessel.verschluesseln("1001", "mailpasswort", "geheim")
        self.assertTrue(t.startswith("v2:"))
        self.assertNotIn("geheim", t)
        self.assertEqual(schluessel.entschluesseln("1001", "mailpasswort", t), "geheim")

    def test_jedes_mal_anderer_nonce(self):
        self.assertNotEqual(schluessel.verschluesseln("1001", "x", "a"), schluessel.verschluesseln("1001", "x", "a"))

    def test_nicht_in_andere_beratungsstelle_oder_anderes_feld_kopierbar(self):
        t = schluessel.verschluesseln("1001", "mailpasswort", "geheim")
        for ref, zweck in (("2002", "mailpasswort"), ("1001", "mitglied-mail")):
            with self.assertRaises(schluessel.Entschluesselung):
                schluessel.entschluesseln(ref, zweck, t)

    def test_manipulation_wird_erkannt(self):
        t = schluessel.verschluesseln("1001", "x", "geheim")
        kopf, _, rest = t.rpartition(":")
        kaputt = kopf + ":" + ("A" if rest[0] != "A" else "B") + rest[1:]
        for text in (kaputt, "v1:abc:def", "", "v2:zz", t[:-4], "v2:" + "0" * 8 + ":" + rest):
            with self.assertRaises(schluessel.Entschluesselung):
                schluessel.entschluesseln("1001", "x", text)

    def test_ohne_hauptschluessel_nichts(self):
        for wert in ("", "zu-kurz-" * 3):
            with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": wert}):
                with self.assertRaises(schluessel.HauptschluesselFehlt):
                    schluessel.verschluesseln("1001", "x", "a")
                with self.assertRaises(schluessel.HauptschluesselFehlt):
                    schluessel.code_hmac("1001", "01021960")

    def test_ungueltige_beratungsstelle(self):
        for ref in ("../x", "", "a b", "x" * 65):
            with self.assertRaises(ValueError):
                schluessel.verschluesseln(ref, "x", "a")

    def test_schluesselwechsel_mit_altem_schluessel(self):
        t = schluessel.verschluesseln("1001", "x", "geheim")
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK2}):
            with self.assertRaises(schluessel.Entschluesselung):
                schluessel.entschluesseln("1001", "x", t)                       # ohne ALT nicht lesbar
            with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL_ALT": HK}):
                self.assertEqual(schluessel.entschluesseln("1001", "x", t), "geheim")
                neu = schluessel.umschluesseln("1001", "x", t)
            self.assertNotEqual(neu.split(":")[1], t.split(":")[1])
            self.assertEqual(schluessel.entschluesseln("1001", "x", neu), "geheim")   # neu lesbar ohne ALT

    def test_code(self):
        self.assertEqual(schluessel.code_aus_datum("01.02.1960"), "01021960")
        self.assertEqual(schluessel.code_aus_datum(" 31.12.2005 "), "31122005")
        for schlecht in ("31.02.2020", "1960-02-01", "01.02.60", "", None, "01.01.2999", "01.01.1899", "a", "1.2.1960 ", "01.02.1960.", 5):
            self.assertIsNone(schluessel.code_aus_datum(schlecht), schlecht)

    def test_code_hmac_je_beratungsstelle_und_pruefung(self):
        h1, k1 = schluessel.code_hmac("1001", "01021960")
        h2, _ = schluessel.code_hmac("2002", "01021960")
        self.assertNotEqual(h1, h2)
        self.assertNotIn("01021960", h1)
        self.assertTrue(schluessel.code_pruefen("1001", k1, h1, "01021960"))
        for schlecht in ("01021961", "", None, "01021960 "):
            self.assertFalse(schluessel.code_pruefen("1001", k1, h1, schlecht))
        self.assertFalse(schluessel.code_pruefen("2002", k1, h1, "01021960"))
        self.assertFalse(schluessel.code_pruefen("1001", "00000000", h1, "01021960"))
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK2, "SIGNNEXT_HAUPTSCHLUESSEL_ALT": HK}):
            self.assertTrue(schluessel.code_pruefen("1001", k1, h1, "01021960"))      # Wert vom alten Schlüssel bleibt prüfbar
            h3, k3 = schluessel.code_hmac("1001", "01021960")
            self.assertNotEqual(k3, k1)

    def test_token(self):
        t, h = schluessel.neues_token()
        self.assertGreaterEqual(len(t), 43)                                        # 32 Zufallsbytes = 256 Bit
        self.assertRegex(t, r"^[A-Za-z0-9_-]+$")
        self.assertEqual(h, hashlib.sha256(t.encode()).hexdigest())
        self.assertEqual(schluessel.token_hash(t), h)
        self.assertNotEqual(schluessel.neues_token()[0], t)


# ======================================================================================================== Mailer

class TestMailer(MailBasis):
    def z(self, srv, **kw):
        d = {"host": HOST, "port": srv.port, "benutzer": srv.benutzer, "passwort": srv.passwort, "absender_adresse": ABSENDER}
        d.update(kw)
        return d

    def nachricht(self):
        return mailer.nachricht_bauen(ABSENDER, "BSt", "mitglied@example.org", "Betreff", "Text")

    def test_tls_von_anfang_an(self):
        srv = self.starte("implicit")
        mid = mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertTrue(mid.startswith("<"))
        self.assertEqual(len(srv.nachrichten), 1)
        self.assertTrue(srv.nachrichten[0]["tls"] and srv.nachrichten[0]["angemeldet"])
        self.assertEqual(srv.nachrichten[0]["an"], ["mitglied@example.org"])

    def test_starttls_vor_der_anmeldung(self):
        srv = self.starte("starttls")
        mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertLess(srv.befehle.index("STARTTLS"), srv.befehle.index("AUTH"))
        self.assertTrue(srv.nachrichten[0]["tls"])

    def test_falscher_hostname_im_zertifikat_bricht_ab_ohne_zugangsdaten(self):
        for modus in ("implicit", "starttls"):
            srv = self.starte(modus, zert=ZERT_FALSCH)
            with self.assertRaises(mailer.MailFehler) as k:
                mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
            self.assertEqual((k.exception.code, k.exception.definitiv), ("tls_zertifikat", True), modus)
            self.assertEqual((srv.anmeldungen, srv.nachrichten), ([], []), modus)

    def test_unbekannte_zertifizierungsstelle_bricht_ab(self):
        """Mit dem echten Standardkontext (ohne Test-CA) ist das Testzertifikat nicht vertrauenswürdig."""
        for modus in ("implicit", "starttls"):
            srv = self.starte(modus)
            with mock.patch("mailer._ssl_kontext", side_effect=_echt_ssl_kontext):
                with self.assertRaises(mailer.MailFehler) as k:
                    mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
            self.assertEqual(k.exception.code, "tls_zertifikat", modus)
            self.assertEqual(srv.anmeldungen, [], modus)

    def test_standardkontext_prueft_zertifikat_und_hostname(self):
        c = _echt_ssl_kontext()
        self.assertEqual((c.verify_mode, c.check_hostname), (mailer.ssl.CERT_REQUIRED, True))
        quelle = open(mailer.__file__, encoding="utf-8").read()
        self.assertNotIn("_create_unverified_context", quelle)
        self.assertNotIn("CERT_NONE", quelle)
        self.assertNotIn("check_hostname = False", quelle)

    def test_starttls_nicht_angeboten_kein_rueckfall_auf_unverschluesselt(self):
        srv = self.starte("starttls", starttls_angeboten=False, auth_ohne_tls=True)
        with self.assertRaises(mailer.MailFehler) as k:
            mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertEqual(k.exception.code, "tls_nicht_angeboten")
        self.assertEqual((srv.anmeldungen, srv.nachrichten), ([], []))
        self.assertNotIn("AUTH", srv.befehle)

    def test_ganz_ohne_tls_auf_dem_starttls_port(self):
        srv = self.starte(None)
        with mock.patch("mailer._implizit_tls", return_value=False):
            with self.assertRaises(mailer.MailFehler) as k:
                mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertEqual(k.exception.code, "tls_nicht_angeboten")
        self.assertEqual(srv.anmeldungen, [])

    def test_falsches_passwort(self):
        srv = self.starte("implicit")
        with self.assertRaises(mailer.MailFehler) as k:
            mailer.senden(self.z(srv, passwort="falsch"), ["mitglied@example.org"], self.nachricht())
        self.assertEqual((k.exception.code, k.exception.definitiv), ("anmeldung", True))
        self.assertEqual(srv.nachrichten, [])

    def test_server_nicht_erreichbar(self):
        srv = self.starte("implicit")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        frei = s.getsockname()[1]
        s.close()
        with mock.patch("mailer.ERLAUBTE_PORTS", (frei,)):
            with self.assertRaises(mailer.MailFehler) as k:
                mailer.senden(self.z(srv, port=frei), ["mitglied@example.org"], self.nachricht())
        self.assertEqual((k.exception.code, k.exception.definitiv), ("verbindung", True))

    def test_empfaenger_abgelehnt(self):
        srv = self.starte("implicit", rcpt_ablehnen=["mitglied@example.org"])
        with self.assertRaises(mailer.MailFehler) as k:
            mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertEqual((k.exception.code, k.exception.definitiv), ("empfaenger_abgelehnt", True))
        self.assertEqual(srv.nachrichten, [])

    def test_absender_abgelehnt(self):
        srv = self.starte("implicit", mail_ablehnen=True)
        with self.assertRaises(mailer.MailFehler) as k:
            mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertEqual((k.exception.code, k.exception.definitiv), ("absender_abgelehnt", True))
        self.assertEqual(srv.nachrichten, [])

    def test_nachricht_vom_server_abgelehnt(self):
        srv = self.starte("implicit", data_antwort="554 5.7.1 rejected")
        with self.assertRaises(mailer.MailFehler) as k:
            mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertEqual((k.exception.code, k.exception.definitiv), ("abgelehnt", True))

    def test_abbruch_nach_data_ist_unklar_und_nicht_definitiv(self):
        srv = self.starte("implicit", nach_data_abbrechen=True)
        with self.assertRaises(mailer.MailFehler) as k:
            mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertEqual((k.exception.code, k.exception.definitiv), ("unklar", False))

    def test_verbindung_geht_zur_aufgeloesten_adresse_pruefung_gegen_hostnamen(self):
        srv = self.starte("implicit")
        ziele = []
        echt = mailer.socket.create_connection

        def spion(adresse, *a, **kw):
            ziele.append(adresse)
            return echt(adresse, *a, **kw)
        with mock.patch("mailer.socket.create_connection", side_effect=spion):
            mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertEqual(ziele, [("127.0.0.1", srv.port)])                          # nicht der Hostname, der erneut aufgelöst werden könnte
        self.assertEqual(len(srv.nachrichten), 1)                                   # und trotzdem passte das Zertifikat zum Hostnamen

    def test_aufloesung_nur_einmal_pro_versand(self):
        srv = self.starte("implicit")
        with mock.patch("mailer._aufloesen", return_value=["127.0.0.1"]) as a:
            mailer.senden(self.z(srv), ["mitglied@example.org"], self.nachricht())
        self.assertEqual(a.call_count, 1)

    def test_unzulaessige_ziele(self):
        """Ohne die Test-Ersetzungen: der echte Prüfweg."""
        with mock.patch("mailer.socket.create_connection") as verbinden:
            for host, port in (("localhost", 465), ("127.0.0.1", 465), ("10.0.0.5", 587), ("::1", 465), ("mail.beispiel.de", 25), ("mail.beispiel.de", 2525),
                               ("mail.beispiel.de", 0), ("mail.beispiel.de", "abc"), ("a b.example", 465), ("-x.example", 465), ("x" * 300 + ".de", 465), ("", 465),
                               (None, 465), ("kein-punkt", 465), ("mail.beispiel.de/../x", 465)):
                with self.assertRaises(ValueError, msg=(host, port)):
                    mailer.ziel_pruefen(host, port)
            for adressen in (["10.1.2.3"], ["127.0.0.1"], ["169.254.169.254"], ["192.168.0.9"], ["8.8.8.8", "10.0.0.1"], ["::1"], ["fe80::1"], [], ["0.0.0.0"], ["224.0.0.1"]):
                with mock.patch("mailer._aufloesen", return_value=adressen):
                    with self.assertRaises(ValueError, msg=adressen):
                        mailer.ziel_pruefen("mail.beispiel.de", 465)
            with mock.patch("mailer._aufloesen", side_effect=OSError("nicht gefunden")):
                with self.assertRaises(ValueError):
                    mailer.ziel_pruefen("mail.beispiel.de", 465)
            with mock.patch("mailer._aufloesen", return_value=["93.184.216.34"]):
                self.assertEqual(mailer.ziel_pruefen(" Mail.Beispiel.de. ", "587"), ("mail.beispiel.de", 587, "93.184.216.34"))
            for adressen in (["10.1.2.3"], ["127.0.0.1"]):
                with mock.patch("mailer._aufloesen", return_value=adressen):
                    with self.assertRaises(mailer.MailFehler) as k:
                        mailer.senden({"host": "mail.beispiel.de", "port": 465, "benutzer": "x", "passwort": "y", "absender_adresse": ABSENDER}, ["a@example.org"], self.nachricht())
                    self.assertEqual(k.exception.code, "konfiguration")
            verbinden.assert_not_called()

    def test_fehler_tragen_nur_einen_code(self):
        srv = self.starte("implicit")
        with self.assertRaises(mailer.MailFehler) as k:
            mailer.senden(self.z(srv, passwort="falsch-geheim-xyz"), ["mitglied@example.org"], self.nachricht())
        self.assertEqual(str(k.exception), "anmeldung")
        self.assertNotIn("falsch-geheim-xyz", repr(k.exception) + str(k.exception.args))

    def test_kopfzeilen_werden_nicht_eingeschleust(self):
        for name, betreff in (("Name\r\nBcc: boese@example.org", "x"), ("Name", "Betreff\nBcc: boese@example.org")):
            with self.assertRaises(ValueError):
                mailer.nachricht_bauen(ABSENDER, name, "a@example.org", betreff, "t")

    def test_adressen(self):
        for ok in ("a@b.de", "vorname.name@sub.example.org", "a+b@example.org"):
            self.assertTrue(mailer.adresse_gueltig(ok), ok)
        for schlecht in ("", "a", "a@b", "a b@c.de", "<a@b.de>", "a@b.de, c@d.de", "a@b..de", "a@-b.de", "x" * 70 + "@b.de", None, "a@b.de\r\nBcc: x"):
            self.assertFalse(mailer.adresse_gueltig(schlecht), schlecht)


# ======================================================================================================== Mail-Zugang (Routen)

class TestMailZugang(MailBasis):
    def test_anlegen_und_lesen_ohne_passwort(self):
        srv = self.starte("implicit")
        r = self.zugang(srv)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        g = self.c.get("/api/v1/mail-zugang")
        j = g.get_json()
        self.assertEqual((j["konfiguriert"], j["host"], j["port"], j["benutzer"], j["absender_adresse"], j["passwort_gesetzt"], j["bcc_an_absender"]),
                         (True, HOST, srv.port, srv.benutzer, ABSENDER, True, False))
        for antwort in (r, g):
            self.assertNotIn(PW, antwort.get_data(as_text=True))
            self.assertNotIn("passwort", {k for k in antwort.get_json() if k != "passwort_gesetzt"})
            self.assertNotIn("passwort_enc", antwort.get_json())

    def test_passwort_nirgends_im_klartext(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        self.c.post("/api/v1/mail-zugang/pruefen")
        self.assertNotIn(PW.encode(), self.db_bytes())
        self.assertIn(b"v2:", self.db_bytes())
        self.assertNotIn(PW, self.db_text())
        self.assertNotIn(PW, self.logtext.getvalue())
        self.assertNotIn(PW, json.dumps(self.audit_zeilen()))

    def test_passwort_nicht_in_fehlerantworten(self):
        srv = self.starte("implicit")
        for kaputt in ({"host": "10.0.0.5"}, {"port": 25}, {"absender_adresse": "kaputt"}, {"signatur": "x" * 2000}, {"unbekannt": 1}):
            r = self.zugang(srv, **kaputt)
            self.assertEqual(r.status_code, 422, kaputt)
            self.assertNotIn(PW, r.get_data(as_text=True))
        r = self.zugang(srv, passwort="falsch")
        self.assertNotIn(PW, r.get_data(as_text=True))
        self.assertEqual(self.c.post("/api/v1/mail-zugang/pruefen").status_code, 502)
        self.assertNotIn("falsch", self.logtext.getvalue().replace("falschem", ""))

    def test_eingabepruefung(self):
        srv = self.starte("implicit")
        for kaputt, feld in (({"host": "10.0.0.5"}, "host"), ({"host": "127.0.0.1"}, "host"), ({"host": "localhost"}, "host"), ({"port": 25}, "host"), ({"host": ""}, "host"),
                             ({"benutzer": ""}, "benutzer"), ({"benutzer": "a\nb"}, "benutzer"), ({"absender_adresse": "x"}, "absender_adresse"),
                             ({"absender_name": "Name\r\nBcc: x@example.org"}, "absender_name"), ({"absender_name": "<Name>"}, "absender_name"),
                             ({"absender_name": "x" * 101}, "absender_name"), ({"signatur": "x" * 1001}, "signatur"), ({"passwort": ""}, "passwort"),
                             ({"passwort": "a\nb"}, "passwort"), ({"passwort": 5}, "passwort"), ({"bcc_an_absender": "ja"}, "bcc_an_absender")):
            r = self.zugang(srv, **kaputt)
            self.assertEqual(r.status_code, 422, kaputt)
            if feld:
                self.assertEqual(r.get_json().get("feld"), feld, kaputt)
        self.assertFalse(self.c.get("/api/v1/mail-zugang").get_json()["konfiguriert"])
        self.assertEqual(self.c.put("/api/v1/mail-zugang", json=["x"]).status_code, 422)
        self.assertEqual(self.c.put("/api/v1/mail-zugang", data="kein json", content_type="application/json").status_code, 422)

    def test_ohne_passwort_beim_anlegen(self):
        srv = self.starte("implicit")
        r = self.zugang(srv)
        self.assertEqual(r.status_code, 200)
        self.c.delete("/api/v1/mail-zugang")
        daten = {"host": HOST, "port": srv.port, "benutzer": "x", "absender_adresse": ABSENDER}
        r = self.c.put("/api/v1/mail-zugang", json=daten)
        self.assertEqual((r.status_code, r.get_json().get("feld")), (422, "passwort"))

    def test_aendern_ohne_passwort_behaelt_das_alte_und_hebt_die_pruefung_auf(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        self.assertEqual(self.c.post("/api/v1/mail-zugang/pruefen").status_code, 200)
        self.assertTrue(self.c.get("/api/v1/mail-zugang").get_json()["geprueft_am"])
        r = self.c.put("/api/v1/mail-zugang", json={"signatur": "Neue Signatur"})
        self.assertEqual(r.status_code, 200)
        j = self.c.get("/api/v1/mail-zugang").get_json()
        self.assertEqual((j["signatur"], j["geprueft_am"], j["host"]), ("Neue Signatur", None, HOST))
        self.assertEqual(mail_zugang.fuer_dienst("1001", "test", "x")["passwort"], PW)

    def test_pruefen_schickt_nur_an_die_beratungsstelle_selbst(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        r = self.c.post("/api/v1/mail-zugang/pruefen")
        self.assertEqual((r.status_code, r.get_json()["ok"]), (200, True))
        self.assertEqual([m["an"] for m in srv.nachrichten], [[ABSENDER]])
        self.assertTrue(self.c.get("/api/v1/mail-zugang").get_json()["geprueft_am"])

    def test_pruefen_fehler_ohne_einzelheiten(self):
        srv = self.starte("implicit")
        self.zugang(srv, passwort="falsch-1")
        r = self.c.post("/api/v1/mail-zugang/pruefen")
        self.assertEqual((r.status_code, r.get_json()["error"], r.get_json()["ok"]), (502, "mail_anmeldung", False))
        self.assertIsNone(self.c.get("/api/v1/mail-zugang").get_json()["geprueft_am"])
        self.assertNotIn("falsch-1", r.get_data(as_text=True) + self.logtext.getvalue() + json.dumps(self.audit_zeilen()))
        self.c.delete("/api/v1/mail-zugang")
        r = self.c.post("/api/v1/mail-zugang/pruefen")
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "mail_zugang_fehlt"))

    def test_zertifikatsfehler_beim_pruefen(self):
        srv = self.starte("starttls", zert=ZERT_FALSCH)
        self.zugang(srv)
        r = self.c.post("/api/v1/mail-zugang/pruefen")
        self.assertEqual((r.status_code, r.get_json()["error"]), (502, "mail_tls_zertifikat"))
        self.assertEqual(srv.anmeldungen, [])

    def test_loeschen(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        self.assertEqual(self.c.delete("/api/v1/mail-zugang").status_code, 200)
        self.assertFalse(self.c.get("/api/v1/mail-zugang").get_json()["konfiguriert"])
        self.assertEqual(self.c.delete("/api/v1/mail-zugang").status_code, 404)

    def test_nur_admin_aendert(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        self.als("tm", me(email="m@test.example", rolle="mitarbeiter", ref="1001"))
        self.assertEqual(self.c.get("/api/v1/mail-zugang").status_code, 200)
        for r in (self.zugang(srv), self.c.delete("/api/v1/mail-zugang"), self.c.post("/api/v1/mail-zugang/pruefen")):
            self.assertEqual((r.status_code, r.get_json()["grund"]), (403, "rolle_nicht_erlaubt"))
        self.assertTrue(self.c.get("/api/v1/mail-zugang").get_json()["konfiguriert"])
        self.assertTrue(self.audit_zeilen("zugriff_verweigert"))

    def test_mehrere_beratungsstellen_fail_closed(self):
        srv = self.starte("implicit")
        stellen = [{"stelle": {"id": 1}, "produkte": [{"produkt": "signnext", "externe_referenz": "1001"}]}, {"stelle": {"id": 2}, "produkte": [{"produkt": "booknext", "externe_referenz": "7"}]}]
        self.als("tz", me(ref="1001", stellen=stellen))
        for r in (self.zugang(srv), self.c.delete("/api/v1/mail-zugang"), self.c.post("/api/v1/mail-zugang/pruefen")):
            self.assertEqual((r.status_code, r.get_json()["grund"]), (403, "mehrere_beratungsstellen"))
        self.assertEqual(self.c.get("/api/v1/mail-zugang").status_code, 200)             # lesen und alles andere bleibt möglich
        self.als("te", me(ref="1001", stellen=stellen[:1]))
        self.assertEqual(self.zugang(srv).status_code, 200)

    def test_variante_mit_stelle_rolle(self):
        srv = self.starte("implicit")
        def stellen(rolle):
            st = {"stelle": {"id": 1}, "produkte": [{"produkt": "signnext", "externe_referenz": "1001"}]}
            if rolle:
                st["stelle_rolle"] = rolle
            return [st, {"stelle": {"id": 2}, "produkte": []}]
        with mock.patch.dict(os.environ, {"SIGNNEXT_STELLE_ROLLE": "an"}):
            self.als("t1", me(ref="1001", rolle="mitarbeiter", stellen=stellen("admin")))
            self.assertEqual(self.zugang(srv).status_code, 200)                           # Rolle je Stelle zählt, auch bei mehreren Stellen
            for rolle, grund in (("mitarbeiter", "rolle_nicht_erlaubt"), (None, "rolle_nicht_erlaubt"), ("root", "rolle_nicht_erlaubt")):
                self.als("t2", me(ref="1001", rolle="bst_admin", stellen=stellen(rolle)))
                r = self.zugang(srv)
                self.assertEqual((r.status_code, r.get_json()["grund"]), (403, grund), rolle)
            self.als("t3", me(ref="1001", rolle="bst_admin"))                             # kein stellen-Feld: fail closed
            self.assertEqual(self.zugang(srv).status_code, 403)
        self.als("t4", me(ref="1001", rolle="bst_admin", stellen=stellen("mitarbeiter")[:1]))   # Voreinstellung 'aus': Rolle des Nutzers
        self.assertEqual(self.zugang(srv).status_code, 200)

    def test_fremder_mandant(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertFalse(self.c.get("/api/v1/mail-zugang").get_json()["konfiguriert"])
        self.assertEqual(self.c.delete("/api/v1/mail-zugang").status_code, 404)
        r = self.c.post("/api/v1/mail-zugang/pruefen")
        self.assertEqual(r.get_json()["error"], "mail_zugang_fehlt")
        self.zugang(srv, absender_adresse="b@example.org")
        self.als("ta", me(ref="1001"))
        self.assertEqual(self.c.get("/api/v1/mail-zugang").get_json()["absender_adresse"], ABSENDER)
        with self.assertRaises(schluessel.Entschluesselung):                             # das Passwort von 1001 lässt sich unter 2002 nicht lesen
            with db.verbindung() as c:
                enc = c.execute("SELECT passwort_enc FROM mail_zugang WHERE mandant_ref='1001'").fetchone()[0]
            schluessel.entschluesseln("2002", "mailpasswort", enc)

    def test_ohne_anmeldung(self):
        self.c.delete_cookie("bst_session", domain="localhost")
        for r in (self.c.get("/api/v1/mail-zugang"), self.c.put("/api/v1/mail-zugang", json={}), self.c.delete("/api/v1/mail-zugang"), self.c.post("/api/v1/mail-zugang/pruefen")):
            self.assertEqual(r.status_code, 401)

    def test_ohne_hauptschluessel_fail_closed(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": ""}):
            self.assertEqual(self.zugang(srv).status_code, 503)
            self.assertEqual(self.c.post("/api/v1/mail-zugang/pruefen").status_code, 503)
            self.assertEqual(srv.nachrichten, [])

    def test_lesezugriffe_werden_protokolliert(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        self.c.post("/api/v1/mail-zugang/pruefen")
        z = self.audit_zeilen("mailzugang_gelesen")
        self.assertEqual([x["details"] for x in z], ["zweck=test"])
        self.assertEqual({x["aktion"] for x in self.audit_zeilen()} >= {"mailzugang_gesetzt", "mailzugang_gelesen", "mailzugang_geprueft"}, True)

    def test_loeschung_der_beratungsstelle_entfernt_den_zugang(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        self.assertEqual(db.mandant_loeschen("1001")["mail_zugang"], 1)
        self.assertFalse(self.c.get("/api/v1/mail-zugang").get_json()["konfiguriert"])

    def test_schluesselwechsel_fuer_mail_passwort(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK2, "SIGNNEXT_HAUPTSCHLUESSEL_ALT": HK}):
            self.assertEqual(schluesselwechsel.wechseln(), {"umgeschluesselt": 1, "schon_aktuell": 0})
            self.assertEqual(schluesselwechsel.wechseln(), {"umgeschluesselt": 0, "schon_aktuell": 1})
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK2}):
            self.assertEqual(mail_zugang.fuer_dienst("1001", "test", "x")["passwort"], PW)       # ohne ALT lesbar
            self.assertEqual(self.c.post("/api/v1/mail-zugang/pruefen").status_code, 200)

    def test_schluesselwechsel_ohne_alten_schluessel_bricht_ab(self):
        with self.assertRaises(schluessel.HauptschluesselFehlt):
            schluesselwechsel.wechseln()


# ======================================================================================================== Prüfansicht und Angaben

class TestPruefansicht(MailBasis):
    def test_a_und_b(self):
        vid = self.bereit()
        r = self.c.get("/api/v1/vorgaenge/%d/pruefung" % vid)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual(j["name"], {"wert": "Quentin Zzyzx", "herkunft": "pdf"})
        self.assertEqual((j["geburtsdatum"]["wert"], j["geburtsdatum"]["herkunft"], j["geburtsdatum"]["gesetzt"]), (pdfs.DOB, "pdf", True))
        self.assertEqual((j["email"]["wert"], j["email"]["herkunft"]), (pdfs.MAIL, "pdf"))
        self.assertEqual((j["personen"]["wert"], j["personen"]["herkunft"]), (2, "pdf"))
        self.assertEqual((j["status"], j["fehlend"], j["versandbereit_pdf"], j["abgleich"]["status"]), ("bereit", [], True, "automatisch"))
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_nur_a_braucht_mail_und_personenzahl(self):
        j = self.c.get("/api/v1/vorgaenge/%d/pruefung" % self.bereit(b=False)).get_json()
        self.assertEqual((j["email"]["herkunft"], j["personen"]["herkunft"], j["fehlend"]), ("fehlt", "fehlt", ["email", "personen"]))
        self.assertEqual(j["geburtsdatum"]["herkunft"], "pdf")
        self.assertEqual(j["name"]["wert"], "Quentin Zzyzx")

    def test_nur_b_braucht_das_geburtsdatum(self):
        j = self.c.get("/api/v1/vorgaenge/%d/pruefung" % self.bereit(a=False)).get_json()
        self.assertEqual((j["geburtsdatum"]["herkunft"], j["geburtsdatum"]["gesetzt"], j["fehlend"]), ("fehlt", False, ["geburtsdatum"]))
        self.assertEqual((j["email"]["wert"], j["personen"]["wert"]), (pdfs.MAIL, 2))

    def test_ohne_weisse_zeile_fehlt_die_adresse(self):
        j = self.c.get("/api/v1/vorgaenge/%d/pruefung" % self.bereit(weisse_zeile=None)).get_json()
        self.assertEqual((j["email"]["herkunft"], "email" in j["fehlend"]), ("fehlt", True))

    def test_nichts_davon_wird_gespeichert(self):
        vid = self.bereit()
        self.c.get("/api/v1/vorgaenge/%d/pruefung" % vid)
        gesamt = self.db_text() + self.logtext.getvalue() + json.dumps(self.audit_zeilen())
        for wert in pdfs.PERSONENDATEN:
            self.assertNotIn(wert, gesamt, wert)
        z = self.audit_zeilen("pruefansicht_gelesen")
        self.assertEqual([x["details"] for x in z], ["id=%d" % vid])

    def test_fremder_mandant_und_unbekannt(self):
        vid = self.bereit()
        for url in ("/api/v1/vorgaenge/%d/pruefung", "/api/v1/vorgaenge/%d/dokument"):
            self.als("tb", me(email="b@test.example", ref="2002"))
            self.assertEqual(self.c.get(url % vid).status_code, 404)
            self.assertEqual(self.c.get(url % 99999).status_code, 404)
            self.c.delete_cookie("bst_session", domain="localhost")
            self.assertEqual(self.c.get(url % vid).status_code, 401)
            self.als("ta", me(ref="1001"))
            self.assertEqual(self.c.get(url % vid).status_code, 200)

    def test_dokument_zum_ansehen(self):
        vid = self.bereit()
        r = self.c.get("/api/v1/vorgaenge/%d/dokument" % vid)
        self.assertEqual((r.status_code, r.mimetype), (200, "application/pdf"))
        self.assertTrue(r.data.startswith(b"%PDF-"))
        self.assertTrue(r.headers["Content-Disposition"].startswith("inline"))
        self.assertIn("sandbox", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.data, db.dokument_bytes("1001", vid, "B"))                      # ohne Angabe das erste Dokument (Anschreiben)
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/dokument?dokument=A" % vid).data, db.dokument_bytes("1001", vid, "A"))
        einzeln = self.bereit(b=False)
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/dokument" % einzeln).data, db.dokument_bytes("1001", einzeln, "A"))
        self.assertTrue(self.audit_zeilen("dokument_gelesen"))

    def test_dokument_vor_der_bestaetigung(self):
        a = pdfs.a_pdf(stnr="999/9999/9999", id_a="11111111111", id_b="22222222222", mnr="7654321")
        vid = hochladen(self.c, pdfs.b_pdf(), a).get_json()["vorgang_id"]
        r = self.c.get("/api/v1/vorgaenge/%d/dokument" % vid)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, db.dokument_bytes("1001", vid, "B"))                        # noch kein zusammengeführtes PDF


class TestAngaben(MailBasis):
    def put(self, vid, **kw):
        return self.c.put("/api/v1/vorgaenge/%d/angaben" % vid, json=kw)

    def ansicht(self, vid):
        return self.c.get("/api/v1/vorgaenge/%d/pruefung" % vid).get_json()

    def test_nur_a_manuell_ergaenzen(self):
        vid = self.bereit(b=False)
        r = self.put(vid, email="Mitglied.Neu@Example.org", personen=2)
        self.assertEqual((r.status_code, r.get_json()), (200, {"gesetzt": ["email", "personen"]}))
        j = self.ansicht(vid)
        self.assertEqual((j["email"]["wert"], j["email"]["herkunft"], j["personen"]["wert"], j["personen"]["herkunft"], j["fehlend"]), ("mitglied.neu@example.org", "manuell", 2, "manuell", []))
        self.assertTrue(j["versandbereit_pdf"])

    def test_mailadresse_nur_verschluesselt_gespeichert(self):
        vid = self.bereit(b=False)
        self.put(vid, email="mitglied.neu@example.org", personen=1)
        gesamt = self.db_text() + self.db_bytes().decode("latin-1") + self.logtext.getvalue() + json.dumps(self.audit_zeilen())
        self.assertNotIn("mitglied.neu@example.org", gesamt)
        v = db.vorgang_intern("1001", vid)
        self.assertTrue(v["email_enc"].startswith("v2:"))
        self.assertEqual(schluessel.entschluesseln("1001", "mitglied-mail", v["email_enc"]), "mitglied.neu@example.org")
        z = self.audit_zeilen("angaben_gesetzt")
        self.assertEqual([x["details"] for x in z], ["id=%d felder=email,personen" % vid])

    def test_geburtsdatum_nur_als_hmac(self):
        vid = self.bereit(a=False)
        self.assertEqual(self.put(vid, geburtsdatum="03.04.1962").status_code, 200)
        j = self.ansicht(vid)
        self.assertEqual((j["geburtsdatum"]["wert"], j["geburtsdatum"]["herkunft"], j["geburtsdatum"]["gesetzt"], j["fehlend"]), (None, "manuell", True, []))
        v = db.vorgang_intern("1001", vid)
        self.assertEqual((v["code_hmac"], v["code_kid"]), schluessel.code_hmac("1001", "03041962"))
        gesamt = self.db_text() + self.db_bytes().decode("latin-1") + self.logtext.getvalue() + json.dumps(self.audit_zeilen())
        for wert in ("03.04.1962", "030462", "1962-04-03"):
            self.assertNotIn(wert, gesamt, wert)

    def test_herkunft_gleich_abweichend_zuruecknehmen(self):
        vid = self.bereit()
        self.assertEqual(self.put(vid, email=pdfs.MAIL, geburtsdatum=pdfs.DOB, personen=2).status_code, 200)         # gleich wie im PDF
        v = db.vorgang_intern("1001", vid)
        self.assertEqual((v["email_enc"], v["code_hmac"], v["email_herkunft"], v["datum_herkunft"], v["personen_herkunft"]), (None, None, None, None, None))
        self.put(vid, email="andere@example.org", geburtsdatum="05.06.1970", personen=1)
        j = self.ansicht(vid)
        self.assertEqual((j["email"]["herkunft"], j["geburtsdatum"]["herkunft"], j["personen"]["herkunft"], j["personen"]["wert"]), ("abweichend", "abweichend", "abweichend", 1))
        self.assertEqual((j["email"]["pdf_wert"], j["geburtsdatum"]["pdf_wert"], j["personen"]["pdf_wert"]), (pdfs.MAIL, pdfs.DOB, 2))
        self.assertIsNone(j["geburtsdatum"]["wert"])
        self.put(vid, email=None, geburtsdatum=None, personen=None)
        j = self.ansicht(vid)
        self.assertEqual((j["email"]["wert"], j["email"]["herkunft"], j["geburtsdatum"]["wert"], j["geburtsdatum"]["herkunft"], j["personen"]["wert"], j["personen"]["herkunft"]),
                         (pdfs.MAIL, "pdf", pdfs.DOB, "pdf", 2, "pdf"))

    def test_ungueltige_eingaben(self):
        vid = self.bereit(b=False)
        for kaputt, feld in (({"email": "x"}, "email"), ({"email": "a b@c.de"}, "email"), ({"email": 5}, "email"), ({"email": "a@b.de\r\nBcc: x@y.de"}, "email"),
                             ({"geburtsdatum": "31.02.2020"}, "geburtsdatum"), ({"geburtsdatum": "1960-02-01"}, "geburtsdatum"), ({"geburtsdatum": "01.01.2999"}, "geburtsdatum"),
                             ({"geburtsdatum": "01.01.1899"}, "geburtsdatum"), ({"geburtsdatum": 12}, "geburtsdatum"), ({"personen": 3}, "personen"), ({"personen": 0}, "personen"),
                             ({"personen": True}, "personen"), ({"personen": "2"}, "personen")):
            r = self.put(vid, **kaputt)
            self.assertEqual((r.status_code, r.get_json().get("feld")), (422, feld), kaputt)
        for body in ({}, {"unbekannt": 1}, {"email": "a@b.de", "name": "x"}, [], "x", None):
            r = self.c.put("/api/v1/vorgaenge/%d/angaben" % vid, json=body)
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual(self.c.put("/api/v1/vorgaenge/%d/angaben" % vid, data="kein json", content_type="application/json").status_code, 400)
        self.assertEqual(db.vorgang_intern("1001", vid)["email_enc"], None)

    def test_fremder_mandant_und_ohne_anmeldung(self):
        vid = self.bereit(b=False)
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.put(vid, personen=1).status_code, 404)
        self.assertIsNone(db.vorgang_intern("1001", vid)["personen"])
        self.c.delete_cookie("bst_session", domain="localhost")
        self.assertEqual(self.put(vid, personen=1).status_code, 401)

    def test_vor_der_bestaetigung_des_abgleichs_moeglich(self):
        a = pdfs.a_pdf(stnr="999/9999/9999", id_a="11111111111", id_b="22222222222", mnr="7654321")
        vid = hochladen(self.c, pdfs.b_pdf(), a).get_json()["vorgang_id"]
        self.assertEqual(self.put(vid, personen=1).status_code, 200)

    def test_nach_dem_versand_gesperrt(self):
        srv = self.starte("implicit")
        self.zugang(srv)
        self.markiere_geprueft()
        vid = self.bereit()
        self.assertEqual(self.senden_ok(vid).status_code, 200)
        self.assertEqual(self.put(vid, personen=1).status_code, 409)

    def test_ohne_hauptschluessel(self):
        vid = self.bereit(b=False)
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": ""}):
            self.assertEqual(self.put(vid, email="a@example.org").status_code, 503)
            self.assertEqual(self.put(vid, geburtsdatum="03.04.1962").status_code, 503)
        self.assertEqual(self.put(vid, personen=1).status_code, 200)                    # braucht keinen Schlüssel


# ======================================================================================================== Versand

class TestVersand(MailBasis):
    def vorbereiten(self, tls="implicit", **kw):
        srv = self.starte(tls, **kw)
        self.assertEqual(self.zugang(srv).status_code, 200)
        self.markiere_geprueft()
        return srv

    def link_zeilen(self):
        with db.verbindung() as c:
            return [dict(z) for z in c.execute("SELECT * FROM link")]

    def test_versand_ende_zu_ende(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        r = self.senden_ok(vid)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual((j["vorgang_id"], j["status"]), (vid, "versendet"))
        self.assertEqual(len(srv.nachrichten), 1)
        n = srv.nachrichten[0]
        self.assertEqual((n["an"], n["von"].lower().strip("<>").split(":")[-1].strip("<> "), n["angemeldet"], n["tls"]), ([pdfs.MAIL], ABSENDER, True, True))
        m, text = self.mail_text(n)
        self.assertEqual(m["Subject"], versand.BETREFF_MEHRERE)                                # zwei Dokumente
        self.assertEqual(m["From"], "Beratungsstelle Beispiel <%s>" % ABSENDER)
        self.assertIsNone(m["Bcc"])
        token = re.search(r"https://signnext\.example/u#([A-Za-z0-9_-]+)", text).group(1)
        self.assertGreaterEqual(len(token), 43)
        # Betreff und Text enthalten nichts über die Person oder die Steuer
        for wert in pdfs.PERSONENDATEN + ("01021960", "Steuer", "Erklärung", "Gesendet-Ordner"):
            self.assertNotIn(wert, m["Subject"] + text, wert)
        self.assertIn("Geburtsdatum", text)
        self.assertIn("acht Ziffern", text)
        self.assertIn("Ihre Beratungsstelle Beispiel", text)
        # gespeichert ist nur der Hash des Tokens, 7 Tage gültig, einmalig
        zeilen = self.link_zeilen()
        self.assertEqual(len(zeilen), 1)
        self.assertEqual(zeilen[0]["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        ablauf = datetime.strptime(zeilen[0]["laeuft_ab"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertAlmostEqual((ablauf - datetime.now(timezone.utc)).total_seconds(), 7 * 86400, delta=120)
        self.assertEqual((zeilen[0]["verbraucht_am"], zeilen[0]["fehlversuche"], zeilen[0]["gesperrt"]), (None, 0, 0))
        # Vorgang
        v = db.vorgang_intern("1001", vid)
        self.assertEqual((v["status"], v["email_herkunft"], v["datum_herkunft"], v["personen"], v["personen_herkunft"]), ("versendet", "pdf", "pdf", 2, "pdf"))
        self.assertTrue(v["versendet_am"])
        self.assertEqual((v["code_hmac"], v["code_kid"]), schluessel.code_hmac("1001", "01021960"))
        self.assertEqual(schluessel.entschluesseln("1001", "mitglied-mail", v["email_enc"]), pdfs.MAIL)
        with db.verbindung() as c:
            vz = [dict(z) for z in c.execute("SELECT * FROM versand")]
        self.assertEqual([(x["ergebnis"], x["mandant_ref"]) for x in vz], [("gesendet", "1001")])
        self.assertTrue(vz[0]["message_id"].startswith("<"))

    def test_token_und_geheimnisse_nirgends_ausser_in_der_mail(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        r = self.senden_ok(vid)
        _, text = self.mail_text(srv.nachrichten[0])
        token = re.search(r"/u#([A-Za-z0-9_-]+)", text).group(1)
        antworten = r.get_data(as_text=True) + self.c.get("/api/v1/vorgaenge/%d" % vid).get_data(as_text=True) + self.c.get("/api/v1/vorgaenge/%d/pruefung" % vid).get_data(as_text=True)
        gesamt = antworten + self.db_text() + self.db_bytes().decode("latin-1") + self.logtext.getvalue() + json.dumps(self.audit_zeilen())
        for geheim in (token, PW, "01021960", "/u#"):
            self.assertNotIn(geheim, gesamt, geheim)
        intern = self.db_text() + self.db_bytes().decode("latin-1") + self.logtext.getvalue() + json.dumps(self.audit_zeilen())
        self.assertNotIn(pdfs.DOB, intern)                                  # die Prüfansicht zeigt das Datum aus dem PDF an, gespeichert wird es nie
        for wert in (pdfs.MAIL, pdfs.NAME, pdfs.VORNAME, pdfs.ID_A, pdfs.STNR):
            self.assertNotIn(wert, self.db_text() + self.logtext.getvalue() + json.dumps(self.audit_zeilen()), wert)
        self.assertEqual([x["details"] for x in self.audit_zeilen("versendet")], ["id=%d" % vid])

    def test_vorgang_antwort_enthaelt_keine_geheimnisse(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        self.senden_ok(vid)
        j = self.c.get("/api/v1/vorgaenge/%d" % vid).get_json()
        self.assertEqual(j["status"], "versendet")
        for verboten in ("email_enc", "code_hmac", "code_kid", "token", "token_hash", "passwort", "passwort_enc"):
            self.assertNotIn(verboten, j)
        self.assertNotIn("v2:", json.dumps(j))
        self.assertNotIn("v2:", self.c.get("/api/v1/vorgaenge").get_data(as_text=True))

    def test_ohne_ausdrueckliche_bestaetigung_wird_nichts_gesendet(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        url = "/api/v1/vorgaenge/%d/versenden" % vid
        for body in (None, {}, {"bestaetigt": False}, {"bestaetigt": "ja"}, {"bestaetigt": 1}, [True]):
            r = self.c.post(url, json=body) if body is not None else self.c.post(url)
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual((srv.nachrichten, srv.anmeldungen), ([], []))
        self.assertEqual(db.vorgang_intern("1001", vid)["status"], "bereit")

    def test_zweiter_versand_wird_abgelehnt(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        self.assertEqual(self.senden_ok(vid).status_code, 200)
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "schon_versendet"))
        self.assertEqual(len(srv.nachrichten), 1)
        self.assertEqual(len(self.link_zeilen()), 1)

    def test_gleichzeitiger_doppelklick_sendet_nur_einmal(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        drinnen, weiter = threading.Event(), threading.Event()
        echt = mailer.senden

        def langsam(*a, **kw):
            drinnen.set()
            weiter.wait(10)
            return echt(*a, **kw)
        ergebnis = {}
        zweiter = self.app.test_client()
        zweiter.set_cookie("bst_session", "ta", domain="localhost")
        with mock.patch("mailer.senden", side_effect=langsam):
            t = threading.Thread(target=lambda: ergebnis.update(erster=self.senden_ok(vid)))
            t.start()
            self.assertTrue(drinnen.wait(10))
            r2 = zweiter.post("/api/v1/vorgaenge/%d/versenden" % vid, json={"bestaetigt": True})
            self.assertEqual((r2.status_code, r2.get_json()["error"]), (409, "versand_laeuft_oder_unklar"))
            weiter.set()
            t.join(15)
        self.assertEqual(ergebnis["erster"].status_code, 200)
        self.assertEqual(len(srv.nachrichten), 1)
        self.assertEqual(len(self.link_zeilen()), 1)

    def test_voraussetzungen(self):
        vid = self.bereit()
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "mail_zugang_fehlt"))
        srv = self.vorbereiten()
        with mock.patch.dict(os.environ, {"SIGNNEXT_BASIS_URL": ""}):
            self.assertEqual((self.senden_ok(vid).status_code, self.senden_ok(vid).get_json()["error"]), (503, "basis_url_fehlt"))
        with mock.patch.dict(os.environ, {"SIGNNEXT_BASIS_URL": "http://signnext.example"}):
            self.assertEqual(self.senden_ok(vid).status_code, 503)                                     # nur https
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": ""}):
            self.assertEqual(self.senden_ok(vid).get_json()["error"], "nicht_bereit")
        self.assertEqual((srv.nachrichten, srv.anmeldungen), ([], []))
        self.assertEqual(db.vorgang_intern("1001", vid)["status"], "bereit")
        self.assertEqual(self.link_zeilen(), [])

    def test_angaben_fehlen(self):
        srv = self.vorbereiten()
        vid = self.bereit(b=False)
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"], r.get_json()["fehlend"]), (409, "angaben_fehlen", ["email", "personen"]))
        self.assertEqual((srv.nachrichten, self.link_zeilen()), ([], []))
        self.assertEqual(db.vorgang_intern("1001", vid)["status"], "bereit")

    def test_nur_a_mit_manuellen_angaben(self):
        srv = self.vorbereiten()
        vid = self.bereit(b=False)
        self.c.put("/api/v1/vorgaenge/%d/angaben" % vid, json={"email": "mitglied.neu@example.org", "personen": 1})
        self.assertEqual(self.senden_ok(vid).status_code, 200)
        self.assertEqual(srv.nachrichten[0]["an"], ["mitglied.neu@example.org"])
        v = db.vorgang_intern("1001", vid)
        self.assertEqual((v["email_herkunft"], v["datum_herkunft"], v["personen"], v["personen_herkunft"]), ("manuell", "pdf", 1, "manuell"))

    def test_nur_b_mit_manuellem_datum(self):
        srv = self.vorbereiten()
        vid = self.bereit(a=False)
        self.assertEqual(self.senden_ok(vid).status_code, 409)
        self.c.put("/api/v1/vorgaenge/%d/angaben" % vid, json={"geburtsdatum": "03.04.1962"})
        self.assertEqual(self.senden_ok(vid).status_code, 200)
        v = db.vorgang_intern("1001", vid)
        self.assertEqual((v["code_hmac"], v["datum_herkunft"]), (schluessel.code_hmac("1001", "03041962")[0], "manuell"))

    def test_zustand_bestaetigung_noetig(self):
        srv = self.vorbereiten()
        a = pdfs.a_pdf(stnr="999/9999/9999", id_a="11111111111", id_b="22222222222", mnr="7654321")
        vid = hochladen(self.c, pdfs.b_pdf(), a).get_json()["vorgang_id"]
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "zustand_passt_nicht"))
        self.assertEqual(srv.nachrichten, [])
        self.c.post("/api/v1/vorgaenge/%d/abgleich-bestaetigen" % vid, json={"bestaetigt": True})
        self.assertEqual(self.senden_ok(vid).status_code, 200)

    def test_kopie_an_die_beratungsstelle_nur_auf_wunsch(self):
        srv = self.vorbereiten()
        self.senden_ok(self.bereit())
        self.assertEqual(srv.nachrichten[0]["an"], [pdfs.MAIL])                                      # Voreinstellung: aus
        self.assertFalse(self.c.get("/api/v1/mail-zugang").get_json()["bcc_an_absender"])
        self.c.put("/api/v1/mail-zugang", json={"bcc_an_absender": True})
        self.markiere_geprueft()
        self.senden_ok(self.bereit())
        n = srv.nachrichten[1]
        self.assertEqual(n["an"], [pdfs.MAIL, ABSENDER])
        m, _ = self.mail_text(n)
        self.assertIsNone(m["Bcc"])
        self.assertNotIn(ABSENDER, m["To"])

    def test_signatur_nur_wenn_gesetzt(self):
        srv = self.vorbereiten()
        self.c.put("/api/v1/mail-zugang", json={"signatur": ""})
        self.markiere_geprueft()
        self.senden_ok(self.bereit())
        _, text = self.mail_text(srv.nachrichten[0])
        self.assertNotIn("-- \n", text)
        self.assertTrue(text.rstrip().endswith("Ihre Beratungsstelle."))

    def test_mitarbeiter_duerfen_senden(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        self.als("tm", me(email="m@test.example", rolle="mitarbeiter", ref="1001"))
        self.assertEqual(self.senden_ok(vid).status_code, 200)

    def test_fremder_mandant_und_ohne_anmeldung(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        self.als("tb", me(email="b@test.example", ref="2002"))
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (404, "nicht_gefunden"))
        self.c.delete_cookie("bst_session", domain="localhost")
        self.assertEqual(self.senden_ok(vid).status_code, 401)
        self.assertEqual((srv.nachrichten, self.link_zeilen()), ([], []))

    def test_jeder_link_ist_neu(self):
        srv = self.vorbereiten()
        self.senden_ok(self.bereit())
        self.senden_ok(self.bereit())
        tokens = [re.search(r"/u#([A-Za-z0-9_-]+)", self.mail_text(n)[1]).group(1) for n in srv.nachrichten]
        self.assertEqual(len(set(tokens)), 2)

    def test_eindeutiger_fehler_vor_der_zustellung_erlaubt_neuen_versuch(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        self.c.put("/api/v1/mail-zugang", json={"passwort": "falsches-Passwort-1"})
        self.markiere_geprueft()
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (502, "mail_anmeldung"))
        self.assertEqual(db.vorgang_intern("1001", vid)["status"], "bereit")
        self.assertEqual(db.vorgang_intern("1001", vid)["versand_fehler"], "anmeldung")
        self.assertEqual((self.link_zeilen(), srv.nachrichten), ([], []))                              # Link verworfen, nichts zugestellt
        with db.verbindung() as c:
            self.assertEqual([z["ergebnis"] for z in c.execute("SELECT ergebnis FROM versand")], ["fehlgeschlagen"])
        self.c.put("/api/v1/mail-zugang", json={"passwort": PW})
        self.markiere_geprueft()
        self.assertEqual(self.senden_ok(vid).status_code, 200)
        self.assertEqual(len(srv.nachrichten), 1)
        self.assertNotIn("falsches-Passwort-1", r.get_data(as_text=True) + self.logtext.getvalue() + self.db_text())

    def test_empfaenger_abgelehnt_und_nachricht_abgelehnt(self):
        srv = self.vorbereiten(rcpt_ablehnen=[pdfs.MAIL])
        vid = self.bereit()
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (422, "mail_empfaenger_abgelehnt"))
        self.assertEqual((db.vorgang_intern("1001", vid)["status"], self.link_zeilen()), ("bereit", []))
        srv2 = self.vorbereiten(data_antwort="554 5.7.1 rejected")
        vid2 = self.bereit()
        r = self.senden_ok(vid2)
        self.assertEqual((r.status_code, r.get_json()["error"]), (502, "mail_abgelehnt"))
        self.assertEqual(db.vorgang_intern("1001", vid2)["status"], "bereit")

    def test_absender_abgelehnt_beim_versand(self):
        srv = self.vorbereiten(mail_ablehnen=True)
        vid = self.bereit()
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (502, "mail_absender_abgelehnt"))
        self.assertEqual((db.vorgang_intern("1001", vid)["status"], self.link_zeilen(), srv.nachrichten), ("bereit", [], []))

    def test_zustandswechsel_ist_atomar_und_gilt_nur_im_erwarteten_zustand(self):
        vid = self.bereit()
        self.assertFalse(db.vorgang_status_wechseln("1001", vid, "versendet", "versenden"))             # falscher Ausgangszustand
        self.assertTrue(db.vorgang_status_wechseln("1001", vid, "bereit", "versenden"))
        self.assertFalse(db.vorgang_status_wechseln("1001", vid, "bereit", "versenden"))                # nur ein Aufruf gewinnt
        self.assertFalse(db.vorgang_status_wechseln("2002", vid, "versenden", "bereit"))                # fremder Mandant
        self.assertEqual(db.vorgang_intern("1001", vid)["status"], "versenden")
        with self.assertRaises(ValueError):
            db.vorgang_status_wechseln("1001", vid, "versenden", "bereit", erstellt="x")

    def test_zertifikatsfehler_beim_versand(self):
        srv = self.vorbereiten("starttls", zert=ZERT_FALSCH)
        vid = self.bereit()
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (502, "mail_tls_zertifikat"))
        self.assertEqual((srv.anmeldungen, srv.nachrichten, db.vorgang_intern("1001", vid)["status"]), ([], [], "bereit"))

    def test_abbruch_nach_data_ist_unklar_und_wird_nie_wiederholt(self):
        srv = self.vorbereiten(nach_data_abbrechen=True)
        vid = self.bereit()
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (502, "versand_unklar"))
        v = db.vorgang_intern("1001", vid)
        self.assertEqual((v["status"], v["versand_fehler"]), ("versand_unklar", "unklar"))
        self.assertEqual(len(self.link_zeilen()), 1)                                                   # der Link könnte beim Mitglied angekommen sein
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "versand_laeuft_oder_unklar"))
        with db.verbindung() as c:
            self.assertEqual([z["ergebnis"] for z in c.execute("SELECT ergebnis FROM versand")], ["unklar"])
        self.assertEqual(self.c.post("/api/v1/vorgaenge/%d/abgleich-bestaetigen" % vid, json={"bestaetigt": True}).status_code, 409)

    def test_unlesbarer_mail_zugang(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK2}):
            r = self.senden_ok(vid)
            self.assertEqual((r.status_code, r.get_json()["error"]), (503, "mail_zugang_nicht_lesbar"))
            self.assertEqual((db.vorgang_intern("1001", vid)["status"], self.link_zeilen()), ("bereit", []))
        self.assertEqual(srv.nachrichten, [])

    def test_stundenlimit_zaehlt_auch_fehlversuche(self):
        srv = self.vorbereiten()
        with mock.patch("config.VERSAND_JE_STUNDE", 2):
            self.c.put("/api/v1/mail-zugang", json={"passwort": "falsch-2"})
            self.markiere_geprueft()
            vid = self.bereit()
            self.assertEqual(self.senden_ok(vid).status_code, 502)
            self.assertEqual(self.senden_ok(vid).status_code, 502)
            r = self.senden_ok(vid)
            self.assertEqual((r.status_code, r.get_json()["error"]), (429, "zu_viele_versaende"))
            self.als("tb", me(email="b@test.example", ref="2002"))
            self.assertEqual(self.senden_ok(vid).status_code, 404)                                     # anderer Mandant: eigene Zählung, fremder Vorgang

    def test_mail_zugang_lesezugriff_wird_protokolliert(self):
        srv = self.vorbereiten()
        self.senden_ok(self.bereit())
        self.assertEqual([x["details"] for x in self.audit_zeilen("mailzugang_gelesen")], ["zweck=versand"])

    def test_loeschung_entfernt_links_und_versandzeilen(self):
        srv = self.vorbereiten()
        self.senden_ok(self.bereit())
        zahlen = db.mandant_loeschen("1001")
        self.assertEqual((zahlen["link"], zahlen["versand"], zahlen["mail_zugang"]), (1, 1, 1))
        self.assertEqual(self.link_zeilen(), [])

    def test_nach_einem_schluesselwechsel_ist_die_mailadresse_lesbar(self):
        srv = self.vorbereiten()
        vid = self.bereit()
        self.senden_ok(vid)
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK2, "SIGNNEXT_HAUPTSCHLUESSEL_ALT": HK}):
            self.assertEqual(schluesselwechsel.wechseln(), {"umgeschluesselt": 2, "schon_aktuell": 0})     # Mail-Passwort + Mitglieder-Adresse
            v = db.vorgang_intern("1001", vid)
            self.assertTrue(schluessel.code_pruefen("1001", v["code_kid"], v["code_hmac"], "01021960"))      # der Code bleibt am alten Schlüssel prüfbar
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK2}):
            self.assertEqual(schluessel.entschluesseln("1001", "mitglied-mail", db.vorgang_intern("1001", vid)["email_enc"]), pdfs.MAIL)


class TestRollenFunktion(unittest.TestCase):
    def test_admin_pruefen(self):
        import portal_auth
        n = {"rolle": "admin", "stellen_anzahl": 1, "stelle_rolle": None}
        self.assertEqual(portal_auth.admin_pruefen(n), (True, None))
        self.assertEqual(portal_auth.admin_pruefen(dict(n, stellen_anzahl=2)), (False, "mehrere_beratungsstellen"))
        self.assertEqual(portal_auth.admin_pruefen(dict(n, rolle="mitarbeiter")), (False, "rolle_nicht_erlaubt"))
        with mock.patch.dict(os.environ, {"SIGNNEXT_STELLE_ROLLE": "an"}):
            self.assertEqual(portal_auth.admin_pruefen(dict(n, stelle_rolle="admin", stellen_anzahl=3)), (True, None))
            self.assertEqual(portal_auth.admin_pruefen(n), (False, "rolle_nicht_erlaubt"))
        with mock.patch.dict(os.environ, {"SIGNNEXT_STELLE_ROLLE": "irgendwas"}):
            self.assertEqual(config.stelle_rolle_variante(), "aus")

    def test_konfiguration(self):
        with mock.patch.dict(os.environ, {"SIGNNEXT_BASIS_URL": "https://x.example/"}):
            self.assertEqual(config.basis_url(), "https://x.example")
        for schlecht in ("", "http://x.example", "https://", "ftp://x", "x.example"):
            with mock.patch.dict(os.environ, {"SIGNNEXT_BASIS_URL": schlecht}):
                self.assertIsNone(config.basis_url(), schlecht)


if __name__ == "__main__":
    unittest.main()
