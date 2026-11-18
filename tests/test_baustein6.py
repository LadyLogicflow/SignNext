"""Baustein 6: Zählung, Tarife, Sperre, Betreiberkommandos, Zähleinträge (Löschung nach Frist). Nur erfundene Daten."""
import io
import json
import os
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import berlinzeit
import config
import db
import jobs
import loeschen
import mailer
import manage
import tarif
from tests import pdfs
from tests.test_baustein5 import B5, jetzt


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Z(B5):
    """Hilfen: gezählte Vorgänge ohne Inhalt anlegen, Zählstand lesen, Kommandos ausführen."""

    def jetzt_monat(self):
        return berlinzeit.monat(db.jetzt())

    def seed(self, n, mandant="1001", monat=None, status="geloescht"):
        monat = monat or self.jetzt_monat()
        with db.verbindung() as c:
            for _ in range(n):
                c.execute("INSERT INTO vorgang(mandant_ref, status, erstellt, versendet_am, gezaehlt_am, zaehlmonat) VALUES (?,?,?,?,?,?)",
                          (mandant, status, db.jetzt(), db.jetzt(), db.jetzt(), monat))

    def info(self, mandant="1001"):
        return tarif.info(mandant)

    def vz(self, vid):
        v = db.vorgang_intern("1001", vid)
        return v["status"], v["gezaehlt_am"], v["zaehlmonat"]

    def manage(self, *args):
        puffer = io.StringIO()
        with redirect_stdout(puffer), mock.patch("db.init"):
            code = manage.main(["manage.py", *args])
        return code, puffer.getvalue()

    def versenden(self, vid):
        return self.senden_ok(vid)


# ======================================================================================================== Zählen

