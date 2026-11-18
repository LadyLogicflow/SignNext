"""Baustein 5: Abschluss (Einbetten, Nachweisprotokoll), Download, Erinnerungen, Fristen und Löschung, Recovery, Löschprotokoll. Nur erfundene Daten."""
import email
import io
import json
import os
import re
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from pypdf import PdfReader

import abschluss
import config
import db
import jobs
import loeschen
import mailer
import mailtexte
from tests import pdfs
from tests.test_baustein3 import ABSENDER
from tests.test_baustein4 import BASIS, CODE, ORIGIN, SEITEN, ZugangBasis, png_bytes, uri


def jetzt(**delta):
    return datetime.now(timezone.utc) + timedelta(**delta)


class B5(ZugangBasis):
    ABSCHLUSS_AUS = False

    def unterschreiben(self, **kw):
        """Versendet, öffnet über den Link und unterschreibt alle Personen. Liefert die Vorgangsnummer."""
        self.starten(**kw)
        self.einloggen()
        self.lesen_n(SEITEN)
        h = self.texte_holen()["hash"]
        self.assertEqual(self.sign(1, h).status_code, 200)
        if db.vorgang_intern("1001", self.vid)["personen"] == 2:
            self.assertEqual(self.sign(2, self.texte_holen()["hash"], bild=uri(png_bytes(300, 100))).status_code, 200)
        return self.vid

    def lesen_n(self, n):
        self.alle_seiten(n)
        self.assertEqual(self.post("/gelesen", {"bestaetigt": True}).status_code, 200)

    def mails(self, an):
        out = []
        for n in self.srv.nachrichten:
            if an in n["an"]:
                m, text = self.mail_text(n)
                out.append((m["Subject"], text))
        return out

    def vorgang(self, vid=None):
        return db.vorgang_intern("1001", vid or self.vid)

    def rollen(self, vid=None):
        return sorted(d["rolle"] for d in db.dokumente_liste("1001", vid or self.vid))

    def dateien(self, vid=None):
        verz = db.dokumente_verzeichnis("1001") / str(vid or self.vid)
        return sorted(p.name for p in verz.glob("*")) if verz.is_dir() else []

    def protokoll(self):
        with db.verbindung() as c:
            return [dict(z) for z in c.execute("SELECT * FROM loeschprotokoll ORDER BY id")]

    def hole_download(self, token, kopf=None, ip="198.51.100.7", code=CODE):
        h = dict(ORIGIN)
        h.update(kopf or {})
        return self.m.post("/api/v1/download", json={"token": token, "code": code}, headers=h, base_url=BASIS, environ_overrides={"REMOTE_ADDR": ip})


# ======================================================================================================== Abschluss

class TestAbschluss(B5):
    def test_ehepaar_wird_abgeschlossen(self):
        self.starten()
        vorher = db.dokument_bytes("1001", self.vid, "M")
        self.einloggen()
        self.lesen_n(SEITEN)
        self.assertEqual(self.sign(1, self.texte_holen()["hash"]).status_code, 200)
        self.assertEqual(self.sign(2, self.texte_holen()["hash"], bild=uri(png_bytes(300, 100))).get_json(), {"fertig": True})
        v = self.vorgang()
        self.assertEqual(v["status"], "abgeschlossen")
        self.assertTrue(v["unterschrieben_am"] and v["abgeschlossen_am"] and v["aufbewahrung_bis"])
        self.assertEqual(v["aufbewahrung_tage"], config.aufbewahrung_standard())
        self.assertEqual(self.rollen(), ["N", "S"])                              # Originale (A, B, M) sind weg
        self.assertEqual(len(self.dateien()), 2)
        fertig = db.dokument_bytes("1001", self.vid, "S")
        leser = PdfReader(io.BytesIO(fertig))
        self.assertEqual(len(leser.pages), SEITEN)
        self.assertEqual({i + 1: len(p.images) for i, p in enumerate(leser.pages) if len(p.images)}, {1: 2, 4: 2, 15: 2})
        self.assertEqual(sum(len(p.images) for p in PdfReader(io.BytesIO(vorher)).pages), 0)
        self.assertNotEqual(fertig, vorher)
        # Die Texte bleiben unverändert (nur Overlay)
        self.assertEqual([p.extract_text() for p in leser.pages], [p.extract_text() for p in PdfReader(io.BytesIO(vorher)).pages])

    def test_einzelperson_nur_eine_stelle_je_block(self):
        self.unterschreiben(personen=1)
        leser = PdfReader(io.BytesIO(db.dokument_bytes("1001", self.vid, "S")))
        self.assertEqual({i + 1: len(p.images) for i, p in enumerate(leser.pages) if len(p.images)}, {1: 1, 4: 1, 15: 1})

    def test_unterschrift_liegt_in_der_stelle(self):
        self.starten()
        stellen = [s for d in db.dokumente_liste("1001", self.vid) if d["rolle"] == "M" for s in d["stellen"]]
        self.einloggen()
        self.lesen_n(SEITEN)
        self.sign(1, self.texte_holen()["hash"])
        self.sign(2, self.texte_holen()["hash"], bild=uri(png_bytes(300, 100)))
        leser = PdfReader(io.BytesIO(db.dokument_bytes("1001", self.vid, "S")))
        s1 = [s for s in stellen if s["seite"] == 1 and s["person"] == 1][0]
        bild = leser.pages[0].images[0]
        self.assertTrue(bild.data.startswith(b"\x89PNG") or len(bild.data) > 100)
        # Die Fläche der Stelle ist gleich oder größer als die Fläche des gesetzten Bildes: Das Bild wird in die Stelle eingepasst, nie darüber hinaus
        inhalt = leser.pages[0].get_contents().get_data().decode("latin-1")
        m = re.search(r"(?m)^([\d.]+) 0 0 ([\d.]+) ([\d.]+) ([\d.]+) cm\s*\n/FormXob", inhalt)
        self.assertIsNotNone(m)
        w, h, x, y = (float(v) for v in m.groups())
        self.assertLessEqual(w, s1["x1"] - s1["x0"] + 0.01)
        self.assertLessEqual(h, s1["y1"] - s1["y0"] + 0.01)
        self.assertGreaterEqual(x, s1["x0"] - 0.01)
        self.assertGreaterEqual(y, s1["y0"] - 0.01)
        self.assertAlmostEqual(w / h, 400 / 150, places=2)                       # Seitenverhältnis bleibt erhalten

    def test_nachweisprotokoll(self):
        self.starten()
        vorher = db.dokument_bytes("1001", self.vid, "M")
        self.einloggen()
        self.lesen_n(SEITEN)
        self.sign(1, self.texte_holen()["hash"])
        self.sign(2, self.texte_holen()["hash"])
        import hashlib
        nachweis = db.dokument_bytes("1001", self.vid, "N")
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(nachweis)).pages)
        self.assertIn("Vorgang Nr. %d" % self.vid, text)
        self.assertIn(hashlib.sha256(vorher).hexdigest(), text)                    # Hash vorher
        self.assertIn(hashlib.sha256(db.dokument_bytes("1001", self.vid, "S")).hexdigest(), text)    # Hash nachher
        self.assertIn("Unterschrift von Person 1", text)
        self.assertIn("Unterschrift von Person 2", text)
        self.assertRegex(text, r"\d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d (MEZ|MESZ)")
        self.assertIn("nicht kryptografisch signiert", text)
        self.assertNotIn("noch nicht freigegeben", text)                              # Texte sind freigegeben (catrin 18.11.): kein Vorbehalt im Nachweis
        self.assertNotIn("Freigabe offen", text)
        self.assertIn("SignNext-Datenschutzhinweis-Unterschriftsseite-1.0", text)
        self.assertIn("SignNext-Ausstieg-1.0", text)                                   # die lange Kennung läuft nicht über den Seitenrand (Umbruch)
        for seite in PdfReader(io.BytesIO(nachweis)).pages:
            for teil in seite.extract_text().splitlines():
                self.assertLess(len(teil), 140, teil)
        for verboten in pdfs.PERSONENDATEN + ("rechtssicher",):
            self.assertNotIn(verboten, text)

    def test_berlin_zeit(self):
        self.assertEqual(abschluss.berlin("2026-11-13T14:05:00Z"), "13.11.2026 15:05:00 MEZ")
        self.assertEqual(abschluss.berlin("2026-07-01T10:00:00Z"), "01.07.2026 12:00:00 MESZ")
        self.assertEqual(abschluss.berlin("2026-03-29T00:59:59Z"), "29.03.2026 01:59:59 MEZ")
        self.assertEqual(abschluss.berlin("2026-03-29T01:00:00Z"), "29.03.2026 03:00:00 MESZ")
        self.assertEqual(abschluss.berlin("2026-10-25T00:59:59Z"), "25.10.2026 02:59:59 MESZ")
        self.assertEqual(abschluss.berlin("2026-10-25T01:00:00Z"), "25.10.2026 02:00:00 MEZ")

    def test_originale_sind_geloescht_und_protokolliert(self):
        self.unterschreiben()
        p = [z for z in self.protokoll() if z["art"] == "originale"]
        self.assertEqual(len(p), 1)
        self.assertEqual(json.loads(p[0]["zahlen"]), {"dateien": 3})                # A, B, M
        self.assertEqual(p[0]["vorgang_id"], self.vid)

    def test_aufbewahrung_je_beratungsstelle(self):
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET aufbewahrung_tage=30")
        self.unterschreiben()
        v = self.vorgang()
        self.assertEqual(v["aufbewahrung_tage"], 30)
        bis = datetime.strptime(v["aufbewahrung_bis"], "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(v["abgeschlossen_am"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(bis, timedelta(days=30))

    def test_fehler_beim_einbetten_unterschrift_bleibt_gespeichert_und_job_holt_nach(self):
        with mock.patch("abschluss.einbetten", side_effect=RuntimeError("kaputt")):
            self.unterschreiben()
            self.assertEqual(self.vorgang()["status"], "abschluss_fehlgeschlagen")
            self.assertEqual(len(self.rollen()), 3)                                  # nichts gelöscht, Originale noch da
            with db.verbindung() as c:                                               # das wiederherstellbare Token ist trotzdem sofort weg
                self.assertEqual(c.execute("SELECT COUNT(*) FROM link WHERE token_enc IS NOT NULL").fetchone()[0], 0)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM unterschrift").fetchone()[0], 2)
        jobs.lauf(jetzt())
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")
        self.assertEqual(self.rollen(), ["N", "S"])

    def test_nach_fuenf_fehlversuchen_ist_schluss(self):
        with mock.patch("abschluss.einbetten", side_effect=RuntimeError("kaputt")) as e:
            self.unterschreiben()
            for _ in range(10):
                jobs.lauf(jetzt(minutes=5))
        self.assertEqual(self.vorgang()["abschluss_versuche"], config.ABSCHLUSS_VERSUCHE_MAX)
        self.assertEqual(e.call_count, config.ABSCHLUSS_VERSUCHE_MAX)
        self.assertEqual(self.vorgang()["status"], "abschluss_fehlgeschlagen")

    def test_nur_ein_abschluss_gewinnt(self):
        with mock.patch("abschluss.einbetten", side_effect=RuntimeError("kaputt")):
            self.unterschreiben()
        ergebnisse = []
        sperre = threading.Barrier(4)

        def lauf():
            sperre.wait()
            ergebnisse.append(abschluss.ausfuehren("1001", self.vid))
        ts = [threading.Thread(target=lauf) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(ergebnisse), [False, False, False, True])
        self.assertEqual(self.rollen(), ["N", "S"])
        self.assertEqual(len([z for z in self.protokoll() if z["art"] == "originale"]), 1)

    def test_haengender_abschluss_wird_zurueckgesetzt_und_nachgeholt(self):
        with mock.patch("abschluss.einbetten", side_effect=RuntimeError("kaputt")):
            self.unterschreiben()
        with db.verbindung() as c:
            c.execute("UPDATE vorgang SET status='abschluss_laeuft', abschluss_seit=?, abschluss_versuche=1", (db.jetzt(),))
        jobs.lauf(jetzt(minutes=5))                                                   # noch nicht lange genug
        self.assertEqual(self.vorgang()["status"], "abschluss_laeuft")
        jobs.lauf(jetzt(minutes=11))                                                  # Recovery: zurück auf fehlgeschlagen, dann Nachholung im selben Lauf
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")

    def test_reste_eines_abgebrochenen_abschlusses_werden_ersetzt(self):
        with mock.patch("abschluss.originale_loeschen", side_effect=RuntimeError("absturz")):
            self.unterschreiben()
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")
        self.assertEqual(self.rollen(), ["A", "B", "M", "N", "S"])
        jobs.lauf(jetzt())                                                           # Aufräumen holt die Originalöschung nach
        self.assertEqual(self.rollen(), ["N", "S"])
        self.assertEqual(len(self.dateien()), 2)

    def test_einbetten_lehnt_fehlende_stellen_ab(self):
        with self.assertRaises(abschluss.Fehler):
            abschluss.einbetten(db.dokument_bytes("1001", self.bereit(), "M"), [], 2, {1: b"x", 2: b"y"})
        self.starten()
        stellen = [s for d in db.dokumente_liste("1001", self.vid) if d["rolle"] == "M" for s in d["stellen"]]
        with self.assertRaises(abschluss.Fehler):
            abschluss.einbetten(db.dokument_bytes("1001", self.vid, "M"), stellen, 2, {1: b"x"})

    def test_kein_wiederherstellbares_token_nach_abschluss(self):
        self.starten()
        with db.verbindung() as c:
            self.assertIsNotNone(c.execute("SELECT token_enc FROM link").fetchone()["token_enc"])    # vorher vorhanden
        token = self.token
        self.einloggen()
        self.lesen_n(SEITEN)
        self.sign(1, self.texte_holen()["hash"])
        self.sign(2, self.texte_holen()["hash"])
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link WHERE token_enc IS NOT NULL").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link").fetchone()[0], 1)         # der Hash bleibt für die Sperrprüfung
        self.assertNotIn(token, self.intern())
        self.assertNotIn(token.encode("ascii"), self.db_bytes())


