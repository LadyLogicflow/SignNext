"""Baustein 4: Versand-Schalter, Mailtext, Zertifikatsmeldungen, Mitglieds-Zugang (Code, Sperre, Sitzung), Schrittfolge, Unterschrift, neuer Link, Texte.
Alles mit erfundenen Daten und dem lokalen Test-Mailserver aus test_baustein3."""
import base64
import hashlib
import io
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

import config
import db
import mailer
import seitenbild
import texte
import zugang
from tests import pdfs
from tests.test_baustein1 import me
from tests.test_baustein3 import ABSENDER, PW, MailBasis, _echt_ssl_kontext
import unittest

BASIS = "https://signnext.example"
ORIGIN = {"Origin": BASIS}
CODE = "01021960"                                   # Geburtsdatum aus dem erfundenen Protokoll A: 01.02.1960
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
SEITEN = 16                                      # B (4) + A (12) zusammen; seit der Einzel-Signatur werden die Dokumente einzeln gelesen (B zuerst: 4 Seiten)
SEITEN_B, SEITEN_A = 4, 12
_BILDER = {}
_echt_rendern = seitenbild.rendern


def _rendern_gemerkt(pdf, seite):
    k = (hashlib.sha256(pdf).hexdigest(), seite)
    if k not in _BILDER:
        _BILDER[k] = _echt_rendern(pdf, seite)
    return _BILDER[k]