class TestZaehlen(Z):
    def test_erstversand_zaehlt_einmal_im_monat_der_deutschen_zeit(self):
        self.starten()
        status, am, monat = self.vz(self.vid)
        self.assertEqual(status, "versendet")
        self.assertTrue(am)
        self.assertEqual(monat, berlinzeit.monat(am))
        self.assertEqual(self.info()["genutzt"], 1)

    def test_nichts_ausser_dem_erstversand_zaehlt(self):
        self.unterschreiben()                                                        # Link, Lesen, Unterschrift, Abschluss
        self.assertEqual(self.info()["genutzt"], 1)
        jobs.lauf(jetzt(hours=100))                                                  # Erinnerungen, Bestätigung, Fristen
        self.assertEqual(self.info()["genutzt"], 1)
        vorher = self.vz(self.vid)
        self.assertEqual(self.vz(self.vid), vorher)

    def test_neuer_link_zaehlt_nicht(self):
        self.starten()
        vorher = self.vz(self.vid)
        for _ in range(2):
            r = self.c.post("/api/v1/vorgaenge/%d/neuer-link" % self.vid, json={"bestaetigt": True})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.vz(self.vid), vorher)
        self.assertEqual(self.info()["genutzt"], 1)

    def test_hochladen_und_pruefen_zaehlen_nicht(self):
        vid = self.bereit()
        self.c.get("/api/v1/vorgaenge/%d/pruefung" % vid)
        self.assertEqual(self.info()["genutzt"], 0)
        self.assertEqual(self.vz(vid)[1:], (None, None))

    def test_eindeutig_gescheiterter_erstversand_gibt_frei(self):
        vid = self.bereit()
        with mock.patch("mailer.senden", side_effect=mailer.MailFehler("verbindung")):
            r = self.versenden(vid)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(self.vz(vid), ("bereit", None, None))
        self.assertEqual(self.info()["genutzt"], 0)
        self.assertEqual(self.versenden(vid).status_code, 200)                      # neuer Versuch zählt jetzt
        self.assertEqual(self.info()["genutzt"], 1)

    def test_unklarer_versand_zaehlt(self):
        vid = self.bereit()
        with mock.patch("mailer.senden", side_effect=mailer.MailFehler("unklar", definitiv=False)):
            r = self.versenden(vid)
        self.assertEqual(r.status_code, 502)
        status, am, monat = self.vz(vid)
        self.assertEqual(status, "versand_unklar")
        self.assertTrue(am and monat)
        self.assertEqual(self.info()["genutzt"], 1)

    def test_gescheiterter_neuer_link_nimmt_die_zaehlung_nicht_zurueck(self):
        self.starten()
        vorher = self.vz(self.vid)
        with mock.patch("mailer.senden", side_effect=mailer.MailFehler("verbindung")):
            r = self.c.post("/api/v1/vorgaenge/%d/neuer-link" % self.vid, json={"bestaetigt": True})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(self.vz(self.vid), (vorher[0], vorher[1], vorher[2]))
        self.assertEqual(self.info()["genutzt"], 1)

    def test_fehler_vor_der_zustellung_im_zugang_gibt_frei(self):
        vid = self.bereit()
        import schluessel
        with mock.patch("mail_zugang.fuer_dienst", side_effect=schluessel.Entschluesselung("nicht_lesbar")):
            r = self.versenden(vid)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.vz(vid), ("bereit", None, None))

    def test_loeschung_senkt_den_zaehler_nicht(self):
        self.starten()
        loeschen.vorgang_loeschen("1001", self.vid, "storno")
        self.assertEqual(self.info()["genutzt"], 1)
        status, am, monat = self.vz(self.vid)
        self.assertEqual(status, "geloescht")
        self.assertTrue(am and monat)

    def test_mandantentrennung(self):
        self.seed(3, mandant="2002")
        self.starten()
        self.assertEqual((self.info("1001")["genutzt"], self.info("2002")["genutzt"]), (1, 3))

    def test_monatswechsel_deutsche_zeit(self):
        self.assertEqual(berlinzeit.monat("2026-12-31T23:30:00Z"), "2027-01")          # Winter: +1 h
        self.assertEqual(berlinzeit.monat("2026-12-31T22:59:59Z"), "2026-12")
        self.assertEqual(berlinzeit.monat("2026-11-30T23:00:00Z"), "2026-12")
        self.assertEqual(berlinzeit.monat("2026-03-31T22:00:00Z"), "2026-04")          # Sommer: +2 h
        self.assertEqual(berlinzeit.monat("2026-03-31T21:59:59Z"), "2026-03")
        self.assertEqual(berlinzeit.monat("2026-10-31T22:59:59Z"), "2026-10")          # Winterzeit seit 25.10.: +1 h
        self.assertEqual(berlinzeit.monat("2026-10-31T23:00:00Z"), "2026-11")
        self.assertEqual(berlinzeit.monat("2026-06-30T22:00:00Z"), "2026-07")
        self.assertEqual(berlinzeit.berlin("2026-11-13T14:05:00Z"), "13.11.2026 15:05:00 MEZ")

    def test_anderer_monat_zaehlt_nicht(self):
        self.seed(15, monat="2020-01")
        self.assertEqual(self.info()["genutzt"], 0)
        tarif.preis_setzen("basis", 900)
        self.starten()                                                               # kein Kontingent verbraucht: frei
        self.assertEqual(self.info()["genutzt"], 1)

    def test_info_mit_vorgestellter_uhr(self):
        self.seed(2, monat="2027-01")
        self.assertEqual(tarif.info("1001", utc(2026, 12, 31, 23, 30))["genutzt"], 2)       # in Berlin schon Januar
        self.assertEqual(tarif.info("1001", utc(2026, 12, 31, 22, 30))["genutzt"], 0)
        self.assertEqual(tarif.info("1001", utc(2026, 12, 31, 23, 30))["zeitraum"], "2027-01")


# ======================================================================================================== Tarife, Sperre