# ======================================================================================================== Token-Entzug

class TestTokenEntzug(B5):
    def test_nach_sperre_ist_das_token_weg(self):
        self.starten()
        self.versuche_falsch(5)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link WHERE token_enc IS NOT NULL").fetchone()[0], 0)
        self.assertNotIn(self.token.encode("ascii"), self.db_bytes())

    def test_neuer_link_entzieht_das_alte_token(self):
        self.starten()
        alt = self.token
        r = self.c.post("/api/v1/vorgaenge/%d/neuer-link" % self.vid, json={"bestaetigt": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link WHERE token_enc IS NOT NULL").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link WHERE gesperrt=1 AND token_enc IS NOT NULL").fetchone()[0], 0)
        self.assertNotIn(alt.encode("ascii"), self.db_bytes())

    def test_job_entzieht_abgelaufene_tokens(self):
        self.starten()
        with db.verbindung() as c:
            c.execute("UPDATE link SET laeuft_ab=?", (db.jetzt(),))
        jobs.token_entzug(jetzt(seconds=1))
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link WHERE token_enc IS NOT NULL").fetchone()[0], 0)

    def test_token_ist_nur_mit_dem_schluessel_der_beratungsstelle_lesbar(self):
        import schluessel
        self.starten()
        with db.verbindung() as c:
            enc = c.execute("SELECT token_enc FROM link").fetchone()["token_enc"]
        self.assertNotIn(self.token, enc)
        self.assertEqual(schluessel.entschluesseln("1001", "link-token", enc), self.token)
        with self.assertRaises(schluessel.Entschluesselung):
            schluessel.entschluesseln("2002", "link-token", enc)
        with self.assertRaises(schluessel.Entschluesselung):
            schluessel.entschluesseln("1001", "mitglied-mail", enc)


# ======================================================================================================== Download