def png_bytes(breite=400, hoehe=150, tinte=True, text=None, apng=False, anhang=b""):
    bild = Image.new("RGBA", (breite, hoehe), (0, 0, 0, 0))
    if tinte:
        d = ImageDraw.Draw(bild)
        d.line([(10, hoehe - 20), (breite // 3, 20), (breite // 2, hoehe - 30), (breite - 20, 25)], fill=(10, 10, 80, 255), width=5)
    b = io.BytesIO()
    if apng:
        zweit = bild.copy()
        ImageDraw.Draw(zweit).line([(0, 0), (80, 80)], fill=(0, 0, 0, 255), width=6)
        bild.save(b, "PNG", save_all=True, append_images=[zweit], duration=100)
    else:
        info = None
        if text:
            info = PngInfo()
            info.add_text("Kommentar", text)
        bild.save(b, "PNG", pnginfo=info)
    return b.getvalue() + anhang


def uri(daten: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(daten).decode("ascii")


class ZugangBasis(MailBasis):
    ABSCHLUSS_AUS = True                      # Baustein 4 prüft nur den Zugang; der Abschluss (Einbetten) hat seine Tests in test_baustein5

    def setUp(self):
        super().setUp()
        if self.ABSCHLUSS_AUS:
            p = mock.patch("abschluss.ausfuehren", return_value=False)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("seitenbild.rendern", side_effect=_rendern_gemerkt)
        p.start()
        self.addCleanup(p.stop)
        self.srv = self.starte("implicit")
        self.assertEqual(self.zugang(self.srv).status_code, 200)
        self.markiere_geprueft()
        self.m = self.app.test_client()
        self.ua = {"User-Agent": IPHONE}

    # --- Hilfen
    def starten(self, **kw):
        """Legt einen Vorgang (B + A) an, versendet ihn und merkt sich Vorgang und Token aus der Mail."""
        self.vid = self.bereit(**kw)
        r = self.senden_ok(self.vid)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.token = self.token_aus_mail(self.srv.nachrichten[-1])
        return self.vid, self.token

    def token_aus_mail(self, nachricht):
        return re.search(r"/u#([A-Za-z0-9_-]+)", self.mail_text(nachricht)[1]).group(1)

    def post(self, pfad, daten=None, client=None, kopf=None):
        h = dict(ORIGIN)
        h.update(self.ua)
        h.update(kopf or {})
        return (client or self.m).post("/api/v1/zugang" + pfad, json=daten, headers=h, base_url=BASIS)

    def hole(self, pfad, client=None, kopf=None):
        h = dict(self.ua)
        h.update(kopf or {})
        return (client or self.m).get("/api/v1/zugang" + pfad, headers=h, base_url=BASIS)

    def pruefen(self, token=None, code=CODE, **kw):
        return self.post("/pruefen", {"token": self.token if token is None else token, "code": code}, **kw)

    def einloggen(self):
        r = self.pruefen()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r

    def status(self, client=None):
        r = self.hole("/status", client=client)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def alle_seiten(self, n=None, dokument=None):
        """Sieht alle Seiten des aktuellen (oder angegebenen) Dokuments an. ``n`` bleibt nur für ältere Aufrufe (seit der Einzel-Signatur zählt das Dokument)."""
        st = self.status()
        rolle = dokument or st["dokument"]["rolle"]
        seiten = next(d["seiten"] for d in st["dokumente"] if d["rolle"] == rolle)
        for i in range(1, seiten + 1):
            self.assertEqual(self.hole("/seite/%d?dokument=%s" % (i, rolle)).status_code, 200, (rolle, i))
        return rolle

    def lesen(self, dokument=None):
        rolle = self.alle_seiten(dokument=dokument)
        r = self.post("/gelesen", {"bestaetigt": True, "dokument": rolle})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def texte_holen(self):
        r = self.hole("/texte")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def sign(self, person, hash_, bild=None, client=None, dokument=None, **kw):
        """Unterschrift für ``person`` im aktuellen Dokument (oder ``dokument``); ``dokument=False`` lässt die Angabe weg."""
        if dokument is None:
            dokument = self.status(client=client)["dokument"]["rolle"]
        d = {"person": person, "bild": uri(png_bytes()) if bild is None else bild, "ich_unterschreibe": True, "haken": True, "text_hash": hash_}
        if dokument is not False:
            d["dokument"] = dokument
        d.update(kw)
        return self.post("/unterschrift", d, client=client)

    def alles_unterschreiben(self, bild=None):
        """Liest und unterschreibt der Reihe nach alle Dokumente und Personen, bis der Vorgang fertig ist. Liefert die letzte Antwort."""
        for _ in range(12):
            st = self.status()
            if st["schritt"] == "lesen":
                self.lesen()
                st = self.status()
            h = self.texte_holen()["hash"] if st["texte_noetig"] else None
            if h is None:
                h = ""
            p = st["naechste_person"]
            r = self.sign(p, h, bild=bild if bild is not None else uri(png_bytes() if p == 1 else png_bytes(300, 100)))
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            if r.get_json().get("fertig"):
                return r
        self.fail("Vorgang wurde nicht fertig")

    def bis_zum_unterschreiben(self):
        self.starten()
        self.einloggen()
        self.lesen()
        return self.texte_holen()["hash"]

    def versuche_falsch(self, n, token=None):
        return [self.pruefen(token=token, code="99999999").get_json() for _ in range(n)]

    def zeit(self, **delta):
        return mock.patch("zugang._jetzt", return_value=datetime.now(timezone.utc) + timedelta(**delta))

    def link_zeile(self):
        with db.verbindung() as c:
            return dict(c.execute("SELECT * FROM link ORDER BY id DESC LIMIT 1").fetchone())

    def intern(self):
        return self.db_text() + self.db_bytes().decode("latin-1") + self.logtext.getvalue() + json.dumps(self.audit_zeilen())


# ======================================================================================================== Versand-Schalter, Mailtext, Meldungen

class TestVersandSchalter(MailBasis):
    def vorbereiten(self):
        srv = self.starte("implicit")
        self.assertEqual(self.zugang(srv).status_code, 200)
        self.markiere_geprueft()
        return srv, self.bereit()

    def test_standard_ist_aus(self):
        srv, vid = self.vorbereiten()
        for wert in ("", "aus", "AN", "ja", "1", "true", "testadresse"):
            with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": wert}):
                r = self.senden_ok(vid)
                self.assertEqual((r.status_code, r.get_json()["error"]), (503, "versand_gesperrt"), wert)
        with mock.patch.dict(os.environ):
            os.environ.pop("SIGNNEXT_VERSAND", None)
            self.assertEqual(config.versand_modus(), "aus")
            self.assertEqual(self.senden_ok(vid).status_code, 503)
        self.assertEqual((srv.nachrichten, srv.anmeldungen), ([], []))
        self.assertEqual(db.vorgang_intern("1001", vid)["status"], "bereit")

    def test_testadressen(self):
        srv, vid = self.vorbereiten()
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": "testadressen", "SIGNNEXT_VERSAND_TESTADRESSEN": "andere@example.org, Test.Person@example.org"}):
            r = self.senden_ok(vid)
            self.assertEqual((r.status_code, r.get_json()["error"]), (409, "empfaenger_nicht_freigegeben"))
            self.assertEqual((srv.nachrichten, db.vorgang_intern("1001", vid)["status"]), ([], "bereit"))
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": "testadressen", "SIGNNEXT_VERSAND_TESTADRESSEN": "x@example.org, %s" % pdfs.MAIL.upper()}):
            self.assertEqual(self.senden_ok(vid).status_code, 200)
        self.assertEqual(srv.nachrichten[0]["an"], [pdfs.MAIL])

    def test_testadressen_leer_sendet_nichts(self):
        srv, vid = self.vorbereiten()
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": "testadressen", "SIGNNEXT_VERSAND_TESTADRESSEN": " , ,"}):
            self.assertEqual(self.senden_ok(vid).status_code, 409)
        self.assertEqual(srv.nachrichten, [])

    def test_mail_zugang_muss_geprueft_sein(self):
        srv, vid = self.vorbereiten()
        with db.verbindung() as c:
            c.execute("UPDATE mail_zugang SET geprueft_am=NULL")
        for modus in ("an", "testadressen"):
            with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": modus, "SIGNNEXT_VERSAND_TESTADRESSEN": pdfs.MAIL}):
                r = self.senden_ok(vid)
                self.assertEqual((r.status_code, r.get_json()["error"]), (409, "mail_zugang_ungeprueft"), modus)
        self.assertEqual(srv.nachrichten, [])
        # Ende zu Ende: Prüfung über die Schnittstelle schaltet den Versand frei
        self.assertEqual(self.c.post("/api/v1/mail-zugang/pruefen").status_code, 200)
        self.assertEqual(self.senden_ok(vid).status_code, 200)

    def test_aenderung_am_zugang_hebt_die_freigabe_auf(self):
        srv, vid = self.vorbereiten()
        self.c.put("/api/v1/mail-zugang", json={"signatur": "Neu"})
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "mail_zugang_ungeprueft"))

    def test_ohne_angaben_fuer_die_datenschutzinformation(self):
        srv, vid = self.vorbereiten()
        with db.verbindung() as c:
            c.execute("DELETE FROM stelle_einstellung")
        r = self.senden_ok(vid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "stelleneinstellungen_fehlen"))
        self.assertEqual(srv.nachrichten, [])


class TestMailtextUndMeldungen(ZugangBasis):
    def test_mailtext(self):
        self.starten()
        m, text = self.mail_text(self.srv.nachrichten[0])
        self.assertIn("Haben Sie diese Nachricht nicht erwartet, öffnen Sie den Link bitte nicht und rufen Sie Ihre Beratungsstelle an.", text)
        self.assertIn("Zum Öffnen benötigen Sie das Geburtsdatum der Person, für die das Dokument erstellt wurde, bei Ehepaaren und eingetragenen Lebenspartnerschaften "
                      "der zuerst genannten Person (acht Ziffern: Tag, Monat, vierstelliges Jahr, ohne Punkte).", text)
        self.assertNotIn("Ihr Geburtsdatum", text)
        self.assertEqual(m["Subject"], "Ihre Beratungsstelle: Dokumente zur Unterschrift bereit")                 # zwei Dokumente (Anschreiben und Protokoll), catrin 18.11.2026
        self.assertIn("Ihre Beratungsstelle hat zwei Dokumente für Sie zur Unterschrift bereitgestellt. Jedes Dokument wird einzeln gelesen und einzeln unterschrieben.", text)
        self.assertIn("Bitte öffnen Sie sie über diesen persönlichen Link", text)

    def test_mailtext_bei_einem_dokument_bleibt_der_freigegebene(self):
        vid = self.bereit(a=False)                                                               # nur das Anschreiben
        self.assertEqual(self.c.put("/api/v1/vorgaenge/%d/angaben" % vid, json={"geburtsdatum": "01.02.1960"}).status_code, 200)
        self.assertEqual(self.senden_ok(vid).status_code, 200)
        m, text = self.mail_text(self.srv.nachrichten[0])
        self.assertEqual(m["Subject"], "Ihre Beratungsstelle: Dokument zur Unterschrift bereit")
        self.assertIn("Ihre Beratungsstelle hat ein Dokument für Sie zur Unterschrift bereitgestellt.", text)
        self.assertIn("Bitte öffnen Sie es über diesen persönlichen Link", text)
        self.assertNotIn("zwei Dokumente", text)

    def test_link_steht_im_fragment_nicht_im_pfad(self):
        self.starten()
        _, text = self.mail_text(self.srv.nachrichten[0])
        self.assertRegex(text, r"https://signnext\.example/u#[A-Za-z0-9_-]{43,}")
        self.assertNotIn("/u/", text)
        self.assertEqual(len(self.token), 43)                                     # 32 Zufallsbytes = 256 Bit

    def test_zertifikatsmeldungen_verstaendlich_und_ohne_details(self):
        from tests.test_baustein3 import ZERT_FALSCH
        srv = self.starte("starttls", zert=ZERT_FALSCH)
        self.zugang(srv)
        r = self.c.post("/api/v1/mail-zugang/pruefen")
        self.assertEqual((r.status_code, r.get_json()["error"], r.get_json()["meldung"]), (502, "mail_tls_zertifikat", "Zertifikat des Mailservers nicht vertrauenswürdig."))
        srv2 = self.starte("starttls", starttls_angeboten=False, auth_ohne_tls=True)
        self.zugang(srv2)
        r = self.c.post("/api/v1/mail-zugang/pruefen")
        self.assertEqual((r.status_code, r.get_json()["error"], r.get_json()["meldung"]), (502, "mail_tls_nicht_angeboten", "Der Mailserver bietet keine verschlüsselte Verbindung an."))
        for r in (r,):
            for verboten in ("SSL", "certificate", "CERTIFICATE", "verify", "self-signed", "Traceback", "127.0.0.1", "ssl."):
                self.assertNotIn(verboten, r.get_data(as_text=True))

    def test_verbindungsbeleg_der_pruefung(self):
        for modus in ("implicit", "starttls"):
            srv = self.starte(modus)
            self.zugang(srv)
            r = self.c.post("/api/v1/mail-zugang/pruefen")
            self.assertEqual(r.status_code, 200)
            v = r.get_json()["verbindung"]
            self.assertTrue(v["tls_version"].startswith("TLSv1."), modus)
            self.assertTrue(v["cipher"])
            self.assertEqual(v["aussteller"], "SignNext Test-CA")
            self.assertRegex(v["zertifikat_gueltig_bis"], r"GMT$")
            z = [x for x in self.audit_zeilen("mailzugang_geprueft") if x["details"].startswith("ok=True")][-1]
            self.assertIn("tls=" + v["tls_version"], z["details"])
            self.assertIn("aussteller=SignNext Test-CA", z["details"])
            self.assertNotIn(PW, z["details"])

    def test_verbindungsinfo_ohne_tls_ist_leer(self):
        self.assertEqual(mailer.verbindungsinfo(mock.Mock(sock=object())), {})
        self.assertEqual(mailer.verbindungsinfo(object()), {})


# ======================================================================================================== Code-Prüfung und Sperre

class TestCode(ZugangBasis):
    def test_hinweistext(self):
        r = self.hole("/hinweis")
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertIn("Bei Ehepaaren und eingetragenen Lebenspartnerschaften ist das die zuerst genannte Person", j["zugangshinweis"])
        self.assertIn("Nach fünf Fehleingaben wird der Link aus Sicherheitsgründen gesperrt", j["zugangshinweis"])
        self.assertIn("acht Ziffern: Tag, Monat, vierstelliges Jahr, ohne Punkte", j["zugangshinweis"])
        self.assertTrue(j["freigegeben"])

    def test_erfolg_und_cookie(self):
        self.starten()
        r = self.einloggen()
        self.assertEqual(r.get_json(), {"ok": True, "sitzung_minuten": 60})
        k = r.headers["Set-Cookie"]
        for teil in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/api/v1/zugang", "Max-Age=3600"):
            self.assertIn(teil, k)
        wert = re.match(r"sn_zugang=([^;]+)", k).group(1)
        self.assertGreaterEqual(len(wert), 43)
        self.assertNotIn(wert, self.intern())                                    # nur der Hash steht in der Datenbank
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT id_hash FROM sitzung").fetchone()[0], hashlib.sha256(wert.encode()).hexdigest())

    def test_fuenf_fehlversuche_sperren_dauerhaft(self):
        self.starten()
        antworten = self.versuche_falsch(5)
        self.assertEqual([(a["versuch"], a["von"], a["gesperrt"]) for a in antworten], [(1, 5, False), (2, 5, False), (3, 5, False), (4, 5, False), (5, 5, True)])
        for a in antworten:
            self.assertEqual((a["error"], a["meldung"]), ("angaben_stimmen_nicht", "Angaben stimmen nicht."))
        self.assertEqual((self.link_zeile()["fehlversuche"], self.link_zeile()["gesperrt"]), (5, 1))
        # der richtige Code hilft jetzt nicht mehr, auch nicht nach Wochen
        for delta in ({}, {"days": 3}, {"days": 30}):
            with self.zeit(**delta):
                r = self.pruefen()
                self.assertEqual((r.status_code, r.get_json()["gesperrt"], r.get_json()["versuch"]), (401, True, 5), delta)
                self.assertNotIn("Set-Cookie", r.headers)
        self.assertEqual(self.link_zeile()["fehlversuche"], 5)                  # nichts setzt den Zähler zurück
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_json()["link_status"], "gesperrt")
        self.assertEqual([x["details"] for x in self.audit_zeilen("link_gesperrt")], ["vorgang=%d" % self.vid])

    def test_richtiger_code_nach_vier_fehlversuchen(self):
        self.starten()
        self.versuche_falsch(4)
        self.einloggen()
        self.assertEqual(self.link_zeile()["fehlversuche"], 4)                    # der Zähler wird auch bei Erfolg nicht zurückgesetzt
        self.assertEqual(self.pruefen(code="88888888").get_json()["versuch"], 5)

    def test_kein_orakel_unbekanntes_token_antwortet_gleich(self):
        self.starten()
        echt = self.versuche_falsch(7)
        unbekannt = self.versuche_falsch(7, token="A" * 43)
        self.assertEqual(echt, unbekannt)
        for a in (echt[0], unbekannt[0]):
            self.assertEqual(set(a), {"error", "meldung", "versuch", "von", "gesperrt"})
        codes = {self.pruefen(token=t, code="99999999").status_code for t in ("B" * 43, "", "x" * 500, "ä", self.token)}
        self.assertEqual(codes, {401})

    def test_abgelaufen_verbraucht_gesperrt_sehen_aus_wie_gesperrt(self):
        self.starten()
        erwartet = {"error": "angaben_stimmen_nicht", "meldung": "Angaben stimmen nicht.", "versuch": 5, "von": 5, "gesperrt": True}
        with self.zeit(days=8):
            r = self.pruefen()
            self.assertEqual((r.status_code, r.get_json()), (401, erwartet))
        with db.verbindung() as c:
            c.execute("UPDATE link SET verbraucht_am=?", (db.jetzt(),))
        self.assertEqual(self.pruefen().get_json(), erwartet)
        with db.verbindung() as c:
            c.execute("UPDATE link SET verbraucht_am=NULL, gesperrt=1")
        self.assertEqual(self.pruefen().get_json(), erwartet)
        self.assertEqual(self.pruefen(token="C" * 43, code="99999999").get_json()["versuch"], 1)

    def test_format_fehler_zaehlen_mit(self):
        self.starten()
        for i, code in enumerate(["12345", "abcdef", "", "010219601", "01.02.", " 01021960", "01021960\n", "010260", "0102196"], 1):
            r = self.pruefen(code=code)
            self.assertEqual((r.status_code, r.get_json()["versuch"]), (401, min(i, 5)), code)

    def test_ungueltige_eingaben(self):
        self.starten()
        for body in (None, [], "x", {"token": self.token}, {"token": self.token, "code": CODE, "extra": 1}, {"code": CODE, "x": 1}):
            r = self.post("/pruefen", body)
            self.assertIn(r.status_code, (400, 401), body)
        self.assertEqual(self.post("/pruefen", {"token": 5, "code": 5}).status_code, 401)
        self.assertEqual(self.post("/pruefen", {"token": self.token, "code": 10}).status_code, 401)
        self.assertEqual(self.m.post("/api/v1/zugang/pruefen", data="kein json", content_type="application/json", headers=ORIGIN, base_url=BASIS).status_code, 400)
        self.assertEqual(self.link_zeile()["fehlversuche"], 2)

    def test_herkunft_wird_geprueft(self):
        self.starten()
        for kopf in ({}, {"Origin": "https://boese.example"}, {"Origin": "http://signnext.example"}, {"Origin": "null"}, {"Origin": BASIS + ".boese.example"}):
            r = self.m.post("/api/v1/zugang/pruefen", json={"token": self.token, "code": CODE}, headers=kopf, base_url=BASIS)
            self.assertEqual(r.status_code, 403, kopf)
        self.assertEqual(self.link_zeile()["fehlversuche"], 0)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sitzung").fetchone()[0], 0)
        for pfad, d in (("/gelesen", {"bestaetigt": True}), ("/unterschrift", {}), ("/abmelden", None)):
            self.assertEqual(self.m.post("/api/v1/zugang" + pfad, json=d, headers={"Origin": "https://boese.example"}, base_url=BASIS).status_code, 403, pfad)

    def test_parallele_versuche_zaehlen_genau_fuenf(self):
        self.starten()
        ergebnisse, fehler = [], []

        def versuch():
            try:
                c = self.app.test_client()
                ergebnisse.append(self.pruefen(code="99999999", client=c).get_json())
            except Exception as e:                                                  # pragma: no cover
                fehler.append(e)
        ts = [threading.Thread(target=versuch) for _ in range(14)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(fehler, [])
        self.assertEqual(sorted(a["versuch"] for a in ergebnisse), [1, 2, 3, 4] + [5] * 10)
        self.assertEqual((self.link_zeile()["fehlversuche"], self.link_zeile()["gesperrt"]), (5, 1))

    def test_begrenzung_je_ip(self):
        self.starten()
        with mock.patch("zugang.IP_LIMIT", 3):
            for i in range(3):
                self.assertEqual(self.pruefen(code="99999999").status_code, 401)
            r = self.pruefen()                                                       # auch der richtige Code kommt nicht durch
            self.assertEqual((r.status_code, r.get_json()["error"]), (429, "zu_viele_versuche"))
            r = self.pruefen(token="D" * 43)
            self.assertEqual(r.status_code, 429)
            r = self.pruefen(kopf={"X-Forwarded-For": "198.51.100.7"})                # eine andere Adresse ist nicht betroffen
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.link_zeile()["fehlversuche"], 3)                        # 429 zählt nicht als Fehlversuch
        with self.zeit(minutes=11):
            self.assertEqual(self.pruefen(code="99999999").status_code, 401)             # nach dem Zeitfenster wieder möglich

    def test_ip_nur_als_hmac_und_tabellen_werden_bereinigt(self):
        self.starten()
        self.pruefen(code="99999999", kopf={"X-Forwarded-For": "198.51.100.7"})
        self.pruefen(token="E" * 43, code="99999999")
        gesamt = self.intern()
        for ip in ("198.51.100.7", "127.0.0.1"):
            self.assertNotIn(ip, self.db_text().replace("127.0.0.0", ""), ip)
        with db.verbindung() as c:
            self.assertEqual(len(c.execute("SELECT ip_hmac FROM zugang_ip").fetchone()[0]), 64)
            self.assertEqual(c.execute("SELECT token_hash FROM zugang_unbekannt").fetchone()[0], hashlib.sha256(("E" * 43).encode()).hexdigest())
        self.assertNotIn("E" * 43, gesamt)
        with self.zeit(days=2):
            self.pruefen(code="99999999")
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM zugang_unbekannt").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM zugang_ip").fetchone()[0], 1)

    def test_aufrufprotokoll(self):
        self.starten()
        self.pruefen(code="99999999", kopf={"X-Forwarded-For": "198.51.100.77"})
        self.einloggen()
        with db.verbindung() as c:
            zeilen = [dict(z) for z in c.execute("SELECT * FROM zugriff_log ORDER BY id")]
        self.assertEqual([(z["ergebnis"], z["mandant_ref"]) for z in zeilen], [("falsch", "1001"), ("ok", "1001")])
        self.assertEqual((zeilen[0]["ip"], zeilen[1]["ip"]), ("198.51.100.0", "127.0.0.0"))             # gekürzt
        self.assertTrue(all(z["zeit"] for z in zeilen))

    def test_code_und_token_nirgends_im_klartext(self):
        self.starten()
        self.versuche_falsch(2)
        self.einloggen()
        gesamt = self.intern()
        for geheim in (self.token, CODE, "99999999", pdfs.DOB):
            self.assertNotIn(geheim, gesamt, geheim)

    def test_code_pruefung_nach_schluesselwechsel(self):
        from tests.test_baustein3 import HK2, HK
        self.starten()
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": HK2, "SIGNNEXT_HAUPTSCHLUESSEL_ALT": HK}):
            self.einloggen()
            self.assertEqual(self.pruefen(code="99999999").get_json()["versuch"], 1)

    def test_ohne_hauptschluessel(self):
        self.starten()
        with mock.patch.dict(os.environ, {"SIGNNEXT_HAUPTSCHLUESSEL": ""}):
            self.assertEqual(self.pruefen().status_code, 503)