class TestSperre(Z):
    def test_grenze_bei_preis_null_nur_zaehlen(self):
        self.seed(14)
        self.starten()                                                               # 15.
        i = self.info()
        self.assertEqual((i["tarif"], i["limit"], i["genutzt"], i["noch_frei"], i["gesperrt"], i["sperre_aktiv"]), ("basis", 15, 15, 0, False, False))
        vid2 = self.bereit()
        self.assertEqual(self.versenden(vid2).status_code, 200)                      # 16. geht durch: Preis 0
        self.assertEqual(self.info()["genutzt"], 16)
        self.assertEqual(self.info()["noch_frei"], 0)
        self.assertFalse(self.info()["gesperrt"])

    def test_grenze_bei_preis_groesser_null_sperrt_ab_16(self):
        tarif.preis_setzen("basis", 900)
        self.seed(14)
        self.starten()                                                               # 15. geht
        vid2 = self.bereit()
        n = len(self.srv.nachrichten)
        r = self.versenden(vid2)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "kontingent_erreicht"))
        self.assertEqual(r.get_json()["meldung"], "Das Kontingent für diesen Monat ist erreicht. Bitte wenden Sie sich an den Betreiber.")
        self.assertEqual({k: r.get_json()[k] for k in ("tarif", "limit", "genutzt")}, {"tarif": "basis", "limit": 15, "genutzt": 15})
        self.assertEqual(self.vz(vid2), ("bereit", None, None))                      # nichts verändert, nicht gezählt
        self.assertEqual(len(self.srv.nachrichten), n)                               # keine Mail
        self.assertEqual(self.info()["genutzt"], 15)
        self.assertTrue(self.info()["gesperrt"])
        self.assertTrue(self.info()["sperre_aktiv"])
        self.assertEqual(self.audit_zeilen("versand_abgebrochen")[-1]["details"], "id=%d grund=kontingent_erreicht" % vid2)

    def test_upgrade_hinweis_optional(self):
        tarif.preis_setzen("basis", 900)
        self.seed(15)
        vid = self.bereit()
        with mock.patch.dict(os.environ, {"SIGNNEXT_UPGRADE_HINWEIS": "Kontakt: support@betreiber.example\nBcc: x"}):
            r = self.versenden(vid)
        self.assertTrue(r.get_json()["meldung"].endswith("Kontakt: support@betreiber.example Bcc: x"))
        self.assertNotIn("\n", r.get_json()["meldung"])
        self.assertNotIn("Upgrade", self.versenden(vid).get_json()["meldung"])

    def test_sperrmodus(self):
        self.seed(15)
        vid = self.bereit()
        with mock.patch.dict(os.environ, {"SIGNNEXT_SPERRE": "an"}):                 # an: Sperre am Limit auch bei Preis 0
            self.assertTrue(self.info()["gesperrt"])
            self.assertEqual(self.versenden(vid).status_code, 409)
        tarif.preis_setzen("basis", 900)
        with mock.patch.dict(os.environ, {"SIGNNEXT_SPERRE": "aus"}):                # aus: nie, auch bei Preis > 0
            self.assertFalse(self.info()["gesperrt"])
            self.assertEqual(self.versenden(vid).status_code, 200)
        for wert, soll in (("auto", "auto"), ("", "auto"), ("AN", "auto"), ("ja", "auto"), ("aus", "aus"), ("an", "an")):
            with mock.patch.dict(os.environ, {"SIGNNEXT_SPERRE": wert}):
                self.assertEqual(config.sperre_modus(), soll, wert)

    def test_bereits_gezaehlte_vorgaenge_laufen_bei_sperre_weiter(self):
        self.starten()                                                               # 1.
        tarif.preis_setzen("basis", 900)
        tarif.limit_setzen("basis", 1)
        self.assertTrue(self.info()["gesperrt"])
        r = self.c.post("/api/v1/vorgaenge/%d/neuer-link" % self.vid, json={"bestaetigt": True})
        self.assertEqual(r.status_code, 200)                                         # neuer Link: nicht gesperrt
        self.einloggen_neu()
        self.lesen()
        self.assertEqual(self.alles_unterschreiben().get_json(), {"fertig": True})
        self.assertEqual(db.vorgang_intern("1001", self.vid)["status"], "abgeschlossen")
        jobs.lauf(jetzt())
        self.assertEqual(self.info()["genutzt"], 1)

    def einloggen_neu(self):
        import re
        self.token = re.search(r"/u#([A-Za-z0-9_-]+)", self.mail_text(self.srv.nachrichten[-1])[1]).group(1)
        self.einloggen()

    def test_tarifwechsel_wirkt_sofort(self):
        tarif.preis_setzen("basis", 900)
        self.seed(15)
        vid = self.bereit()
        self.assertEqual(self.versenden(vid).status_code, 409)
        tarif.zuordnen("1001", "plus")
        self.assertEqual(self.info()["tarif"], "plus")
        self.assertEqual(self.versenden(vid).status_code, 200)

    def test_plus_ist_unbegrenzt_und_preis_aendert_nichts(self):
        tarif.zuordnen("1001", "plus")
        tarif.preis_setzen("plus", 2000)
        self.seed(500)
        i = self.info()
        self.assertEqual((i["limit"], i["noch_frei"], i["gesperrt"], i["sperre_aktiv"]), (None, None, False, False))
        self.assertEqual(self.versenden(self.bereit()).status_code, 200)
        tarif.limit_setzen("plus", 3)                                                # mit Limit und Preis > 0 sperrt auch plus
        self.assertTrue(self.info()["gesperrt"])

    def test_pilot_ist_von_anfang_an_plus(self):
        self.assertEqual(config.tarif_plus_referenzen(), frozenset({"282874"}))
        i = tarif.info("282874")
        self.assertEqual((i["tarif"], i["limit"], i["gesperrt"]), ("plus", None, False))
        self.assertEqual(tarif.info("1001")["tarif"], "basis")
        tarif.zuordnen("282874", "basis")                                            # ausdrückliche Zuordnung geht vor
        self.assertEqual(tarif.info("282874")["tarif"], "basis")

    def test_plus_referenzen_konfiguration(self):
        for wert, soll in (("", frozenset()), (" 1001 , 2002 ", frozenset({"1001", "2002"})), ("a b,c/d,ok_1", frozenset({"ok_1"})), ("x" * 65, frozenset()), (",,", frozenset())):
            with mock.patch.dict(os.environ, {"SIGNNEXT_TARIF_PLUS_REFERENZEN": wert}):
                self.assertEqual(config.tarif_plus_referenzen(), soll, repr(wert))
        with mock.patch.dict(os.environ, {"SIGNNEXT_TARIF_PLUS_REFERENZEN": "1001"}):
            self.assertEqual(tarif.info("1001")["tarif"], "plus")

    def test_gleichzeitige_versendungen_am_limit(self):
        tarif.preis_setzen("basis", 900)
        self.seed(14)
        vorgaenge = [self.bereit() for _ in range(6)]
        ergebnisse = []
        sperre = threading.Barrier(len(vorgaenge))
        echt = tarif.genutzt

        def langsam(c, mandant_ref, monat):                                          # zwischen Prüfen und Eintragen liegt ein Zeitfenster: nur eine Transaktion hält es dicht
            n = echt(c, mandant_ref, monat)
            time.sleep(0.05)
            return n

        def lauf(vid):
            sperre.wait()
            ergebnisse.append(tarif.beanspruchen("1001", vid, "bereit"))
        with mock.patch("tarif.genutzt", side_effect=langsam):
            ts = [threading.Thread(target=lauf, args=(v,)) for v in vorgaenge]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertEqual(sorted(ergebnisse), ["kontingent"] * 5 + ["ok"])
        self.assertEqual(self.info()["genutzt"], 15)

    def test_beanspruchen_nur_im_richtigen_zustand_und_nur_einmal(self):
        vid = self.bereit()
        ergebnisse = []
        sperre = threading.Barrier(4)

        def lauf():
            sperre.wait()
            ergebnisse.append(tarif.beanspruchen("1001", vid, "bereit"))
        ts = [threading.Thread(target=lauf) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(ergebnisse), ["ok", "zustand", "zustand", "zustand"])
        self.assertEqual(self.info()["genutzt"], 1)
        self.assertEqual(tarif.beanspruchen("1001", 99999, "bereit"), "zustand")
        self.assertEqual(tarif.beanspruchen("2002", vid, "versenden"), "zustand")           # fremder Mandant

    def test_tarif_info_ohne_preis_und_nur_lesend(self):
        r = self.c.get("/api/v1/tarif")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(set(j), {"tarif", "limit", "genutzt", "noch_frei", "zeitraum", "gesperrt", "sperre_aktiv"})
        tarif.preis_setzen("basis", 900)
        self.assertNotIn("900", json.dumps(self.c.get("/api/v1/tarif").get_json()))
        self.assertNotIn("preis", json.dumps(self.c.get("/api/v1/tarif").get_json()).lower())
        for methode in ("put", "post", "delete", "patch"):
            self.assertEqual(getattr(self.c, methode)("/api/v1/tarif", json={"tarif": "plus", "preis_cent": 0}).status_code, 405, methode)
        self.assertEqual(tarif.info("1001")["tarif"], "basis")
        self.assertEqual(self.app.test_client().get("/api/v1/tarif").status_code, 401)

    def test_tarif_info_je_mandant(self):
        from tests.test_baustein1 import me
        self.seed(7, mandant="2002")
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.c.get("/api/v1/tarif").get_json()["genutzt"], 7)
        self.als("ta", me(ref="1001"))
        self.assertEqual(self.c.get("/api/v1/tarif").get_json()["genutzt"], 0)