class TestDownload(B5):
    def abgeschlossen(self, **kw):
        self.unterschreiben(**kw)
        jobs.lauf(jetzt())
        ms = self.mails(pdfs.MAIL)
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")
        return ms

    def link_aus_mail(self):
        ms = self.mails(pdfs.MAIL)
        return re.search(r"/d#([A-Za-z0-9_-]+)", ms[-1][1]).group(1)

    def test_mitglied_bekommt_bestaetigung_mit_link(self):
        ms = self.abgeschlossen()
        betreff, text = ms[-1]
        self.assertEqual(betreff, mailtexte.FERTIG_BETREFF)
        self.assertRegex(text, r"https://signnext\.example/d#[A-Za-z0-9_-]{43}")
        self.assertIn("nicht weiterleiten", text)
        self.assertEqual(self.vorgang()["mitglied_mail_am"] is not None, True)
        n = len(self.srv.nachrichten)
        jobs.lauf(jetzt(minutes=1))
        self.assertEqual(len(self.srv.nachrichten) - n, 0)                            # nur einmal

    def test_ohne_download_link_je_beratungsstelle(self):
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET mitglied_download_link=0")
        self.unterschreiben()
        jobs.lauf(jetzt())
        _, text = self.mails(pdfs.MAIL)[-1]
        self.assertNotIn("/d#", text)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM download_link").fetchone()[0], 0)

    def test_abruf_dreimal_dann_zu(self):
        self.abgeschlossen()
        token = self.link_aus_mail()
        for i in range(3):
            r = self.hole_download(token)
            self.assertEqual(r.status_code, 200, i)
            self.assertTrue(r.get_data().startswith(b"%PDF"))
        self.assertEqual(r.get_data(), db.dokument_bytes("1001", self.vid, "S"))
        r = self.hole_download(token)
        self.assertEqual((r.status_code, r.get_json()["error"], r.get_json()["gesperrt"]), (401, "angaben_stimmen_nicht", True))          # aufgebraucht, auch mit richtigem Code
        with db.verbindung() as c:
            self.assertEqual([z["zaehler"] for z in c.execute("SELECT zaehler FROM download_log ORDER BY id")], [1, 2, 3])

    def test_kopfzeilen(self):
        self.abgeschlossen()
        r = self.hole_download(self.link_aus_mail())
        self.assertIn("no-store", r.headers["Cache-Control"])
        self.assertIn("noindex", r.headers["X-Robots-Tag"])
        self.assertEqual(r.headers["Content-Type"], "application/pdf")
        self.assertTrue(r.headers["Content-Disposition"].startswith("attachment"))
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("vorgang", r.headers["Content-Disposition"].lower())         # kein Hinweis im Dateinamen

    def test_alle_unbrauchbaren_links_antworten_gleich(self):
        self.abgeschlossen()
        token = self.link_aus_mail()
        antworten = []
        antworten.append(self.hole_download("x" * 43))                                # unbekannt
        antworten.append(self.hole_download("kurz"))                                  # Form falsch
        with db.verbindung() as c:
            c.execute("UPDATE download_link SET laeuft_ab=?", (db.jetzt(),))
        antworten.append(self.hole_download(token))                                   # abgelaufen
        with db.verbindung() as c:
            c.execute("UPDATE download_link SET laeuft_ab='2099-01-01T00:00:00Z', gesperrt=1")
        antworten.append(self.hole_download(token))                                   # gesperrt
        with db.verbindung() as c:
            c.execute("UPDATE download_link SET gesperrt=0, abrufe=max_abrufe")
        antworten.append(self.hole_download(token))                                   # aufgebraucht
        self.assertEqual({a.status_code for a in antworten}, {401})
        self.assertEqual({a.get_json()["error"] for a in antworten}, {"angaben_stimmen_nicht"})
        self.assertEqual([a.get_json()["gesperrt"] for a in antworten[2:]], [True, True, True])                 # tote Links antworten wie "gesperrt"
        self.assertTrue(all(set(a.get_json()) == {"error", "meldung", "versuch", "von", "gesperrt"} for a in antworten))      # immer dieselbe Form

    def test_origin_pflicht_und_nur_post_mit_body(self):
        self.abgeschlossen()
        token = self.link_aus_mail()
        r = self.m.post("/api/v1/download", json={"token": token}, base_url=BASIS)
        self.assertEqual(r.status_code, 403)
        r = self.m.post("/api/v1/download", json={"token": token}, headers={"Origin": "https://boese.example"}, base_url=BASIS)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.m.get("/api/v1/download?token=" + token, base_url=BASIS).status_code, 405)
        for body in ({"token": token, "code": CODE, "x": 1}, {"token": token, "code": CODE, "download": True}, [], "x", None):
            self.assertEqual(self.hole_download_roh(body).status_code, 400, body)
        for body in ({}, {"token": 5}, {"token": token}, {"token": token, "code": 10260}, {"code": CODE}):
            self.assertEqual(self.hole_download_roh(body).status_code, 401, body)                              # kein Code oder keine Form: gezählter Fehlversuch, nie ein PDF
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM download_log").fetchone()[0], 0)

    def hole_download_roh(self, body):
        return self.m.post("/api/v1/download", json=body, headers=dict(ORIGIN), base_url=BASIS)

    def test_zaehlen_ist_atomar(self):
        self.abgeschlossen()
        token = self.link_aus_mail()
        codes = []
        sperre = threading.Barrier(8)

        def abruf(i):
            sperre.wait()
            codes.append(self.hole_download(token, ip="198.51.100.%d" % (10 + i)).status_code)
        ts = [threading.Thread(target=abruf, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(codes), [200, 200, 200] + [401] * 5)

    def test_ip_limit(self):
        self.abgeschlossen()
        for i in range(30):
            self.hole_download("y" * 43, ip="203.0.113.5")
        r = self.hole_download("y" * 43, ip="203.0.113.5")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.hole_download("y" * 43, ip="203.0.113.6").status_code, 401)

    # --- zweiter Faktor: Geburtsdatum wie beim Öffnen

    def test_download_ohne_richtigen_code_gibt_kein_pdf_und_zaehlt_keinen_abruf(self):
        self.abgeschlossen()
        token = self.link_aus_mail()
        for falsch in ("", "99999999", "01.02.1960", "0102196", "010219601", "abcdef", " 01021960", "010260"):
            r = self.hole_download(token, code=falsch)
            self.assertEqual(r.status_code, 401, falsch)
            self.assertNotIn(b"%PDF", r.get_data())
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM download_log").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT abrufe FROM download_link").fetchone()[0], 0)

    def test_versuch_x_von_5_und_dauerhafte_sperre(self):
        self.abgeschlossen()
        token = self.link_aus_mail()
        antworten = [self.hole_download(token, code="99999999").get_json() for _ in range(5)]
        self.assertEqual([(a["versuch"], a["von"], a["gesperrt"]) for a in antworten], [(1, 5, False), (2, 5, False), (3, 5, False), (4, 5, False), (5, 5, True)])
        r = self.hole_download(token)                                                 # der richtige Code hilft nach der Sperre nicht mehr
        self.assertEqual((r.status_code, r.get_json()["gesperrt"]), (401, True))
        self.assertNotIn(b"%PDF", r.get_data())
        with db.verbindung() as c:
            z = c.execute("SELECT gesperrt, fehlversuche FROM download_link").fetchone()
            self.assertEqual((z["gesperrt"], z["fehlversuche"]), (1, 5))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM download_log").fetchone()[0], 0)
        self.assertEqual(len(self.audit_zeilen("download_link_gesperrt")), 1)
        self.assertEqual(len(self.audit_zeilen("download_code_falsch")), 4)

    def test_richtiger_code_dazwischen_setzt_den_zaehler_nicht_zurueck(self):
        self.abgeschlossen()
        token = self.link_aus_mail()
        for _ in range(3):
            self.hole_download(token, code="99999999")
        self.assertEqual(self.hole_download(token).status_code, 200)
        self.assertEqual(self.hole_download(token, code="99999999").get_json()["versuch"], 4)

    def test_unbekannter_link_wird_genauso_gezaehlt_und_gesperrt(self):
        self.abgeschlossen()
        fremd = "z" * 43
        antworten = [self.hole_download(fremd).get_json() for _ in range(6)]
        self.assertEqual([a["versuch"] for a in antworten], [1, 2, 3, 4, 5, 5])
        self.assertEqual([a["gesperrt"] for a in antworten], [False] * 4 + [True, True])
        bekannt = self.hole_download(self.link_aus_mail(), code="99999999").get_json()
        self.assertEqual(set(bekannt), set(antworten[0]))                             # gleiche Form wie bei einem unbekannten Link

    def test_download_mail_nennt_den_zweiten_faktor_aber_nie_das_datum(self):
        self.abgeschlossen()
        _, text = self.mails(pdfs.MAIL)[-1]
        self.assertIn("Zum Abruf benötigen Sie wieder das Geburtsdatum der Person, für die das Dokument erstellt wurde", text)
        self.assertIn("acht Ziffern: Tag, Monat, vierstelliges Jahr, ohne Punkte", text)
        for verboten in (CODE, pdfs.DOB):
            self.assertNotIn(verboten, text)

    def test_download_der_beratungsstelle_hat_keinen_zweiten_faktor(self):
        self.abgeschlossen()
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/download/dokument" % self.vid).status_code, 200)

    def test_beratungsstelle_bekommt_info_nach_abruf_ohne_link(self):
        self.abgeschlossen()
        jobs.lauf(jetzt())
        vorher = len(self.mails(ABSENDER))
        self.hole_download(self.link_aus_mail())
        jobs.lauf(jetzt(minutes=1))
        ms = self.mails(ABSENDER)
        self.assertEqual(len(ms), vorher + 1)
        betreff, text = ms[-1]
        self.assertEqual(betreff, "SignNext: Hinweis zu Vorgang Nr. %d" % self.vid)
        self.assertIn("wurde über den Zugangslink abgerufen (Abruf 1 von 3). Waren Sie oder das Mitglied das nicht, melden Sie sich bitte beim Betreiber.", text)
        self.assertNotIn("/d#", text)
        jobs.lauf(jetzt(minutes=2))
        self.assertEqual(len(self.mails(ABSENDER)), vorher + 1)                       # nicht doppelt

    def test_beratungsstelle_info_nach_unterschrift_ohne_link_und_ohne_namen(self):
        self.abgeschlossen()
        ms = self.mails(ABSENDER)
        self.assertEqual(len(ms), 1)
        betreff, text = ms[0]
        self.assertIn("wurde unterschrieben", text)
        self.assertNotIn("http", text.split("Diese Nachricht")[0])
        for verboten in pdfs.PERSONENDATEN:
            self.assertNotIn(verboten, betreff + text)

    def test_download_der_beratungsstelle(self):
        self.abgeschlossen()
        r = self.c.get("/api/v1/vorgaenge/%d/download/dokument" % self.vid)
        self.assertEqual((r.status_code, r.headers["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(r.get_data(), db.dokument_bytes("1001", self.vid, "S"))
        r = self.c.get("/api/v1/vorgaenge/%d/download/nachweis" % self.vid)
        self.assertEqual(r.get_data(), db.dokument_bytes("1001", self.vid, "N"))
        for _ in range(5):                                                            # unbegrenzt
            self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/download/dokument" % self.vid).status_code, 200)
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/download/egal" % self.vid).status_code, 404)
        self.assertEqual(len(self.audit_zeilen("dokument_heruntergeladen")), 7)
        anonym = self.app.test_client()
        self.assertEqual(anonym.get("/api/v1/vorgaenge/%d/download/dokument" % self.vid).status_code, 401)

    def test_download_anderer_mandant_gibt_404(self):
        self.abgeschlossen()
        from tests.test_baustein1 import me
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/download/dokument" % self.vid).status_code, 404)

    def test_nicht_abgeschlossen_kein_download(self):
        self.starten()
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/download/dokument" % self.vid).status_code, 404)


# ======================================================================================================== Erinnerungen

class TestErinnerungen(B5):
    def anzahl_mails(self):
        return len(self.mails(pdfs.MAIL))

    def test_sechs_erinnerungen_werden_geplant(self):
        self.starten()
        with db.verbindung() as c:
            zeilen = [dict(z) for z in c.execute("SELECT * FROM erinnerung ORDER BY nr")]
        self.assertEqual([z["nr"] for z in zeilen], [1, 2, 3, 4, 5, 6])
        v = datetime.strptime(self.vorgang()["versendet_am"], "%Y-%m-%dT%H:%M:%SZ")
        for z, h in zip(zeilen, (48, 72, 96, 120, 144, 156)):
            d = datetime.strptime(z["faellig_am"], "%Y-%m-%dT%H:%M:%SZ") - v
            self.assertLess(abs(d - timedelta(hours=h)), timedelta(seconds=5))

    def test_erste_erinnerung_mit_demselben_link(self):
        self.starten()
        self.assertEqual(self.anzahl_mails(), 1)
        jobs.lauf(jetzt(hours=47))
        self.assertEqual(self.anzahl_mails(), 1)
        jobs.lauf(jetzt(hours=49))
        ms = self.mails(pdfs.MAIL)
        self.assertEqual(len(ms), 2)
        betreff, text = ms[1]
        self.assertEqual(betreff, mailtexte.ERINNERUNG_BETREFF)
        self.assertIn("/u#" + self.token, text)
        self.assertIn("Haben Sie diese Nachricht nicht erwartet", text)
        self.assertIn("bitte nicht weiterleiten", text)
        self.assertIn("Zum Öffnen benötigen Sie das Geburtsdatum", text)
        self.assertNotIn("{", text)                                                  # keine offenen Platzhalter
        jobs.lauf(jetzt(hours=49, minutes=1))
        self.assertEqual(self.anzahl_mails(), 2)

    def test_hoechstens_eine_je_lauf_verpasste_werden_uebersprungen(self):
        self.starten()
        jobs.lauf(jetzt(hours=100))                                                   # 48, 72, 96 fällig: gesendet wird die neueste
        self.assertEqual(self.anzahl_mails(), 2)
        with db.verbindung() as c:
            self.assertEqual([(z["nr"], z["ergebnis"]) for z in c.execute("SELECT nr, ergebnis FROM erinnerung ORDER BY nr")],
                             [(1, "uebersprungen"), (2, "uebersprungen"), (3, "gesendet"), (4, None), (5, None), (6, None)])

    def test_letzte_erinnerung_hat_eigenen_text(self):
        self.starten()
        jobs.lauf(jetzt(hours=157))
        _, text = self.mails(pdfs.MAIL)[-1]
        self.assertIn("Letzte Erinnerung", text)
        self.assertIn("/u#" + self.token, text)
        self.assertNotIn("{", text)

    def test_alle_sechs_nacheinander(self):
        self.starten()
        for h in (49, 73, 97, 121, 145, 157):
            jobs.lauf(jetzt(hours=h))
        self.assertEqual(self.anzahl_mails(), 7)
        with db.verbindung() as c:
            self.assertEqual({z["ergebnis"] for z in c.execute("SELECT ergebnis FROM erinnerung")}, {"gesendet"})

    def test_keine_erinnerung_nach_unterschrift_sperre_ablauf_oder_abschaltung(self):
        self.starten()
        self.versuche_falsch(5)
        jobs.lauf(jetzt(hours=60))
        self.assertEqual(self.anzahl_mails(), 1)                                      # gesperrt
        self.c.post("/api/v1/vorgaenge/%d/neuer-link" % self.vid, json={"bestaetigt": True})
        n = self.anzahl_mails()
        r = self.c.put("/api/v1/vorgaenge/%d/erinnerungen" % self.vid, json={"an": False})
        self.assertEqual(r.get_json(), {"vorgang_id": self.vid, "erinnerungen_aus": True})
        jobs.lauf(jetzt(hours=60))
        self.assertEqual(self.anzahl_mails(), n)                                      # abgeschaltet
        self.c.put("/api/v1/vorgaenge/%d/erinnerungen" % self.vid, json={"an": True})
        jobs.erinnerungen(jetzt(hours=200))                                           # nur der Erinnerungsschritt (der Gesamtlauf würde den Vorgang löschen)
        self.assertEqual(self.anzahl_mails(), n)                                      # Link abgelaufen
        jobs.lauf(jetzt(hours=60))
        self.assertEqual(self.anzahl_mails(), n + 1)                                  # an, Link gültig: jetzt geht sie

    def test_nach_unterschrift_gibt_es_keine_offenen_erinnerungen(self):
        self.unterschreiben()
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM erinnerung WHERE ergebnis IS NULL").fetchone()[0], 0)
        jobs.lauf(jetzt(hours=100))
        self.assertEqual([b for b, _ in self.mails(pdfs.MAIL) if b == mailtexte.ERINNERUNG_BETREFF], [])      # nur Link-Mail und Bestätigung

    def test_schalter_aus_verbraucht_nichts(self):
        self.starten()
        n = len(self.srv.nachrichten)
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": "aus"}):
            jobs.lauf(jetzt(hours=60))
        self.assertEqual(len(self.srv.nachrichten), n)
        with db.verbindung() as c:
            self.assertEqual([(z["versuche"], z["ergebnis"]) for z in c.execute("SELECT versuche, ergebnis FROM erinnerung WHERE nr=1")], [(0, None)])
        jobs.lauf(jetzt(hours=60))
        self.assertEqual(len(self.srv.nachrichten), n + 1)

    def test_testadressen_nur_freigegebene(self):
        self.starten()
        n = len(self.srv.nachrichten)
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": "testadressen", "SIGNNEXT_VERSAND_TESTADRESSEN": "andere@example.org"}):
            jobs.lauf(jetzt(hours=60))
        self.assertEqual(len(self.srv.nachrichten), n)
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": "testadressen", "SIGNNEXT_VERSAND_TESTADRESSEN": pdfs.MAIL}):
            jobs.lauf(jetzt(hours=60))
        self.assertEqual(len(self.srv.nachrichten), n + 1)

    def test_unklarer_versand_wird_nie_wiederholt(self):
        self.starten()
        with mock.patch("mailer.senden", side_effect=mailer.MailFehler("unklar", definitiv=False)):
            jobs.lauf(jetzt(hours=60))
        n = len(self.srv.nachrichten)
        for minuten in (6, 12, 30, 90):
            jobs.lauf(jetzt(hours=60, minutes=minuten))
        self.assertEqual(len(self.srv.nachrichten), n)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT ergebnis, versuche FROM erinnerung WHERE nr=1").fetchone()[0], "unklar")

    def test_eindeutiger_fehler_drei_versuche_im_abstand(self):
        self.starten()
        n = len(self.srv.nachrichten)
        with mock.patch("mailer.senden", side_effect=mailer.MailFehler("verbindung")) as s:
            jobs.lauf(jetzt(hours=60))
            jobs.lauf(jetzt(hours=60, minutes=1))                                     # zu früh
            self.assertEqual(s.call_count, 1)
            jobs.lauf(jetzt(hours=60, minutes=6))
            jobs.lauf(jetzt(hours=60, minutes=12))
            jobs.lauf(jetzt(hours=60, minutes=30))                                    # drei Versuche sind aufgebraucht
            self.assertEqual(s.call_count, 3)
        with db.verbindung() as c:
            z = c.execute("SELECT ergebnis, versuche FROM erinnerung WHERE nr=1").fetchone()
            self.assertEqual((z["ergebnis"], z["versuche"]), ("fehlgeschlagen", 3))
        self.assertEqual(len(self.srv.nachrichten), n)

    def test_gleichzeitige_laeufe_senden_nur_einmal(self):
        self.starten()
        n = len(self.srv.nachrichten)
        sperre = threading.Barrier(4)

        def lauf():
            sperre.wait()
            jobs.erinnerungen(jetzt(hours=60))
        ts = [threading.Thread(target=lauf) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(self.srv.nachrichten), n + 1)

    def test_stundenlimit_gilt_auch_fuer_automatische_mails(self):
        self.starten()
        with db.verbindung() as c:
            for _ in range(config.VERSAND_JE_STUNDE):
                c.execute("INSERT INTO versand(vorgang_id, mandant_ref, zeit, ergebnis, code, message_id) VALUES (?,?,?,?,?,?)", (self.vid, "1001", db.jetzt(), "gesendet", "", ""))
        n = len(self.srv.nachrichten)
        jobs.lauf(jetzt(hours=60))
        self.assertEqual(len(self.srv.nachrichten), n)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT versuche FROM erinnerung WHERE nr=1").fetchone()[0], 0)

    def test_erinnerungen_schalten_nur_im_eigenen_mandanten_und_validiert(self):
        self.starten()
        for body in ({}, {"an": "ja"}, {"an": 1}, {"an": True, "x": 1}, None):
            self.assertEqual(self.c.put("/api/v1/vorgaenge/%d/erinnerungen" % self.vid, json=body).status_code, 422, body)
        from tests.test_baustein1 import me
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.c.put("/api/v1/vorgaenge/%d/erinnerungen" % self.vid, json={"an": False}).status_code, 404)
        self.assertEqual(self.vorgang()["erinnerungen_aus"], 0)

    def test_neuer_link_plant_neu(self):
        self.starten()
        jobs.lauf(jetzt(hours=60))
        self.c.post("/api/v1/vorgaenge/%d/neuer-link" % self.vid, json={"bestaetigt": True})
        with db.verbindung() as c:
            self.assertEqual([z["ergebnis"] for z in c.execute("SELECT ergebnis FROM erinnerung ORDER BY nr")], [None] * 6)

    def test_erinnerungstext_neutral(self):
        for text in (mailtexte.ERINNERUNG_TEXT, mailtexte.ERINNERUNG_LETZTE_TEXT, mailtexte.FERTIG_TEXT_MIT_LINK, mailtexte.FERTIG_TEXT_OHNE_LINK):
            for verboten in ("rechtssicher", "Frist", "Mahnung", "Strafe", "Euro", "Finanzamt"):
                self.assertNotIn(verboten, text)
        for text in (mailtexte.ERINNERUNG_TEXT, mailtexte.ERINNERUNG_LETZTE_TEXT):
            self.assertNotIn("sofort", text)                                          # kein Zeitdruck in den Erinnerungen (der Missbrauchshinweis in den Bestätigungen darf "sofort" sagen)
        for b in (mailtexte.ERINNERUNG_BETREFF, mailtexte.FERTIG_BETREFF):
            self.assertNotIn("Steuer", b)


# ======================================================================================================== Fristen und Löschung

class TestFristen(B5):
    def geloescht_pruefen(self, vid, grund):
        v = self.vorgang(vid)
        self.assertEqual((v["status"], v["geloescht_grund"]), ("geloescht", grund))
        self.assertTrue(v["geloescht_am"])
        for feld in ("email_enc", "code_hmac", "code_kid", "abgleich", "email_herkunft", "datum_herkunft", "personen_herkunft"):
            self.assertIsNone(v[feld], feld)
        self.assertEqual(self.dateien(vid), [])
        self.assertFalse((db.dokumente_verzeichnis("1001") / str(vid)).exists())
        with db.verbindung() as c:
            for t in ("sitzung", "unterschrift", "dokument", "link", "erinnerung", "download_link", "download_log"):
                self.assertEqual(c.execute("SELECT COUNT(*) FROM %s WHERE vorgang_id=?" % t, (vid,)).fetchone()[0], 0, t)
        p = [z for z in self.protokoll() if z["vorgang_id"] == vid and z["art"] == grund]
        self.assertEqual(len(p), 1)
        zahlen = json.loads(p[0]["zahlen"])
        self.assertTrue(all(isinstance(x, int) for x in zahlen.values()))
        self.assertTrue(all(re.fullmatch(r"[a-z_]+", k) for k in zahlen))

    def test_nicht_unterschrieben_vorwarnung_dann_loeschung(self):
        self.starten()
        n = len(self.mails(ABSENDER))
        jobs.lauf(jetzt(days=5))
        self.assertEqual(len(self.mails(ABSENDER)), n)                                # noch zu früh
        jobs.lauf(jetzt(days=6, hours=2))
        ms = self.mails(ABSENDER)
        self.assertEqual(len(ms), n + 1)
        with db.verbindung() as c:
            ablauf = c.execute("SELECT laeuft_ab FROM link").fetchone()[0]
        self.assertIn("Der Link läuft am %s ab" % abschluss.berlin(ablauf), ms[-1][1])      # der tatsächliche Zeitpunkt, nicht "in etwa 24 Stunden"
        self.assertNotIn("24 Stunden", ms[-1][1])
        self.assertEqual(self.vorgang()["status"], "versendet")
        jobs.lauf(jetzt(days=6, hours=3))
        self.assertEqual(len(self.mails(ABSENDER)), n + 1)                            # Vorwarnung nur einmal
        jobs.lauf(jetzt(days=7, minutes=5))
        self.geloescht_pruefen(self.vid, "nicht_unterschrieben")
        self.assertEqual(len(self.mails(ABSENDER)), n + 2)
        self.assertIn("nicht unterschrieben und gelöscht", self.mails(ABSENDER)[-1][1])
        jobs.lauf(jetzt(days=8))                                                       # wiederholbar, nichts Neues
        self.assertEqual(len(self.mails(ABSENDER)), n + 2)
        self.assertEqual(len([z for z in self.protokoll() if z["art"] == "nicht_unterschrieben"]), 1)

    def test_zaehleintrag_bleibt(self):
        self.starten()
        jobs.lauf(jetzt(days=8))
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM vorgang WHERE id=?", (self.vid,)).fetchone()[0], 1)

    def test_neuer_link_verschiebt_die_loeschung(self):
        self.starten()
        with db.verbindung() as c:                                                    # Zustand "Tag 6": der Link läuft in 22 Stunden ab
            c.execute("UPDATE link SET laeuft_ab=?", (jetzt(hours=22).strftime("%Y-%m-%dT%H:%M:%SZ"),))
        jobs.lauf(jetzt())
        self.assertIsNotNone(self.vorgang()["vorwarnung_am"])
        self.c.post("/api/v1/vorgaenge/%d/neuer-link" % self.vid, json={"bestaetigt": True})
        self.assertIsNone(self.vorgang()["vorwarnung_am"])
        jobs.lauf(jetzt(days=1))
        self.assertEqual(self.vorgang()["status"], "versendet")                       # der neue Link läuft erst später ab
        jobs.lauf(jetzt(days=7, minutes=5))
        self.assertEqual(self.vorgang()["status"], "geloescht")

    def test_nie_versendet_nach_sieben_tagen(self):
        vid = self.bereit()
        n = len(self.mails(ABSENDER))
        jobs.lauf(jetzt(days=6, hours=2))
        self.assertEqual(self.vorgang(vid)["status"], "bereit")
        jobs.lauf(jetzt(days=7, minutes=5))
        self.geloescht_pruefen(vid, "nicht_versendet")

    def test_aufbewahrung_und_vorwarnung(self):
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET aufbewahrung_tage=30")
        self.unterschreiben()
        jobs.lauf(jetzt())
        n = len(self.mails(ABSENDER))
        jobs.lauf(jetzt(days=15))
        self.assertEqual(len(self.mails(ABSENDER)), n)
        jobs.lauf(jetzt(days=16, hours=1))
        ms = self.mails(ABSENDER)
        self.assertEqual(len(ms), n + 1)
        self.assertIn("werden am", ms[-1][1])
        self.assertIn("vorher herunter", ms[-1][1])
        jobs.lauf(jetzt(days=17))
        self.assertEqual(len(self.mails(ABSENDER)), n + 1)
        jobs.lauf(jetzt(days=29))
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")
        jobs.lauf(jetzt(days=30, minutes=5))
        self.geloescht_pruefen(self.vid, "aufbewahrung")
        self.assertIn("wurden nach Ablauf der Aufbewahrungsfrist gelöscht", self.mails(ABSENDER)[-1][1])
        self.assertEqual(db.dokument_bytes("1001", self.vid, "S"), None)

    def test_aenderung_der_frist_wirkt_nicht_rueckwirkend(self):
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET aufbewahrung_tage=90")
        self.unterschreiben()
        r = self.c.put("/api/v1/einstellungen", json={"verantwortlicher": "Beratungsstelle Beispiel, Musterweg 1", "datenschutz_link": "https://bst.example/ds", "aufbewahrung_tage": 30})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.vorgang()["aufbewahrung_tage"], 90)
        jobs.lauf(jetzt(days=40))
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")                   # keine rückwirkende Verkürzung

    def test_einstellungen_aufbewahrung_validierung(self):
        basis = {"verantwortlicher": "Beratungsstelle Beispiel, Musterweg 1", "datenschutz_link": "https://bst.example/ds"}
        for falsch in (29, 366, 0, -5, 90.5, "90", True):
            r = self.c.put("/api/v1/einstellungen", json=dict(basis, aufbewahrung_tage=falsch))
            self.assertEqual((r.status_code, r.get_json().get("feld")), (422, "aufbewahrung_tage"), falsch)
        for ok in (30, 365, 90):
            self.assertEqual(self.c.put("/api/v1/einstellungen", json=dict(basis, aufbewahrung_tage=ok)).status_code, 200, ok)
            self.assertEqual(self.c.get("/api/v1/einstellungen").get_json()["aufbewahrung_tage"], ok)
        self.assertEqual(self.c.put("/api/v1/einstellungen", json=dict(basis, mitglied_download_link="ja")).status_code, 422)
        self.assertEqual(self.c.put("/api/v1/einstellungen", json=dict(basis, mitglied_download_link=False)).status_code, 200)
        self.assertEqual(self.c.get("/api/v1/einstellungen").get_json()["mitglied_download_link"], False)
        self.assertEqual(self.c.put("/api/v1/einstellungen", json=dict(basis)).status_code, 200)             # ohne die Felder bleiben die Werte
        self.assertEqual(self.c.get("/api/v1/einstellungen").get_json()["aufbewahrung_tage"], 90)
        self.assertEqual(self.c.put("/api/v1/einstellungen", json=dict(basis, extra=1)).status_code, 422)
        self.assertEqual(self.c.get("/api/v1/einstellungen").get_json()["mitglied_download_link"], False)

    def test_aufbewahrung_nur_fuer_admin(self):
        from tests.test_baustein1 import me
        self.als("tm", me(email="m@test.example", rolle="mitarbeiter", ref="1001"))
        r = self.c.put("/api/v1/einstellungen", json={"verantwortlicher": "Beratungsstelle Beispiel, Musterweg 1", "datenschutz_link": "https://bst.example/ds", "aufbewahrung_tage": 30})
        self.assertEqual(r.status_code, 403)

    def test_loeschung_geht_auch_bei_abgeschaltetem_versand(self):
        self.starten()
        with mock.patch.dict(os.environ, {"SIGNNEXT_VERSAND": "aus"}):
            jobs.lauf(jetzt(days=8))
        self.geloescht_pruefen(self.vid, "nicht_unterschrieben")

    def test_waechter_loeschung_kennt_jede_tabelle_mit_vorgang_id(self):
        """Wer eine neue Tabelle mit vorgang_id anlegt, muss sie hier (loeschen.py) berücksichtigen oder bewusst ausnehmen."""
        with db.verbindung() as c:
            tabellen = [z["name"] for z in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            mit = [t for t in tabellen if any(sp["name"] == "vorgang_id" for sp in c.execute("PRAGMA table_info(%s)" % t))]
        ausnahmen = {"vorgang", "versand", "audit_log", "loeschprotokoll"}
        self.assertEqual(sorted(set(mit) - ausnahmen), sorted(["sitzung", "unterschrift", "dokument", "link", "erinnerung", "download_link", "download_log", "handoff", "handoff_log"]))
        self.starten()
        quelle = open(loeschen.__file__, encoding="utf-8").read()
        for t in set(mit) - ausnahmen:
            self.assertIn('"%s"' % t, quelle, t)

    def test_unbekannter_loeschgrund_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            loeschen.vorgang_loeschen("1001", 1, "weil ich will")


# ======================================================================================================== Recovery bei Prozessabbruch im Versand

class TestRecovery(B5):
    def test_versenden_haengt(self):
        vid = self.bereit()
        with db.verbindung() as c:
            c.execute("UPDATE vorgang SET status='versenden', versenden_seit=? WHERE id=?", (db.jetzt(), vid))
        jobs.lauf(jetzt(minutes=5))
        self.assertEqual(self.vorgang(vid)["status"], "versenden")
        jobs.lauf(jetzt(minutes=11))
        v = self.vorgang(vid)
        self.assertEqual((v["status"], v["versand_fehler"]), ("versand_unklar", "unklar"))
        with db.verbindung() as c:
            z = c.execute("SELECT ergebnis, code FROM versand WHERE vorgang_id=?", (vid,)).fetchone()
        self.assertEqual((z["ergebnis"], z["code"]), ("unklar", "abbruch"))
        self.assertEqual(len(self.audit_zeilen("versand_abbruch")), 1)
        jobs.lauf(jetzt(minutes=20))
        self.assertEqual(len(self.audit_zeilen("versand_abbruch")), 1)

    def test_versand_unklar_wird_nie_automatisch_wiederholt(self):
        vid = self.bereit()
        with db.verbindung() as c:
            c.execute("UPDATE vorgang SET status='versenden', versenden_seit=NULL WHERE id=?", (vid,))
        n = len(self.srv.nachrichten)
        jobs.lauf(jetzt())
        jobs.lauf(jetzt(hours=3))
        self.assertEqual(len(self.srv.nachrichten), n)
        self.assertEqual(self.vorgang(vid)["status"], "versand_unklar")

    def test_versenden_setzt_den_zeitstempel(self):
        self.starten()
        self.assertTrue(self.vorgang()["versenden_seit"])


# ======================================================================================================== Löschprotokoll

class TestLoeschprotokoll(B5):
    def test_nur_zahlen(self):
        with db.verbindung() as c:
            for zahlen in ({"name": "Quentin"}, {"x": True}, {"x": 1.5}, {"Groß": 1}, {"a b": 1}, {"datei": "x.pdf"}, {"x" * 31: 1}):
                with self.assertRaises(ValueError, msg=str(zahlen)):
                    db.loeschprotokoll_schreiben(c, "storno", "1001", 1, zahlen)
            db.loeschprotokoll_schreiben(c, "storno", "1001", 1, {"dateien": 3, "unterschriften": 2})
        z = self.protokoll()[-1]
        self.assertEqual(json.loads(z["zahlen"]), {"dateien": 3, "unterschriften": 2})
        self.assertEqual(set(z), {"id", "zeit", "art", "mandant_ref", "vorgang_id", "zahlen", "loeschen_nach"})

    def test_frist_drei_jahre(self):
        with db.verbindung() as c:
            db.loeschprotokoll_schreiben(c, "storno", "1001", 1, {"dateien": 1}, datetime(2026, 11, 13, tzinfo=timezone.utc))
            db.loeschprotokoll_schreiben(c, "storno", "1001", 2, {"dateien": 1}, datetime(2028, 2, 29, tzinfo=timezone.utc))
        a, b = self.protokoll()
        self.assertEqual((a["loeschen_nach"], b["loeschen_nach"]), ("2029-11-13T00:00:00Z", "2031-02-28T00:00:00Z"))
        self.assertEqual((db.LOESCHPROTOKOLL_JAHRE, db.LOESCHPROTOKOLL_BLEIBT), (3, True))

    def test_bleibt_nach_vertragsende(self):
        self.starten()
        jobs.lauf(jetzt(days=8))
        vorher = len(self.protokoll())
        self.assertGreaterEqual(vorher, 1)
        zahlen = db.mandant_loeschen("1001")
        self.assertNotIn("loeschprotokoll", zahlen)
        nachher = self.protokoll()
        self.assertEqual(len(nachher), vorher + 1)                                     # alter Eintrag bleibt, Vertragsende kommt dazu
        self.assertEqual(nachher[-1]["art"], "vertragsende")
        self.assertEqual(self.vorgang_zeilen(), 1)                                     # der gezählte Vorgang bleibt als Zähleintrag ohne Inhalt (Baustein 6)
        self.assertEqual(self.vorgang()["status"], "geloescht")
        self.assertIsNone(self.vorgang()["email_enc"])

    def vorgang_zeilen(self):
        with db.verbindung() as c:
            return c.execute("SELECT COUNT(*) FROM vorgang WHERE mandant_ref='1001'").fetchone()[0]

    def test_wird_nach_ablauf_der_frist_geloescht(self):
        with db.verbindung() as c:
            db.loeschprotokoll_schreiben(c, "storno", "1001", 1, {"dateien": 1}, datetime(2020, 1, 1, tzinfo=timezone.utc))
            db.loeschprotokoll_schreiben(c, "storno", "1001", 2, {"dateien": 1})
        jobs.lauf(jetzt())
        self.assertEqual([z["vorgang_id"] for z in self.protokoll()], [2])

    def test_enthaelt_keine_personendaten_nach_kompletten_ablauf(self):
        self.unterschreiben()
        jobs.lauf(jetzt(days=100))
        inhalt = json.dumps(self.protokoll())
        for verboten in pdfs.PERSONENDATEN + (".pdf", "@"):
            self.assertNotIn(verboten, inhalt)

    def test_loeschung_von_anfang_an_kein_personenbezug_in_der_datenbank(self):
        self.unterschreiben()
        jobs.lauf(jetzt(days=100))
        self.assertEqual(self.vorgang()["status"], "geloescht")
        roh = self.db_text() + self.db_bytes().decode("latin-1")
        for verboten in (pdfs.MAIL, pdfs.NAME, pdfs.VORNAME, pdfs.MNR, pdfs.ID_A, pdfs.STNR, self.token):
            self.assertNotIn(verboten, roh)
        self.assertEqual(os.listdir(db.dokumente_verzeichnis("1001")) if db.dokumente_verzeichnis("1001").is_dir() else [], [])


# ======================================================================================================== Datenschutzinformation nennt die Frist der Beratungsstelle

class TestFristImText(B5):
    def text(self):
        self.starten()
        self.einloggen()
        self.lesen_n(SEITEN)
        return self.texte_holen()["texte"]["datenschutz"]

    def test_text_nennt_die_frist_der_beratungsstelle(self):
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET aufbewahrung_tage=30")
        t = self.text()
        self.assertIn("30 T", t)
        self.assertNotIn("90 T", t)

    def test_ohne_eigene_frist_gilt_der_standard(self):
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET aufbewahrung_tage=NULL")
        self.assertIn("%d T" % config.aufbewahrung_standard(), self.text())

    def test_satz_zur_aufbewahrung_wortlaut(self):
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET aufbewahrung_tage=30")
        self.assertIn("Das unterschriebene Dokument und das Protokoll werden 30 Tage nach der Unterschrift gelöscht; Ihre Beratungsstelle erhält beides zum Herunterladen.", self.text())

    def test_bestaetigung_ohne_link_spricht_nicht_von_einem_link(self):
        self.assertNotIn("Link", mailtexte.FERTIG_TEXT_OHNE_LINK)
        self.assertIn("Link", mailtexte.FERTIG_TEXT_MIT_LINK)
        for t in (mailtexte.FERTIG_TEXT_MIT_LINK, mailtexte.FERTIG_TEXT_OHNE_LINK):
            self.assertIn("Haben Sie nicht selbst unterschrieben, wenden Sie sich bitte sofort an Ihre Beratungsstelle.", t)

    def test_erste_erinnerung_ohne_steuerbezug(self):
        self.assertNotIn("Finanzamt", mailtexte.ERINNERUNG_TEXT)
        self.assertIn("Sie wird benötigt, damit Ihre Beratungsstelle weiterarbeiten kann.", mailtexte.ERINNERUNG_TEXT)

    def test_letzte_erinnerung_eigener_absatz_und_berechnete_stunden(self):
        self.starten()
        jobs.lauf(jetzt(hours=157))
        _, text = self.mails(pdfs.MAIL)[-1]
        self.assertTrue(text.startswith("Guten Tag,\n\nLetzte Erinnerung\n\n"), text[:60])
        with db.verbindung() as c:
            ablauf = c.execute("SELECT laeuft_ab FROM link").fetchone()[0]
        self.assertIn("bis %s gültig" % abschluss.berlin(ablauf), text)
        stunden = int(re.search(r"in etwa (\d+) Stunden", text).group(1))
        self.assertIn(stunden, (10, 11))                                                # 168 h - 157 h, aus dem Ablaufzeitpunkt berechnet
        self.assertNotIn("12 Stunden", text)

    def test_hinweis_kontakt_des_betreibers_optional(self):
        with mock.patch.dict(os.environ, {"SIGNNEXT_BETREIBER_KONTAKT": "support@betreiber.example\nBcc: x@y"}):
            t = mailtexte.stelle_text("abruf", 5, zaehler=1, max=3)
        self.assertIn("beim Betreiber (support@betreiber.example Bcc: x@y).", t)
        self.assertNotIn("\nBcc", t)


# ======================================================================================================== v0.5.3: Teil-Einstellungen, Erinnerungsstatus, Abrufzähler

class TestV053(B5):
    def put(self, d):
        return self.c.put("/api/v1/einstellungen", json=d)

    def stelle(self):
        return db.stelle_einstellung_lesen("1001")

    def test_teilweise_aenderung_laesst_den_rest_unveraendert(self):
        vorher = self.stelle()
        r = self.put({"aufbewahrung_tage": 30})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        nachher = self.stelle()
        self.assertEqual((nachher["verantwortlicher"], nachher["datenschutz_link"], nachher["aufbewahrung_tage"]), (vorher["verantwortlicher"], vorher["datenschutz_link"], 30))
        self.assertEqual(self.put({"mitglied_download_link": False}).status_code, 200)
        n2 = self.stelle()
        self.assertEqual((n2["aufbewahrung_tage"], n2["mitglied_download_link"], n2["verantwortlicher"]), (30, 0, vorher["verantwortlicher"]))
        self.assertEqual(self.put({"verantwortlicher": "Neue Beratungsstelle, Weg 5, 12345 Ort"}).status_code, 200)
        n3 = self.stelle()
        self.assertEqual((n3["verantwortlicher"], n3["aufbewahrung_tage"], n3["mitglied_download_link"], n3["datenschutz_link"]),
                         ("Neue Beratungsstelle, Weg 5, 12345 Ort", 30, 0, vorher["datenschutz_link"]))

    def test_teilweise_aenderung_wird_weiter_geprueft(self):
        for body in ({}, {"x": 1}, {"aufbewahrung_tage": 29}, {"aufbewahrung_tage": True}, {"mitglied_download_link": "ja"}, {"verantwortlicher": "x"}, {"verantwortlicher": 5},
                     {"datenschutz_link": "http://unsicher.example"}, {"datenschutz_link": ""}, None, []):
            self.assertEqual(self.put(body).status_code, 422, body)
        self.assertEqual(self.stelle()["aufbewahrung_tage"], None)

    def test_ohne_bestehende_angaben_sind_beide_pflicht(self):
        from tests.test_baustein1 import me
        self.als("tb", me(email="b@test.example", ref="2002"))
        for body in ({"aufbewahrung_tage": 30}, {"verantwortlicher": "Beratungsstelle Zwei, Weg 2"}, {"datenschutz_link": "https://zwei.example/ds"}, {"mitglied_download_link": True}):
            self.assertEqual(self.put(body).status_code, 422, body)
        self.assertIsNone(db.stelle_einstellung_lesen("2002"))
        self.assertEqual(self.put({"verantwortlicher": "Beratungsstelle Zwei, Weg 2", "datenschutz_link": "https://zwei.example/ds", "aufbewahrung_tage": 60}).status_code, 200)
        self.assertEqual(db.stelle_einstellung_lesen("2002")["aufbewahrung_tage"], 60)
        self.assertEqual(self.stelle()["aufbewahrung_tage"], None)                      # Mandant 1001 unberührt

    def test_teilweise_aenderung_trifft_nur_den_eigenen_mandanten(self):
        from tests.test_baustein1 import me
        self.einstellungen_setzen("2002")
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.put({"aufbewahrung_tage": 365}).status_code, 200)
        self.assertEqual(db.stelle_einstellung_lesen("2002")["aufbewahrung_tage"], 365)
        self.assertIsNone(self.stelle()["aufbewahrung_tage"])

    def test_pruefung_meldet_erinnerungsstatus(self):
        self.starten()
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/pruefung" % self.vid).get_json()["erinnerungen_aus"], False)
        self.c.put("/api/v1/vorgaenge/%d/erinnerungen" % self.vid, json={"an": False})
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/pruefung" % self.vid).get_json()["erinnerungen_aus"], True)
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_json()["erinnerungen_aus"], 1)

    def test_abrufzaehler_in_den_kopfzeilen(self):
        self.unterschreiben()
        jobs.lauf(jetzt())
        token = re.search(r"/d#([A-Za-z0-9_-]+)", self.mails(pdfs.MAIL)[-1][1]).group(1)
        for i in (1, 2, 3):
            r = self.hole_download(token)
            self.assertEqual((r.status_code, r.headers["X-Abruf"], r.headers["X-Abruf-Max"]), (200, str(i), "3"))
        r = self.hole_download(token)
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("X-Abruf", r.headers)