# ======================================================================================================== Sitzung

class TestSitzung(ZugangBasis):
    def test_ohne_sitzung_nichts(self):
        self.starten()
        for r in (self.hole("/status"), self.hole("/seite/1"), self.hole("/texte"), self.post("/gelesen", {"bestaetigt": True}), self.sign(1, "x", dokument="B")):
            self.assertEqual((r.status_code, r.get_json()["error"]), (401, "sitzung_ungueltig"))

    def test_sitzung_laeuft_nach_60_minuten_ab(self):
        self.starten()
        self.einloggen()
        self.assertEqual(self.hole("/status").status_code, 200)
        with self.zeit(minutes=59):
            self.assertEqual(self.hole("/status").status_code, 200)
        with self.zeit(minutes=61):
            self.assertEqual(self.hole("/status").status_code, 401)
        self.assertEqual(self.pruefen().status_code, 200)                              # Code erneut eingeben genügt, Link gilt bis Tag 7
        self.assertEqual(self.hole("/status").status_code, 200)

    def test_sitzung_endet_mit_dem_link(self):
        self.starten()
        self.einloggen()
        with self.zeit(days=8):
            self.assertEqual(self.hole("/status").status_code, 401)

    def test_sitzung_endet_spaetestens_mit_dem_link(self):
        """Die Sitzung läuft noch, der Link ist aber schon abgelaufen: Der Link zählt."""
        self.starten()
        with self.zeit(days=6, hours=23, minutes=30):
            self.einloggen()
            self.assertEqual(self.hole("/status").status_code, 200)
        with self.zeit(days=7, minutes=10):
            self.assertEqual(self.hole("/status").status_code, 401)

    def test_veraenderter_oder_fremder_cookie(self):
        self.starten()
        r = self.einloggen()
        wert = re.match(r"sn_zugang=([^;]+)", r.headers["Set-Cookie"]).group(1)
        for kaputt in (wert[:-1] + ("A" if wert[-1] != "A" else "B"), "", "x", wert.upper()):
            c = self.app.test_client()
            c.set_cookie("sn_zugang", kaputt, domain="signnext.example", path="/api/v1/zugang")
            self.assertEqual(self.hole("/status", client=c).status_code, 401, kaputt)

    def test_abmelden(self):
        self.starten()
        self.einloggen()
        self.assertEqual(self.post("/abmelden").status_code, 200)
        self.assertEqual(self.hole("/status").status_code, 401)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sitzung").fetchone()[0], 0)

    def test_portalanmeldung_der_beratungsstelle_gilt_hier_nicht(self):
        self.starten()
        self.assertEqual(self.c.get("/api/v1/zugang/status").status_code, 401)
        self.assertEqual(self.c.post("/api/v1/zugang/unterschrift", json={}, headers=ORIGIN).status_code, 401)

    def test_zwei_vorgaenge_zwei_sitzungen_getrennt(self):
        self.starten()
        t1 = self.token
        self.einloggen()
        vid2, t2 = self.starten()
        c2 = self.app.test_client()
        self.assertEqual(self.pruefen(token=t2, client=c2).status_code, 200)
        with db.verbindung() as c:
            zeilen = [dict(z) for z in c.execute("SELECT vorgang_id FROM sitzung ORDER BY id")]
        self.assertEqual(len(zeilen), 2)
        self.assertNotEqual(zeilen[0]["vorgang_id"], zeilen[1]["vorgang_id"])
        self.assertNotEqual(t1, t2)

    def test_status_zeigt_den_fortschritt(self):
        self.starten()
        self.einloggen()
        j = self.hole("/status").get_json()
        self.assertEqual((j["schritt"], j["seiten"], j["geliefert"], j["personen"], j["unterschrieben"], j["naechste_person"]), ("lesen", SEITEN_B, 0, 2, [], 1))
        self.assertEqual((j["dokument"], j["texte_noetig"]), ({"rolle": "B", "titel": "Anschreiben", "nr": 1, "von": 2, "seiten": SEITEN_B}, True))
        self.assertEqual([(d["rolle"], d["seiten"], d["erforderlich"], d["unterschrieben"], d["fertig"]) for d in j["dokumente"]], [("B", SEITEN_B, [1, 2], [], False), ("A", SEITEN_A, [1, 2], [], False)])
        self.lesen()
        self.assertEqual(self.hole("/status").get_json()["schritt"], "texte")
        self.texte_holen()
        self.assertEqual(self.hole("/status").get_json()["schritt"], "unterschreiben")