# ======================================================================================================== Betreiber (manage.py)

class TestBetreiber(Z):
    def audit_manage(self):
        with db.verbindung() as c:
            return [dict(z) for z in c.execute("SELECT * FROM audit_log WHERE nutzer='manage' ORDER BY id")]

    def test_liste(self):
        code, aus = self.manage("tarif-liste")
        self.assertEqual(code, 0, aus)
        self.assertIn("Tarif basis  Limit je Monat: 15         Preis: 0 Cent", aus)
        self.assertIn("Tarif plus   Limit je Monat: unbegrenzt Preis: 0 Cent", aus)
        self.assertIn("Standard Tarif plus für: 282874", aus)
        self.assertIn("Sperrmodus (SIGNNEXT_SPERRE): auto", aus)

    def test_preis_limit_setzen_im_audit(self):
        self.assertEqual(self.manage("tarif-preis", "basis", "900")[0], 0)
        self.assertEqual(self.manage("tarif-limit", "basis", "20")[0], 0)
        self.assertEqual(self.manage("tarif-limit", "plus", "unbegrenzt")[0], 0)
        self.assertEqual(self.manage("tarif-setzen", "1001", "plus")[0], 0)
        with db.verbindung() as c:
            self.assertEqual([tuple(z) for z in c.execute("SELECT name, limit_monat, preis_cent FROM tarif ORDER BY name")], [("basis", 20, 900), ("plus", None, 0)])
        self.assertEqual([(z["aktion"], z["details"]) for z in self.audit_manage()],
                         [("tarif_preis", "tarif=basis preis_cent=900"), ("tarif_limit", "tarif=basis limit=20"), ("tarif_limit", "tarif=plus limit=unbegrenzt"), ("tarif_zugeordnet", "tarif=plus")])
        self.assertEqual(self.audit_manage()[-1]["mandant_ref"], "1001")

    def test_ungueltige_eingaben(self):
        faelle = (("tarif-preis", "gold", "1"), ("tarif-preis", "basis", "-1"), ("tarif-preis", "basis", "1000001"), ("tarif-preis", "basis", "9.5"), ("tarif-preis", "basis", "x"),
                  ("tarif-limit", "basis", "0"), ("tarif-limit", "basis", "100001"), ("tarif-limit", "basis", "abc"), ("tarif-limit", "gold", "5"),
                  ("tarif-setzen", "../x", "plus"), ("tarif-setzen", "1001", "gold"), ("tarif-setzen", "a b", "basis"), ("tarif-setzen", "1001\n", "plus"), ("zaehlung", "2026-13"), ("zaehlung", "2026-00"), ("zaehlung", "2026-1x"))
        for args in faelle:
            code, aus = self.manage(*args)
            self.assertEqual(code, 1, args)
            self.assertIn("Eingabe ungültig", aus)
        self.assertEqual(self.audit_manage(), [])
        with db.verbindung() as c:
            self.assertEqual([tuple(z) for z in c.execute("SELECT name, limit_monat, preis_cent FROM tarif ORDER BY name")], [("basis", 15, 0), ("plus", None, 0)])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM stelle_tarif").fetchone()[0], 0)

    def test_falscher_aufruf(self):
        for args in (("tarif-preis",), ("tarif-preis", "basis"), ("tarif-limit", "basis"), ("tarif-setzen", "1001"), ("tarif-liste", "x")):
            self.assertEqual(self.manage(*args)[0], 2, args)

    def test_zaehlung_nur_zahlen(self):
        self.seed(3, monat="2026-10")
        self.seed(2, monat="2026-11")
        self.seed(4, mandant="282874", monat="2026-11")
        code, aus = self.manage("zaehlung")
        self.assertEqual(code, 0)
        self.assertIn("2026-10  1001            3 Vorgänge  Tarif basis", aus)
        self.assertIn("2026-11  1001            2 Vorgänge  Tarif basis", aus)
        self.assertIn("2026-11  282874          4 Vorgänge  Tarif plus", aus)
        code, aus = self.manage("zaehlung", "1001")
        self.assertNotIn("282874", aus)
        code, aus = self.manage("zaehlung", "2026-11")
        self.assertNotIn("2026-10", aus)
        self.assertIn("282874", aus)
        code, aus = self.manage("zaehlung", "1001", "2026-10")
        self.assertEqual(aus.strip(), "2026-10  1001            3 Vorgänge  Tarif basis")
        self.assertIn("Keine Zählung", self.manage("zaehlung", "9999")[1])
        for verboten in pdfs.PERSONENDATEN:
            self.assertNotIn(verboten, aus)

    def test_tarif_aenderung_nicht_ueber_die_beratungsstellen_schnittstelle(self):
        for pfad in ("/api/v1/tarif", "/api/v1/tarife", "/api/v1/einstellungen"):
            r = self.c.put(pfad, json={"tarif": "plus", "preis_cent": 0, "limit": None, "aufbewahrung_tage": 30, "verantwortlicher": "Zwei Zeilen, Weg 1", "datenschutz_link": "https://a.example/ds"})
            self.assertIn(r.status_code, (200, 404, 405, 422), pfad)
        r = self.c.put("/api/v1/einstellungen", json={"tarif": "plus"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(tarif.info("1001")["tarif"], "basis")


# ======================================================================================================== Zähleinträge: Vertragsende, Löschfrist, Migration

class TestZaehleintraege(Z):
    def test_zaehleintrag_ohne_personendaten(self):
        self.unterschreiben()
        loeschen.vorgang_loeschen("1001", self.vid, "storno")
        with db.verbindung() as c:
            z = dict(c.execute("SELECT * FROM vorgang WHERE id=?", (self.vid,)).fetchone())
        erlaubt = {"id", "mandant_ref", "status", "erstellt", "versendet_am", "gezaehlt_am", "zaehlmonat", "geloescht_am", "geloescht_grund", "personen", "unterschrieben_am",
                   "abgeschlossen_am", "aufbewahrung_tage", "aufbewahrung_bis", "erinnerungen_aus", "abschluss_versuche", "mitglied_mail_versuche", "versenden_seit",
                   "abschluss_seit", "link_sperre_gemeldet", "bst_info_am", "mitglied_mail_am", "vorwarnung_am"}
        gefuellt = {k for k, v in z.items() if v not in (None, "")}
        self.assertLessEqual(gefuellt, erlaubt, gefuellt - erlaubt)
        for feld in ("email_enc", "code_hmac", "code_kid", "abgleich", "bestaetigt_von", "email_herkunft", "datum_herkunft", "personen_herkunft", "versand_fehler"):
            self.assertIsNone(z[feld], feld)
        roh = json.dumps(z, default=str)
        for verboten in pdfs.PERSONENDATEN:
            self.assertNotIn(verboten, roh)

    def test_vertragsende_laesst_die_zaehleintraege_stehen(self):
        self.starten()                                                               # gezählt
        vid_offen = self.bereit()                                                    # nie versendet
        zahlen = db.mandant_loeschen("1001")
        self.assertEqual((zahlen["vorgang"], zahlen["zaehleintraege"]), (1, 1))
        with db.verbindung() as c:
            zeilen = [dict(z) for z in c.execute("SELECT id, mandant_ref, status, zaehlmonat, email_enc, code_hmac, geloescht_grund FROM vorgang")]
        self.assertEqual(len(zeilen), 1)
        z = zeilen[0]
        self.assertEqual((z["id"], z["mandant_ref"], z["status"], z["geloescht_grund"]), (self.vid, "1001", "geloescht", "vertragsende"))
        self.assertTrue(z["zaehlmonat"])
        self.assertEqual((z["email_enc"], z["code_hmac"]), (None, None))
        self.assertNotIn(vid_offen, [x["id"] for x in zeilen])
        self.assertEqual(db.dokumente_liste("1001", self.vid), [])
        self.assertEqual(db.vorgaenge_liste("1001")[0]["status"], "geloescht")
        p = [x for x in self.protokoll() if x["art"] == "vertragsende"][-1]
        self.assertEqual(json.loads(p["zahlen"])["zaehleintraege"], 1)

    def test_vertragsende_alles_andere_ist_weg(self):
        self.unterschreiben()
        db.mandant_loeschen("1001")
        with db.verbindung() as c:
            for t in db.MANDANTENTABELLEN:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM %s WHERE mandant_ref='1001'" % t).fetchone()[0], 0, t)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM unterschrift").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM link").fetchone()[0], 0)
        self.assertEqual(self.dateien(), [])

    def test_zaehleintraege_koennen_beim_vertragsende_auch_ganz_geloescht_werden(self):
        self.starten()
        with mock.patch.object(db, "ZAEHLEINTRAG_BLEIBT", False):
            zahlen = db.mandant_loeschen("1001")
        self.assertEqual((zahlen["vorgang"], zahlen["zaehleintraege"]), (1, 0))
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0], 0)

    def test_loeschfrist_drei_jahre_nach_jahresende_mit_vorgestellter_uhr(self):
        self.seed(2, monat="2022-06")                                                # Versandjahr 2022: Ende 31.12.2022 + 3 Jahre = 31.12.2025
        self.seed(1, monat="2022-12")
        self.seed(3, monat="2023-01")
        self.seed(1, monat="2022-07", status="versendet")                            # nicht gelöscht (Inhalt noch da): nie durch diese Frist
        n = jobs.zaehleintraege_loeschen(utc(2025, 12, 31, 22, 59, 59))                # Berlin: 31.12.2025 23:59:59
        self.assertEqual(n, 0)
        n = jobs.zaehleintraege_loeschen(utc(2025, 12, 31, 23, 0, 0))                  # Berlin: 01.01.2026 00:00
        self.assertEqual(n, 3)
        with db.verbindung() as c:
            rest = sorted((z["zaehlmonat"], z["status"]) for z in c.execute("SELECT zaehlmonat, status FROM vorgang"))
        self.assertEqual(rest, [("2022-07", "versendet"), ("2023-01", "geloescht"), ("2023-01", "geloescht"), ("2023-01", "geloescht")])
        p = [x for x in self.protokoll() if x["art"] == "zaehleintraege"]
        self.assertEqual([json.loads(x["zahlen"]) for x in p], [{"eintraege": 3, "jahr": 2022}])
        self.assertTrue(all(isinstance(v, int) for v in json.loads(p[0]["zahlen"]).values()))
        self.assertEqual(jobs.zaehleintraege_loeschen(utc(2025, 12, 31, 23, 30)), 0)    # wiederholbar, nichts doppelt
        self.assertEqual(len([x for x in self.protokoll() if x["art"] == "zaehleintraege"]), 1)
        self.assertEqual(jobs.zaehleintraege_loeschen(utc(2026, 1, 1, 0, 0)), 0)        # die 2023er folgen ein Jahr später, nicht früher
        self.assertEqual(jobs.zaehleintraege_loeschen(utc(2026, 12, 31, 22, 59)), 0)
        self.assertEqual(jobs.zaehleintraege_loeschen(utc(2026, 12, 31, 23, 0)), 3)

    def test_loeschfrist_unabhaengig_vom_vertragsende(self):
        self.seed(2, monat="2022-03")
        db.mandant_loeschen("1001")                                                  # die Zähleinträge bleiben, auch ohne Beratungsstelle
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM vorgang WHERE mandant_ref='1001'").fetchone()[0], 2)
        self.assertEqual(jobs.zaehleintraege_loeschen(utc(2026, 1, 1)), 2)

    def test_job_laeuft_im_normalen_durchlauf(self):
        self.seed(1, monat="2020-05")
        z = jobs.lauf(jetzt())
        self.assertEqual(z["zaehleintraege"], 1)

    def test_loeschprotokoll_ohne_personenbezug(self):
        self.seed(1, monat="2020-05")
        jobs.lauf(jetzt())
        p = [x for x in self.protokoll() if x["art"] == "zaehleintraege"][0]
        self.assertEqual(set(p), {"id", "zeit", "art", "mandant_ref", "vorgang_id", "zahlen", "loeschen_nach"})
        self.assertEqual((p["mandant_ref"], p["vorgang_id"]), ("-", None))

    def test_migration_alter_vorgaenge(self):
        with db.verbindung() as c:
            c.execute("INSERT INTO vorgang(mandant_ref, status, erstellt, versendet_am) VALUES ('1001','versendet',?,?)", ("2026-03-31T21:00:00Z", "2026-03-31T22:30:00Z"))
            c.execute("INSERT INTO vorgang(mandant_ref, status, erstellt) VALUES ('1001','bereit','2026-03-31T21:00:00Z')")
        db.init(os.path.join(self.tmp.name, "t.db"))
        with db.verbindung() as c:
            z = [tuple(r) for r in c.execute("SELECT status, gezaehlt_am, zaehlmonat FROM vorgang ORDER BY id")]
        self.assertEqual(z, [("versendet", "2026-03-31T22:30:00Z", "2026-04"), ("bereit", None, None)])
        db.init(os.path.join(self.tmp.name, "t.db"))                                  # idempotent
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tarif").fetchone()[0], 2)

    def test_tarife_sind_voreingestellt_und_werden_beim_start_nicht_ueberschrieben(self):
        tarif.preis_setzen("basis", 777)
        db.init(os.path.join(self.tmp.name, "t.db"))
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT preis_cent FROM tarif WHERE name='basis'").fetchone()[0], 777)

    def test_waechter_vorgang_ist_ausnahme_stelle_tarif_wird_geloescht(self):
        self.assertIn("vorgang", db.AUSNAHMEN)
        self.assertIn("stelle_tarif", db.MANDANTENTABELLEN)
        self.assertNotIn("vorgang", db.MANDANTENTABELLEN)
        tarif.zuordnen("1001", "plus")
        db.mandant_loeschen("1001")
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM stelle_tarif").fetchone()[0], 0)


class TestKonfiguration(unittest.TestCase):
    def test_upgrade_hinweis(self):
        with mock.patch.dict(os.environ, {"SIGNNEXT_UPGRADE_HINWEIS": "  a\n b\r\n c  "}):
            self.assertEqual(config.upgrade_hinweis(), "a b c")
        with mock.patch.dict(os.environ, {"SIGNNEXT_UPGRADE_HINWEIS": "x" * 500}):
            self.assertEqual(len(config.upgrade_hinweis()), 200)
        env = {k: v for k, v in os.environ.items() if k != "SIGNNEXT_UPGRADE_HINWEIS"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(config.upgrade_hinweis(), "")

    def test_vorgaben(self):
        self.assertEqual(config.TARIF_VORGABE, {"basis": (15, 0), "plus": (None, 0)})
        self.assertEqual(config.TARIF_NAMEN, ("basis", "plus"))

    def test_keine_abrechnung_im_code(self):
        wurzel = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        quelle = open(os.path.join(wurzel, "tarif.py"), encoding="utf-8").read().lower()
        for wort in ("rechnungsnummer", "rechnung_", "iban", "umsatzsteuer", "invoice"):
            self.assertNotIn(wort, quelle)


if __name__ == "__main__":
    unittest.main()