# ======================================================================================================== Texte von Irony (Hash des Quelltextes)

IRONY_HASHES = {
    "datenschutz_mit_link": "848a52230ba979d5391948fd9e5a66629afdfbbbff50cf783ec95fc03df89f50",
    "datenschutz_ohne_link": "c4197732bbae893f79d86154cd30a1e6b832d2c3722a4af8ad074a7b811415b2",
    "unterschriftssatz": "94acb868108539405aac85f72d8b5a57693d11b7945e0dc4f75973e1f44561d0",
    "haken": "2642ea4cf9313a1bbbbc0544c35ab8f62c8e4b1c5a195a675b6eb264f9bb2a8d",
    "ausstieg": "7d18743423df39a9b26239a1835f589153de8054b3bb04198fbe38122f2d1324",
}


class TestIronyTexte(B5):
    def roh(self, name):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "texte", name + ".txt"), "rb") as f:
            return f.read()

    def test_hash_des_quelltextes_ist_der_freigegebene_wert(self):
        import hashlib
        for name, soll in IRONY_HASHES.items():
            daten = self.roh(name)
            self.assertEqual(hashlib.sha256(daten).hexdigest(), soll, name)             # Datei = Template: UTF-8, ein Absatz, kein Umbruch am Ende
            self.assertFalse(daten.endswith(b"\n"), name)
            self.assertNotIn(b"\n", daten, name)
            self.assertFalse(daten.startswith((b'"', "„".encode())) or daten.endswith(("“".encode(), b'"')), name)

    def test_sicherungssatz_option_b(self):
        for name in ("datenschutz_mit_link", "datenschutz_ohne_link"):
            text = self.roh(name).decode("utf-8")
            self.assertIn("In Sicherungen können gelöschte Dokumente noch bis zu 14 Tage, andere gelöschte Daten bis zu 3 Monate vorhanden sein.", text)
            self.assertNotIn("Datensicherungen", text)

    def test_variablen_sind_benannt(self):
        erlaubt = {"verantwortlicher", "ip_behandlung", "aufbewahrung_tage", "datenschutz_link"}
        for name in IRONY_HASHES:
            gefunden = set(re.findall(r"\{([a-z_]+)\}", self.roh(name).decode("utf-8")))
            self.assertLessEqual(gefunden, erlaubt, name)
        self.assertIn("datenschutz_link", re.findall(r"\{([a-z_]+)\}", self.roh("datenschutz_mit_link").decode()))
        self.assertNotIn("{datenschutz_link}", self.roh("datenschutz_ohne_link").decode())

    def test_ohne_link_gilt_die_fassung_ohne_link(self):
        import texte
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET datenschutz_link='   '")
        t = texte.laden("1001")["texte"]["datenschutz"]
        self.assertTrue(t.endswith("Weitere Informationen erhalten Sie von Ihrer Beratungsstelle."))
        self.assertNotIn("http", t)
        self.assertNotIn("{", t)
        with db.verbindung() as c:
            c.execute("UPDATE stelle_einstellung SET datenschutz_link='https://bst.example/datenschutz'")
        self.assertTrue(texte.laden("1001")["texte"]["datenschutz"].endswith("Weitere Informationen: https://bst.example/datenschutz."))

    def test_ip_behandlung_wortlaut(self):
        import texte
        for modus, soll in (("gekuerzt", "IP-Adresse (gekürzt gespeichert)"), ("voll", "IP-Adresse (ungekürzt gespeichert)")):
            with mock.patch.dict(os.environ, {"SIGNNEXT_IP_SPEICHERUNG": modus}):
                self.assertIn(soll, texte.laden("1001")["texte"]["datenschutz"], modus)

    def test_kennung_und_freigabe(self):
        import texte
        self.assertTrue(texte.freigegeben())                                          # catrin 18.11.2026: Mitglieder-Texte freigegeben (texte/FREIGABE = erteilt)
        self.assertEqual(texte.kennung(), "SignNext-Datenschutzhinweis-Unterschriftsseite-1.0; SignNext-Unterschriftssatz-1.0; SignNext-Zustimmungshaken-1.0; SignNext-Ausstieg-1.0")
        with mock.patch.object(texte, "ORDNER", self.freigabe_ordner("offen")):                  # der Mechanismus für offene Texte bleibt erhalten
            self.assertFalse(texte.freigegeben())
            self.assertTrue(texte.kennung().endswith("SignNext-Ausstieg-1.0 (Freigabe offen)"))
        with mock.patch.object(texte, "ORDNER", self.freigabe_ordner("erteilt")):
            self.assertTrue(texte.freigegeben())
            self.assertNotIn("Freigabe offen", texte.kennung())
        with mock.patch.object(texte, "ORDNER", self.freigabe_ordner("erteilt ")):
            self.assertTrue(texte.freigegeben())
        for wert in ("", "offen", "ja", "ERTEILT", "entwurf"):
            with mock.patch.object(texte, "ORDNER", self.freigabe_ordner(wert)):
                self.assertFalse(texte.freigegeben(), wert)

    def freigabe_ordner(self, wert):
        import pathlib
        import shutil
        import tempfile
        ziel = pathlib.Path(tempfile.mkdtemp(prefix="texte_freigabe_"))
        quelle = pathlib.Path(texte_ordner())
        for p in quelle.iterdir():
            shutil.copy(p, ziel / p.name)
        (ziel / "FREIGABE").write_text(wert, encoding="utf-8")
        self.addCleanup(shutil.rmtree, ziel, True)
        return ziel

    def test_nachweis_und_antwort_tragen_die_kennung(self):
        self.starten()
        self.einloggen()
        self.lesen_n(SEITEN)
        j = self.texte_holen()
        self.assertTrue(j.get("freigegeben", False))
        self.sign(1, j["hash"])
        with db.verbindung() as c:
            kennung = c.execute("SELECT text_kennung FROM unterschrift").fetchone()[0]
        self.assertNotIn("Freigabe offen", kennung)
        self.assertTrue(kennung.endswith("SignNext-Ausstieg-1.0"))

    def test_offene_freigabe_zeigt_den_vorbehalt_im_nachweis(self):
        import texte
        with mock.patch.object(texte, "ORDNER", self.freigabe_ordner("offen")):
            self.starten()
            self.einloggen()
            self.lesen_n(SEITEN)
            h = self.texte_holen()["hash"]
            self.sign(1, h)
            self.sign(2, self.texte_holen()["hash"])
        nachweis = db.dokument_bytes("1001", self.vid, "N")
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(nachweis)).pages)
        self.assertIn("noch nicht freigegeben (Freigabe offen)", text)