# ======================================================================================================== Seiten, Lesen, Texte

class TestSchritte(ZugangBasis):
    def test_seitenbilder(self):
        self.starten()
        self.einloggen()
        r = self.hole("/seite/1")
        self.assertEqual((r.status_code, r.mimetype), (200, "image/jpeg"))
        self.assertTrue(r.data.startswith(b"\xff\xd8"))
        self.assertEqual(Image.open(io.BytesIO(r.data)).size[0], 834)
        self.assertIn("no-store", r.headers["Cache-Control"])
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn(self.token, r.headers.get("Content-Disposition", "") + r.headers.get("Location", ""))
        for n in (0, SEITEN_B + 1, 999):
            self.assertEqual(self.hole("/seite/%d" % n).status_code, 404, n)
        self.assertEqual(self.m.get("/api/v1/zugang/seite/abc", base_url=BASIS).status_code, 404)

    def test_seitenbilder_nur_in_der_sitzung_und_nie_mit_token_in_der_adresse(self):
        self.starten()
        c = self.app.test_client()
        self.assertEqual(self.hole("/seite/1", client=c).status_code, 401)
        self.einloggen()
        self.assertEqual(self.hole("/seite/1?token=%s" % self.token, client=c).status_code, 401)   # ein Token in der Adresse ersetzt keine Sitzung
        routen = [str(r) for r in self.app.url_map.iter_rules() if r.rule.startswith("/api/v1/zugang")]
        self.assertFalse([r for r in routen if "token" in r.lower()], routen)

    def test_lesen_erst_nach_allen_seiten(self):
        self.starten()
        self.einloggen()
        r = self.post("/gelesen", {"bestaetigt": True})
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "seiten_fehlen"))
        for n in range(1, SEITEN_B):
            self.hole("/seite/%d" % n)
        self.assertEqual(self.post("/gelesen", {"bestaetigt": True}).get_json()["error"], "seiten_fehlen")
        self.hole("/seite/%d" % SEITEN_B)
        self.assertEqual(self.hole("/status").get_json()["geliefert"], SEITEN_B)
        for body in (None, {}, {"bestaetigt": False}, {"bestaetigt": "ja"}, {"bestaetigt": True, "x": 1}):
            self.assertEqual(self.post("/gelesen", body).status_code, 400, body)
        self.assertEqual(self.post("/gelesen", {"bestaetigt": True}).status_code, 200)

    def test_mehrfach_dieselbe_seite_zaehlt_einmal(self):
        self.starten()
        self.einloggen()
        for _ in range(SEITEN + 2):
            self.hole("/seite/1")
        self.assertEqual(self.hole("/status").get_json()["geliefert"], 1)
        self.assertEqual(self.post("/gelesen", {"bestaetigt": True}).status_code, 409)

    def test_texte_erst_nach_dem_lesen(self):
        self.starten()
        self.einloggen()
        r = self.hole("/texte")
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "lesen_fehlt"))
        self.lesen()
        self.assertEqual(self.hole("/texte").status_code, 200)

    def test_texte_vollstaendig_gefuellt_mit_kennung_und_hash(self):
        self.starten()
        self.einloggen()
        self.lesen()
        j = self.texte_holen()
        t = j["texte"]
        self.assertEqual(set(t), {"datenschutz", "unterschriftssatz", "haken", "ausstieg", "zugangshinweis"})
        for text in t.values():
            self.assertNotRegex(text, r"[{}]")
        self.assertIn("Beratungsstelle Beispiel, Musterweg 1, 12345 Musterstadt", t["datenschutz"])
        self.assertIn("IP-Adresse (ungekürzt gespeichert)", t["datenschutz"])
        self.assertIn("90 Tage nach der Unterschrift", t["datenschutz"])
        self.assertIn("https://bst.example/datenschutz", t["datenschutz"])
        self.assertIn("§ 126 BGB", t["unterschriftssatz"])
        self.assertEqual(t["haken"], "Ich habe die Datenschutzinformation gelesen und möchte das Dokument unterschreiben.")
        self.assertTrue("Freigabe offen" not in j["kennung"] and j["kennung"].startswith("SignNext-Datenschutzhinweis-Unterschriftsseite-1.0") and j["freigegeben"] is True)
        kanonisch = json.dumps({"kennung": j["kennung"], "texte": t}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(j["hash"], hashlib.sha256(kanonisch.encode("utf-8")).hexdigest())

    def test_variablen_kommen_aus_der_tatsaechlichen_einstellung(self):
        self.starten()
        self.einloggen()
        self.lesen()
        h1 = self.texte_holen()["hash"]
        with mock.patch.dict(os.environ, {"SIGNNEXT_AUFBEWAHRUNG_TAGE": "30", "SIGNNEXT_IP_SPEICHERUNG": "gekuerzt"}):
            j = self.texte_holen()
        self.assertIn("30 Tage nach der Unterschrift", j["texte"]["datenschutz"])
        self.assertIn("IP-Adresse (gekürzt gespeichert)", j["texte"]["datenschutz"])
        self.assertNotIn("ungekürzt", j["texte"]["datenschutz"])
        self.assertNotEqual(j["hash"], h1)
        self.einstellungen_setzen()
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET verantwortlicher='Andere Stelle, Weg 2, 54321 Ort, a@b.example'")
        j = self.texte_holen()
        self.assertIn("Andere Stelle, Weg 2", j["texte"]["datenschutz"])
        self.assertNotEqual(j["hash"], h1)

    def test_ohne_angaben_der_beratungsstelle_keine_texte(self):
        self.starten()
        self.einloggen()
        self.lesen()
        with db.verbindung() as c:
            c.execute("DELETE FROM stelle_einstellung")
        r = self.hole("/texte")
        self.assertEqual((r.status_code, r.get_json()["error"]), (503, "texte_fehlen"))

    def test_texte_im_repo_sind_vollstaendig(self):
        for n in ("datenschutz_mit_link", "datenschutz_ohne_link", "unterschriftssatz", "haken", "ausstieg", "zugangshinweis"):
            self.assertTrue(texte._roh(n), n)
        self.assertIn("{verantwortlicher}", texte._roh("datenschutz_mit_link"))
        self.assertIn("{ip_behandlung}", texte._roh("datenschutz_mit_link"))
        self.assertIn("{aufbewahrung_tage}", texte._roh("datenschutz_mit_link"))
        self.assertIn("{datenschutz_link}", texte._roh("datenschutz_mit_link"))


# ======================================================================================================== Unterschrift

class TestUnterschrift(ZugangBasis):
    def alle_zeilen(self):
        with db.verbindung() as c:
            return [dict(z) for z in c.execute("SELECT * FROM unterschrift ORDER BY person")]

    def test_reihenfolge_der_schritte_ist_nicht_umgehbar(self):
        self.starten()
        self.einloggen()
        r = self.sign(1, "x")
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "lesen_fehlt"))
        self.alle_seiten()
        self.assertEqual(self.sign(1, "x").get_json()["error"], "lesen_fehlt")             # alle Seiten gesehen reicht nicht, die Bestätigung fehlt
        self.post("/gelesen", {"bestaetigt": True})
        r = self.sign(1, "x")
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "texte_fehlen"))
        h = self.texte_holen()["hash"]
        self.assertEqual(self.alle_zeilen(), [])
        self.assertEqual(self.sign(1, h).status_code, 200)

    def test_zustimmung_und_klick_sind_pflicht(self):
        h = self.bis_zum_unterschreiben()
        for kw in ({"haken": False}, {"ich_unterschreibe": False}, {"haken": "ja"}, {"ich_unterschreibe": 1}, {"haken": None}):
            r = self.sign(1, h, **kw)
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "zustimmung_fehlt"), kw)
        self.assertEqual(self.alle_zeilen(), [])

    def test_text_hash_muss_zum_angezeigten_text_passen(self):
        h = self.bis_zum_unterschreiben()
        for falsch in ("", "abc", h.upper() + "x", None, 5):
            r = self.sign(1, falsch)
            self.assertEqual((r.status_code, r.get_json()["error"]), (409, "text_geaendert"), falsch)
        with db.verbindung() as c:                                                           # die Beratungsstelle ändert ihre Angaben nach der Anzeige
            c.execute("UPDATE stelle_einstellung SET datenschutz_link='https://neu.example/ds'")
        self.assertEqual(self.sign(1, h).status_code, 200)                                    # gilt, was angezeigt wurde (Hash der Sitzung)
        self.assertEqual(self.alle_zeilen()[0]["text_hash"], h)

    def test_nur_erwartete_felder_keine_biometrie(self):
        h = self.bis_zum_unterschreiben()
        voll = {"dokument": "B", "person": 1, "bild": uri(png_bytes()), "ich_unterschreibe": True, "haken": True, "text_hash": h}
        for extra in ({"druck": [0.5, 0.6]}, {"geschwindigkeit": 3}, {"striche": [[1, 2], [3, 4]]}, {"zeitpunkte": [1, 2]}, {"pointer": "pen"}):
            r = self.post("/unterschrift", dict(voll, **extra))
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "eingabe_ungueltig"), extra)
        for fehlt in ("person", "bild", "ich_unterschreibe"):                                   # Pflichtfelder
            r = self.post("/unterschrift", {k: v for k, v in voll.items() if k != fehlt})
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "eingabe_ungueltig"), fehlt)
        r = self.post("/unterschrift", {k: v for k, v in voll.items() if k != "dokument"})       # bei zwei Dokumenten muss das Dokument genannt sein
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "dokument_ungueltig"))
        for ungueltig in ("X", "M", None, 5, ["B"]):
            r = self.post("/unterschrift", dict(voll, dokument=ungueltig))
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "dokument_ungueltig"), ungueltig)
        for fehlt in ("haken", "text_hash"):                                                    # solange die Zustimmung dieser Person fehlt, sind beide Pflicht
            r = self.post("/unterschrift", {k: v for k, v in voll.items() if k != fehlt})
            self.assertIn(r.status_code, (400, 409), fehlt)
            self.assertNotEqual(r.status_code, 200)
        for body in (None, [], "x"):
            self.assertEqual(self.post("/unterschrift", body).status_code, 400)
        self.assertEqual(self.alle_zeilen(), [])

    def test_personen(self):
        h = self.bis_zum_unterschreiben()
        for p in (0, 3, -1, "1", None, True, 1.5):
            self.assertEqual(self.sign(p, h).status_code, 400, p)
        r = self.sign(2, h)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "reihenfolge"))

    def test_bildpruefung(self):
        h = self.bis_zum_unterschreiben()
        gross = png_bytes(2000, 800)
        zuviel = png_bytes(400, 150, anhang=b"\x00" * (250 * 1024))
        faelle = [(5, "bild_format"), ("", "bild_format"), ("data:image/jpeg;base64,AAAA", "bild_format"), ("data:image/png;base64,!!!", "bild_format"),
                  (uri(b"kein png"), "bild_ungueltig"), (uri(png_bytes(40, 10)), "bild_ungueltig"), (uri(gross), "bild_zu_gross"), (uri(zuviel), "bild_zu_gross"),
                  (uri(png_bytes(tinte=False)), "bild_leer"), (uri(png_bytes(apng=True)), "bild_ungueltig"), ("data:image/png;base64," + "A" * 400000, "bild_zu_gross")]
        for bild, code in faelle:
            r = self.sign(1, h, bild=bild)
            self.assertIn(r.status_code, (413, 422), (code, r.status_code))
            if r.status_code == 422:
                self.assertEqual(r.get_json()["error"], code, code)
        r = self.post("/unterschrift", {"dokument": "B", "person": 1, "bild": None, "ich_unterschreibe": True, "haken": True, "text_hash": h})
        self.assertEqual((r.status_code, r.get_json()["error"]), (422, "bild_format"))
        self.assertEqual(self.alle_zeilen(), [])
        self.assertEqual(self.sign(1, h).status_code, 200)

    def test_bild_wird_neu_kodiert_ohne_zusatzdaten(self):
        h = self.bis_zum_unterschreiben()
        roh = png_bytes(text="GEHEIMER-KOMMENTAR", anhang=b"ANHANG-NACH-IEND")
        self.assertIn(b"GEHEIMER-KOMMENTAR", roh)
        self.assertEqual(self.sign(1, h, bild=uri(roh)).status_code, 200)
        z = self.alle_zeilen()[0]
        self.assertTrue(z["bild"].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertNotIn(b"GEHEIMER-KOMMENTAR", z["bild"])
        self.assertNotIn(b"ANHANG-NACH-IEND", z["bild"])
        self.assertEqual(z["bild_sha256"], hashlib.sha256(z["bild"]).hexdigest())
        bild = Image.open(io.BytesIO(z["bild"]))
        self.assertEqual((bild.size, bild.format), ((400, 150), "PNG"))
        self.assertEqual(set(bild.info) - {"dpi", "gamma"}, set())                           # keine Textblöcke

    def test_ehepaar_nacheinander_ein_link(self):
        h = self.bis_zum_unterschreiben()
        r = self.sign(1, h)                                                                       # Anschreiben (B), Person 1
        self.assertEqual((r.status_code, r.get_json()), (200, {"fertig": False, "dokument_fertig": False, "naechste": {"dokument": "B", "person": 2}, "naechste_person": 2}))
        # Haken und Text gelten je Person: ohne erneute Anzeige geht Person 2 nicht
        r = self.sign(2, h)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "texte_fehlen"))
        self.assertEqual(self.hole("/status").get_json()["schritt"], "texte")
        h2 = self.texte_holen()["hash"]
        self.assertEqual(h2, h)
        r = self.sign(2, h2)                                                                      # Anschreiben (B), Person 2: Dokument fertig
        self.assertEqual((r.status_code, r.get_json()), (200, {"fertig": False, "dokument_fertig": True, "naechste": {"dokument": "A", "person": 1}, "naechste_person": 1}))
        self.assertEqual(db.vorgang_lesen("1001", self.vid)["status"], "versendet")
        r = self.sign(1, h, dokument="A")                                                         # Protokoll vor dem Lesen: nicht möglich
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "lesen_fehlt"))
        self.assertEqual(self.hole("/status").get_json()["schritt"], "lesen")
        self.lesen()
        st = self.hole("/status").get_json()
        self.assertEqual((st["schritt"], st["texte_noetig"], st["dokument"]["rolle"]), ("unterschreiben", False, "A"))     # Person 1 hat schon zugestimmt: kein Texte-Schritt
        r = self.sign(1, "", haken=False)
        self.assertEqual((r.status_code, r.get_json()["naechste"]), (200, {"dokument": "A", "person": 2}))
        r = self.sign(2, "", haken=None)                                                          # Person 2 hat im Anschreiben zugestimmt
        self.assertEqual((r.status_code, r.get_json()), (200, {"fertig": True}))
        self.assertEqual(db.vorgang_lesen("1001", self.vid)["status"], "unterschrieben")
        zeilen = self.alle_zeilen()
        self.assertEqual(sorted((z["dokument_rolle"], z["person"], z["haken_uebernommen"]) for z in zeilen), [("A", 1, 1), ("A", 2, 1), ("B", 1, 0), ("B", 2, 0)])
        self.assertEqual({z["text_hash"] for z in zeilen}, {h})                                   # Zustimmung stammt aus der ersten Unterschrift
        self.assertTrue(self.link_zeile()["verbraucht_am"])
        # danach ist alles zu: Sitzung, Link, Code
        self.assertEqual(self.hole("/status").status_code, 401)
        r = self.pruefen()
        self.assertEqual((r.status_code, r.get_json()["gesperrt"]), (401, True))
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_json()["link_status"], "verbraucht")
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sitzung").fetchone()[0], 0)
        self.assertEqual([x["details"] for x in self.audit_zeilen("unterschrieben")], ["vorgang=%d" % self.vid])

    def test_reihenfolge_der_dokumente_und_personen(self):
        h = self.bis_zum_unterschreiben()
        r = self.sign(1, h, dokument="A")                                                         # Protokoll vor dem Anschreiben
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "lesen_fehlt"))
        self.lesen(dokument="A")
        r = self.sign(1, h, dokument="A")
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "dokument_nicht_dran"))
        r = self.sign(2, h, dokument="B")                                                         # Person 2 vor Person 1
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "reihenfolge"))
        self.assertEqual(self.sign(1, h, dokument="B").status_code, 200)
        r = self.sign(1, h, dokument="B")
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "schon_unterschrieben"))
        self.assertEqual([(z["dokument_rolle"], z["person"]) for z in self.alle_zeilen()], [("B", 1)])

    def test_einzelperson(self):
        self.starten(personen=1)
        self.einloggen()
        self.lesen()
        h = self.texte_holen()["hash"]
        r = self.sign(1, h)                                                                       # Anschreiben
        self.assertEqual((r.status_code, r.get_json()), (200, {"fertig": False, "dokument_fertig": True, "naechste": {"dokument": "A", "person": 1}, "naechste_person": 1}))
        self.lesen()                                                                              # Protokoll
        r = self.sign(1, h)
        self.assertEqual((r.status_code, r.get_json()), (200, {"fertig": True}))
        self.assertEqual(self.sign(2, h, dokument="A").status_code, 401)
        self.assertEqual(len(self.alle_zeilen()), 2)

    def lesen_n(self, n=None):
        self.lesen()

    def test_nachweis_je_unterschrift(self):
        h = self.bis_zum_unterschreiben()
        info = self.texte_holen()
        self.sign(1, h)
        self.texte_holen()
        self.sign(2, h)
        z1, z2 = self.alle_zeilen()
        for z, p in ((z1, 1), (z2, 2)):
            self.assertEqual((z["person"], z["haken"], z["klick"], z["text_kennung"], z["text_hash"]), (p, 1, 1, info["kennung"], info["hash"]))
            self.assertEqual((z["ip"], z["geraet"]), ("127.0.0.1", "Safari/iOS"))
            self.assertRegex(z["zeit_unterschrift"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertLessEqual(z["zeit_texte_angezeigt"], z["zeit_unterschrift"])
        self.assertLessEqual(z1["zeit_unterschrift"], z2["zeit_unterschrift"])
        self.assertNotIn("AppleWebKit", self.db_text())                                      # nie der volle User-Agent
        self.assertNotIn("iPhone OS", self.db_bytes().decode("latin-1"))

    def test_ip_gekuerzt_wenn_so_eingestellt(self):
        self.starten()
        self.einloggen()
        self.lesen()
        h = self.texte_holen()["hash"]
        with mock.patch.dict(os.environ, {"SIGNNEXT_IP_SPEICHERUNG": "gekuerzt"}):
            r = self.post("/unterschrift", {"dokument": "B", "person": 1, "bild": uri(png_bytes()), "ich_unterschreibe": True, "haken": True, "text_hash": h}, kopf={"X-Forwarded-For": "198.51.100.77"})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.alle_zeilen()[0]["ip"], "198.51.100.0")

    def test_doppelklick_und_gleichzeitige_unterschrift(self):
        h = self.bis_zum_unterschreiben()
        self.assertEqual(self.sign(1, h).status_code, 200)
        self.texte_holen()
        r = self.sign(1, h)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "schon_unterschrieben"))
        self.assertEqual(len(self.alle_zeilen()), 1)

    def test_zwei_geraete_gleichzeitig_nur_eine_unterschrift_je_person(self):
        h = self.bis_zum_unterschreiben()
        c2 = self.app.test_client()
        self.assertEqual(self.pruefen(client=c2).status_code, 200)
        self.alle_seiten()
        for n in range(1, SEITEN_B + 1):
            self.hole("/seite/%d" % n, client=c2)
        self.post("/gelesen", {"bestaetigt": True}, client=c2)
        h2 = self.hole("/texte", client=c2).get_json()["hash"]
        antworten = []

        def los(c, hh):
            antworten.append(self.sign(1, hh, client=c).status_code)
        ts = [threading.Thread(target=los, args=(self.m, h)), threading.Thread(target=los, args=(c2, h2))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(sorted(antworten), [200, 409])
        self.assertEqual(len(self.alle_zeilen()), 1)

    def test_beratungsstelle_kann_nicht_fuer_das_mitglied_unterschreiben(self):
        self.starten()
        r = self.c.post("/api/v1/zugang/unterschrift", json={"person": 1}, headers=ORIGIN)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.alle_zeilen(), [])

    def test_nach_sperre_oder_ablauf_ist_die_sitzung_tot(self):
        h = self.bis_zum_unterschreiben()
        with self.zeit(days=8):
            self.assertEqual(self.sign(1, h, dokument="B").status_code, 401)
        with db.verbindung() as c:
            c.execute("UPDATE link SET gesperrt=1")
        self.assertEqual(self.sign(1, h, dokument="B").status_code, 401)
        self.assertEqual(self.alle_zeilen(), [])

    def test_nichts_geheimes_in_log_audit_und_antworten(self):
        h = self.bis_zum_unterschreiben()
        bild = uri(png_bytes())
        r = self.sign(1, h, bild=bild)
        gesamt = self.intern() + r.get_data(as_text=True)
        for geheim in (self.token, CODE, bild[30:80], pdfs.DOB, "AppleWebKit"):
            self.assertNotIn(geheim, gesamt, geheim)


# ======================================================================================================== Neuer Link

class TestNeuerLink(ZugangBasis):
    def neuer(self, vid=None, **kw):
        return self.c.post("/api/v1/vorgaenge/%d/neuer-link" % (vid or self.vid), json={"bestaetigt": True} if not kw else kw)

    def test_nach_sperre(self):
        self.starten()
        alt = self.token
        self.versuche_falsch(5)
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_json()["link_status"], "gesperrt")
        r = self.neuer()
        self.assertEqual((r.status_code, r.get_json()["status"], r.get_json()["neuer_link"]), (200, "versendet", True))
        self.assertEqual(len(self.srv.nachrichten), 2)
        neu = self.token_aus_mail(self.srv.nachrichten[1])
        self.assertNotEqual(neu, alt)
        self.assertEqual(self.pruefen(token=alt).get_json()["gesperrt"], True)               # alter Link bleibt tot
        self.assertEqual(self.pruefen(token=neu).status_code, 200)                           # neuer Link frisch: Zähler 0
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_json()["link_status"], "aktiv")
        with db.verbindung() as c:
            vz = [(z["ergebnis"], z["code"]) for z in c.execute("SELECT ergebnis, code FROM versand ORDER BY id")]
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link WHERE gesperrt=0").fetchone()[0], 1)
        self.assertEqual(vz, [("gesendet", ""), ("gesendet", "neuer_link")])
        self.assertEqual(self.srv.nachrichten[1]["an"], [pdfs.MAIL])
        self.assertEqual([x["details"] for x in self.audit_zeilen("neuer_link")], ["id=%d" % self.vid])

    def test_alte_sitzung_stirbt(self):
        self.starten()
        self.einloggen()
        self.assertEqual(self.hole("/status").status_code, 200)
        self.assertEqual(self.neuer().status_code, 200)
        self.assertEqual(self.hole("/status").status_code, 401)

    def test_nach_ablauf_des_links(self):
        self.starten()
        alt = self.token
        with self.zeit(days=8):
            self.assertEqual(self.pruefen().get_json()["gesperrt"], True)
        self.assertEqual(self.neuer().status_code, 200)
        neu = self.token_aus_mail(self.srv.nachrichten[1])
        self.assertEqual(self.pruefen(token=neu).status_code, 200)
        self.assertEqual(self.pruefen(token=alt).get_json()["gesperrt"], True)

    def test_vorbedingungen(self):
        self.vid = self.bereit()
        r = self.neuer()
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "zustand_passt_nicht"))      # noch nie versendet
        self.assertEqual(self.srv.nachrichten, [])
        self.senden_ok(self.vid)
        self.token = self.token_aus_mail(self.srv.nachrichten[0])
        for body in (None, {}, {"bestaetigt": False}, {"bestaetigt": "ja"}):
            r = self.c.post("/api/v1/vorgaenge/%d/neuer-link" % self.vid, json=body) if body is not None else self.c.post("/api/v1/vorgaenge/%d/neuer-link" % self.vid)
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual(len(self.srv.nachrichten), 1)
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.neuer().status_code, 404)
        self.c.delete_cookie("bst_session", domain="localhost")
        self.assertEqual(self.neuer().status_code, 401)

    def test_nach_der_unterschrift_nicht_mehr(self):
        h = None
        self.starten(personen=1)
        self.einloggen()
        self.alles_unterschreiben()
        r = self.neuer()
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "schon_unterschrieben"))
        self.assertEqual(len(self.srv.nachrichten), 1)

    def test_scheitert_der_versand_bleibt_der_alte_link_gueltig(self):
        self.starten()
        self.c.put("/api/v1/mail-zugang", json={"passwort": "falsch-3"})
        self.markiere_geprueft()
        r = self.neuer()
        self.assertEqual((r.status_code, r.get_json()["error"]), (502, "mail_anmeldung"))
        self.assertEqual(db.vorgang_intern("1001", self.vid)["status"], "versendet")           # Ausgangszustand bleibt
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link").fetchone()[0], 1)
        self.assertEqual(self.pruefen().status_code, 200)                                     # der alte Link funktioniert weiter

    def test_unklarer_versand_kein_automatischer_neuer_versuch(self):
        self.starten()
        srv2 = self.starte("implicit", nach_data_abbrechen=True)
        self.zugang(srv2)
        self.markiere_geprueft()
        r = self.neuer()
        self.assertEqual((r.status_code, r.get_json()["error"]), (502, "versand_unklar"))
        self.assertEqual(db.vorgang_intern("1001", self.vid)["status"], "versand_unklar")
        # der neue Link existiert (könnte angekommen sein), der alte ist NICHT ungültig gemacht worden
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link WHERE gesperrt=0").fetchone()[0], 2)
        self.assertEqual(self.pruefen().status_code, 200)                                     # auch ein Vorgang im Zustand versand_unklar ist über seinen Link erreichbar

    def test_zaehlt_fuer_das_stundenlimit(self):
        self.starten()
        with mock.patch("config.VERSAND_JE_STUNDE", 1):
            r = self.neuer()
            self.assertEqual((r.status_code, r.get_json()["error"]), (429, "zu_viele_versaende"))

    def test_schalter_gilt_auch_hier(self):
        self.starten()
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": ""}):
            self.assertEqual(self.neuer().status_code, 503)
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": "testadressen", "SIGNNEXT_VERSAND_TESTADRESSEN": "x@example.org"}):
            self.assertEqual(self.neuer().status_code, 409)
        self.assertEqual(len(self.srv.nachrichten), 1)