def texte_ordner():
    import texte
    return texte.ORDNER


# ======================================================================================================== Zeilenende in Prüfmustern

class TestZeilenende(B5):
    """Python-'$' passt auch vor einem abschließenden Zeilenumbruch. Alle Prüfmuster für Referenz, Mailadresse und Hostnamen müssen das echte Ende verlangen."""

    def test_referenz_mit_zeilenumbruch_ist_ungueltig(self):
        import schluessel
        for ref in ("1001\n", "1001\r\n", "\n1001", "10 01", "1001 ", ""):
            with self.assertRaises(ValueError, msg=repr(ref)):
                db.dokumente_verzeichnis(ref)
            with self.assertRaises(Exception, msg=repr(ref)):
                schluessel.verschluesseln(ref, "x", "y")
        self.assertEqual(db.dokumente_verzeichnis("1001").name, "1001")

    def test_portal_referenz_mit_zeilenumbruch_wird_abgewiesen(self):
        from tests.test_baustein1 import me
        for ref in ("10\n01", "1001\nX", "10\r01"):                                   # Leerraum außen schneidet das Portal ab, innen ist er ungültig
            self.als("tx", me(email="x@test.example", ref=ref))
            r = self.c.get("/api/v1/me")
            self.assertEqual((r.status_code, r.get_json()["grund"]), (403, "referenz_ungueltig"), repr(ref))

    def test_mailadresse_und_hostname_mit_zeilenumbruch(self):
        import mailer
        for adresse in ("a@example.org\n", "a@example.org\r\n", "a@example.org\nBcc: x@y.de"):
            self.assertFalse(mailer.adresse_gueltig(adresse), repr(adresse))
        self.assertTrue(mailer.adresse_gueltig("a@example.org"))
        self.assertFalse(mailer._HOSTNAME.match("mail.example.org\n"))
        self.assertTrue(mailer._HOSTNAME.match("mail.example.org"))

    def test_mail_aus_dem_pdf_mit_zeilenumbruch(self):
        import pdf_analyse
        self.assertIsNone(pdf_analyse.MAIL.match("a@example.org\n"))
        self.assertTrue(pdf_analyse.MAIL.match("a@example.org"))


# ======================================================================================================== Thread, Betrieb

class TestBetrieb(unittest.TestCase):
    def test_jobs_koennen_abgeschaltet_werden(self):
        with mock.patch.dict(os.environ, {"SIGNNEXT_JOBS": "0"}):
            jobs._gestartet = False
            self.assertFalse(jobs.starten())
            self.assertFalse(jobs._gestartet)

    def test_nur_ein_prozess_haelt_die_sperre(self):
        import fcntl
        import tempfile
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {"SIGNNEXT_DATEN": t}):
            fremd = open(os.path.join(t, "jobs.lock"), "a")
            fcntl.flock(fremd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            alt = jobs._lock_datei
            jobs._lock_datei = None
            try:
                self.assertFalse(jobs._sperre_halten())
                fcntl.flock(fremd, fcntl.LOCK_UN)
                self.assertTrue(jobs._sperre_halten())
                self.assertTrue(jobs._sperre_halten())
            finally:
                if jobs._lock_datei is not None:
                    jobs._lock_datei.close()
                jobs._lock_datei = alt
                fremd.close()

    def test_ein_fehler_stoppt_die_anderen_schritte_nicht(self):
        with mock.patch.object(jobs, "recovery", side_effect=RuntimeError("x")), mock.patch.object(jobs, "aufraeumen", return_value=7):
            z = jobs.lauf(datetime.now(timezone.utc))
        self.assertEqual((z["recovery"], z["aufraeumen"]), (-1, 7))

    def test_version_und_abhaengigkeiten(self):
        wurzel = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(open(os.path.join(wurzel, "VERSION")).read().strip(), "0.6.1")
        req = open(os.path.join(wurzel, "requirements.txt")).read()
        self.assertRegex(req, r"(?m)^reportlab==\d+\.\d+\.\d+$")
        self.assertNotRegex(req.lower(), r"pymupdf|fitz")
        for zeile in req.split():
            self.assertIn("==", zeile)

    def test_kein_pymupdf_im_code(self):
        import ast
        wurzel = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("abschluss.py", "jobs.py", "download.py", "loeschen.py", "mailversand.py", "mailtexte.py"):
            quelle = open(os.path.join(wurzel, name), encoding="utf-8").read()
            for knoten in ast.walk(ast.parse(quelle)):
                if isinstance(knoten, (ast.Import, ast.ImportFrom)):
                    module = [a.name for a in knoten.names] + ([knoten.module] if isinstance(knoten, ast.ImportFrom) and knoten.module else [])
                    for m in module:
                        self.assertNotIn(m.split(".")[0].lower(), ("fitz", "pymupdf"), name)
            self.assertNotIn("rechtssicher", quelle.lower())


if __name__ == "__main__":
    unittest.main()