# ======================================================================================================== Einstellungen der Beratungsstelle

class TestEinstellungen(MailBasis):
    def test_setzen_lesen(self):
        self.assertTrue(self.c.get("/api/v1/einstellungen").get_json()["gesetzt"])
        with db.verbindung() as c:
            c.execute("DELETE FROM stelle_einstellung")
        self.assertEqual(self.c.get("/api/v1/einstellungen").get_json(), {"gesetzt": False})
        r = self.c.put("/api/v1/einstellungen", json={"verantwortlicher": "Beratungsstelle X, Weg 1, 12345 Ort, x@example.org", "datenschutz_link": "https://x.example/ds"})
        self.assertEqual((r.status_code, r.get_json()), (200, {"gesetzt": True}))
        j = self.c.get("/api/v1/einstellungen").get_json()
        self.assertEqual((j["verantwortlicher"], j["datenschutz_link"], j["gesetzt"]), ("Beratungsstelle X, Weg 1, 12345 Ort, x@example.org", "https://x.example/ds", True))

    def test_pruefung(self):
        gut = {"verantwortlicher": "Beratungsstelle X, Weg 1, 12345 Ort", "datenschutz_link": "https://x.example/ds"}
        for kaputt, feld in (({"verantwortlicher": ""}, "verantwortlicher"), ({"verantwortlicher": "x" * 501}, "verantwortlicher"), ({"verantwortlicher": "Zeile\x00"}, "verantwortlicher"),
                             ({"verantwortlicher": 5}, "verantwortlicher"), ({"datenschutz_link": "http://x.example/ds"}, "datenschutz_link"),
                             ({"datenschutz_link": "javascript:alert(1)"}, "datenschutz_link"), ({"datenschutz_link": "https://x.example/ds with space"}, "datenschutz_link"),
                             ({"datenschutz_link": "https://x.example/<script>"}, "datenschutz_link"), ({"datenschutz_link": ""}, "datenschutz_link")):
            r = self.c.put("/api/v1/einstellungen", json=dict(gut, **kaputt))
            self.assertEqual((r.status_code, r.get_json().get("feld")), (422, feld), kaputt)
        for body in ({}, dict(gut, extra=1), [], None):                                  # Teilmengen sind ab 0.5.3 erlaubt (siehe TestV053)
            self.assertEqual(self.c.put("/api/v1/einstellungen", json=body).status_code, 422, body)

    def test_nur_admin_und_nur_eigener_mandant(self):
        self.als("tm", me(email="m@test.example", rolle="mitarbeiter", ref="1001"))
        r = self.c.put("/api/v1/einstellungen", json={"verantwortlicher": "Beratungsstelle X, Weg 1", "datenschutz_link": "https://x.example/ds"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.get("/api/v1/einstellungen").status_code, 200)
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.c.get("/api/v1/einstellungen").get_json(), {"gesetzt": False})

    def test_loeschung_der_beratungsstelle(self):
        self.assertEqual(db.mandant_loeschen("1001")["stelle_einstellung"], 1)


# ======================================================================================================== Gerätekennung, Rendern

class TestGeraetUndRendern(unittest.TestCase):
    def test_geraetekennung(self):
        k = zugang.geraet_kennung
        self.assertEqual(k(IPHONE), "Safari/iOS")
        self.assertEqual(k("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"), "Chrome/Android")
        self.assertEqual(k("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0"), "Firefox/Windows")
        self.assertEqual(k("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"), "Edge/Windows")
        self.assertEqual(k("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"), "Safari/macOS")
        self.assertEqual(k("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"), "Chrome/Linux")
        self.assertEqual(k(""), "Sonstiger/Sonstiges")
        self.assertEqual(k(None), "Sonstiger/Sonstiges")
        self.assertEqual(k("curl/8.0"), "Sonstiger/Sonstiges")

    def test_echtes_rendern_im_kindprozess(self):
        import time
        daten = pdfs.a_pdf()
        t0 = time.perf_counter()
        bild = _echt_rendern(daten, 1)
        letzte = _echt_rendern(daten, 12)
        self.assertLess(time.perf_counter() - t0, 20)
        for b in (bild, letzte):
            self.assertTrue(b.startswith(b"\xff\xd8"))
            self.assertEqual(Image.open(io.BytesIO(b)).size[0], 834)
        with self.assertRaises(seitenbild.Fehler):
            _echt_rendern(daten, 13)
        with self.assertRaises(seitenbild.Fehler):
            _echt_rendern(daten, 0)
        with self.assertRaises(seitenbild.Fehler):
            _echt_rendern(b"kein pdf", 1)
        with self.assertRaises(seitenbild.Fehler):
            _echt_rendern(pdfs.mit_javascript(pdfs.b_pdf(), "js"), 1)                         # dieselbe Sicherheitsprüfung wie beim Eingang

    def test_zeitgrenze(self):
        with mock.patch("seitenbild.TIMEOUT_SEKUNDEN", 0.001):
            with self.assertRaises(seitenbild.Fehler):
                _echt_rendern(pdfs.a_pdf(), 1)

    def test_kindprozess_umgebung(self):
        quelle = open(seitenbild.__file__, encoding="utf-8").read()
        self.assertIn('"PATH": "/usr/local/bin:/usr/bin:/bin"', quelle)
        self.assertIn("timeout=TIMEOUT_SEKUNDEN", quelle)


if __name__ == "__main__":
    unittest.main()
