"""v0.7.0 Arbeitsseite /vorgaenge: Liste mit Name, Personen und Link-Zustand, Statustexte, Löschen durch die Beratungsstelle, Vorgangsname (Datenschutz). Nur erfundene Daten."""
import json
from datetime import datetime, timedelta, timezone

import db
import pdf_eingang
from tests import pdfs
from tests.test_baustein1 import me
from tests.test_baustein2 import RouteBasis, hochladen

NAME_A = "Protokoll Mustermann Erika.PDF"
NAME_B = "Anschreiben Mustermann.pdf"


def _zeit(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestName(RouteBasis):
    def hoch(self, namen):
        r = hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf(), namen=namen)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()

    def test_name_kommt_aus_dem_protokoll_sonst_dem_anschreiben(self):
        j = self.hoch([NAME_B, NAME_A])                                                                   # B zuerst, A danach: der Typ kommt aus dem Inhalt
        self.assertEqual(j["name"], "Protokoll Mustermann Erika")
        eintrag = self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"][0]
        self.assertEqual(eintrag["name"], "Protokoll Mustermann Erika")
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % j["vorgang_id"]).get_json()["name"], "Protokoll Mustermann Erika")
        j2 = self.hoch([NAME_B, ""])                                     # Protokoll ohne Namen: Anschreiben
        self.assertEqual(j2["name"], "Anschreiben Mustermann")

    def test_bereinigung(self):
        f = pdf_eingang.name_aus_dateiname
        self.assertEqual(f("C:\\Users\\x\\Ordner\\Bericht.pdf"), "Bericht")
        self.assertEqual(f("../../etc/passwd.pdf"), "passwd")
        self.assertEqual(f("a\x00b\nc\td.pdf"), "a b c d")
        self.assertEqual(f("  viel   Platz .PDF"), "viel Platz")
        self.assertEqual(len(f("x" * 500 + ".pdf")), 120)
        for leer in (None, "", ".pdf", "   ", 5, "\x00\x01"):
            self.assertIsNone(f(leer), leer)
        self.assertEqual(f("<b>fett<b>.pdf"), "<b>fett<b>")                                              # kein Umbau von Zeichen; die Oberfläche setzt es als Text ein

    def test_name_steht_nirgends_ausser_in_der_vorgangszeile(self):
        self.hoch([NAME_B, NAME_A])
        with db.verbindung() as c:
            audit = json.dumps([dict(z) for z in c.execute("SELECT * FROM audit_log")], default=str)
            dok = json.dumps([dict(z) for z in c.execute("SELECT * FROM dokument")], default=str)
        for geheim in ("Mustermann", "Erika"):
            self.assertNotIn(geheim, audit)
            self.assertNotIn(geheim, dok)
            self.assertNotIn(geheim, self.logtext.getvalue() if hasattr(self, "logtext") else "")

    def test_fremde_beratungsstelle_sieht_den_namen_nicht(self):
        j = self.hoch([NAME_B, NAME_A])
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"], [])
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % j["vorgang_id"]).status_code, 404)

    def test_loeschen_leert_den_namen(self):
        j = self.hoch([NAME_B, NAME_A])
        r = self.c.delete("/api/v1/vorgaenge/%d" % j["vorgang_id"], json={"bestaetigt": True})
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        with db.verbindung() as c:
            z = dict(c.execute("SELECT * FROM vorgang WHERE id=?", (j["vorgang_id"],)).fetchone())
        self.assertEqual((z["status"], z["name"]), ("geloescht", None))
        self.assertNotIn("Mustermann", self.db_text())

    def test_aufbewahrung_und_vertragsende_leeren_den_namen(self):
        import loeschen
        j = self.hoch([NAME_B, NAME_A])
        self.assertIsNotNone(loeschen.vorgang_loeschen("1001", j["vorgang_id"], "aufbewahrung"))
        with db.verbindung() as c:
            self.assertIsNone(c.execute("SELECT name FROM vorgang WHERE id=?", (j["vorgang_id"],)).fetchone()["name"])
        j = self.hoch([NAME_B, NAME_A])
        with db.verbindung() as c:
            c.execute("UPDATE vorgang SET gezaehlt_am=? WHERE id=?", (_zeit(), j["vorgang_id"]))
        import inspect
        quelle = inspect.getsource(db)
        self.assertIn("name=NULL, email_enc=NULL", quelle)                                               # Vertragsende-Zähleintrag leert den Namen ebenfalls


class TestListe(RouteBasis):
    def test_liste_mit_personen_und_link_zustand(self):
        ehepaar = hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf(), namen=["b.pdf", "a.pdf"]).get_json()["vorgang_id"]
        ohne_link = hochladen(self.c, pdfs.b_pdf(), namen=["nur-b.pdf"]).get_json()["vorgang_id"]
        with db.verbindung() as c:
            c.execute("INSERT INTO link(vorgang_id, mandant_ref, token_hash, erstellt, laeuft_ab, gesperrt) VALUES (?,?,?,?,?,1)", (ehepaar, "1001", "h0", _zeit(days=-9), _zeit(days=-6)))   # älterer, ersetzter Link
            c.execute("INSERT INTO link(vorgang_id, mandant_ref, token_hash, erstellt, laeuft_ab, gesperrt) VALUES (?,?,?,?,?,0)", (ehepaar, "1001", "h1", _zeit(), _zeit(days=3)))
        j = self.c.get("/api/v1/vorgaenge").get_json()
        liste = {e["id"]: e for e in j["vorgaenge"]}
        self.assertEqual([e["id"] for e in j["vorgaenge"]], sorted(liste, reverse=True))
        self.assertEqual((liste[ehepaar]["link_status"], liste[ehepaar]["personen"]), ("aktiv", 2))
        self.assertTrue(liste[ehepaar]["link_laeuft_ab"].endswith("Z"))
        self.assertEqual((liste[ohne_link]["link_status"], liste[ohne_link]["link_laeuft_ab"]), (None, None))
        for k in ("id", "status", "erstellt", "name", "personen", "link_status", "link_laeuft_ab"):
            self.assertIn(k, liste[ehepaar])
        for verboten in ("email_enc", "code_hmac", "abgleich", "token_hash"):
            self.assertNotIn(verboten, liste[ehepaar])
        with db.verbindung() as c:
            c.execute("UPDATE link SET laeuft_ab=? WHERE vorgang_id=?", (_zeit(days=-1), ehepaar))
        self.assertEqual({e["id"]: e for e in self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"]}[ehepaar]["link_status"], "abgelaufen")
        with db.verbindung() as c:
            c.execute("UPDATE link SET laeuft_ab=?, gesperrt=1 WHERE vorgang_id=?", (_zeit(days=3), ehepaar))
        self.assertEqual({e["id"]: e for e in self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"]}[ehepaar]["link_status"], "gesperrt")

    def test_geloeschte_stehen_nicht_in_der_liste(self):
        vid = hochladen(self.c, pdfs.b_pdf(), namen=["x.pdf"]).get_json()["vorgang_id"]
        self.assertEqual(self.c.delete("/api/v1/vorgaenge/%d" % vid, json={"bestaetigt": True}).status_code, 200)
        self.assertEqual(self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"], [])

    def test_statustexte_decken_alle_status_ab(self):
        t = self.c.get("/api/v1/vorgaenge").get_json()["status_texte"]
        for s in ("neu", "bestaetigung_noetig", "bereit", "versenden", "versendet", "versand_unklar", "unterschrieben", "abschluss_laeuft",
                  "abschluss_fehlgeschlagen", "abgeschlossen", "geloescht"):
            self.assertTrue(t.get(s), s)
        self.assertNotIn("abgelaufen", t)                                                                 # abgelaufen ist der Link-Zustand, kein Status


class TestLoeschen(RouteBasis):
    def neu(self):
        return hochladen(self.c, pdfs.b_pdf(), pdfs.a_pdf(), namen=["b.pdf", "a.pdf"]).get_json()["vorgang_id"]

    def test_bestaetigung_ist_pflicht(self):
        vid = self.neu()
        for body in (None, {}, {"bestaetigt": False}, {"bestaetigt": "ja"}, {"bestaetigt": 1}, {"bestaetigt": True, "x": 1}, [], "x"):
            r = self.c.delete("/api/v1/vorgaenge/%d" % vid, json=body)
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "bestaetigung_fehlt"), body)
        self.assertEqual(db.vorgang_lesen("1001", vid)["status"], "bereit")
        self.assertTrue(db.dokumente_liste("1001", vid))

    def test_loeschen_entfernt_dateien_zeilen_und_schreibt_audit(self):
        vid = self.neu()
        verz = db.dokumente_verzeichnis("1001") / str(vid)
        self.assertTrue(any(verz.rglob("*.pdf")))
        r = self.c.delete("/api/v1/vorgaenge/%d" % vid, json={"bestaetigt": True})
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        self.assertFalse(verz.exists())
        self.assertEqual(db.dokumente_liste("1001", vid), [])
        with db.verbindung() as c:
            aktionen = [z["aktion"] for z in c.execute("SELECT aktion FROM audit_log WHERE mandant_ref='1001'")]
            self.assertEqual(c.execute("SELECT COUNT(*) FROM loeschprotokoll WHERE vorgang_id=?", (vid,)).fetchone()[0], 1)
        self.assertIn("vorgang_geloescht_durch_beratungsstelle", aktionen)
        r = self.c.delete("/api/v1/vorgaenge/%d" % vid, json={"bestaetigt": True})                         # zweites Mal: gibt es nicht mehr
        self.assertEqual((r.status_code, r.get_json()["error"]), (404, "nicht_gefunden"))

    def test_nicht_waehrend_abschluss_oder_versand(self):
        for status, fehler in (("abschluss_laeuft", "abschluss_laeuft"), ("versenden", "versand_laeuft")):
            vid = self.neu()
            with db.verbindung() as c:
                c.execute("UPDATE vorgang SET status=? WHERE id=?", (status, vid))
            r = self.c.delete("/api/v1/vorgaenge/%d" % vid, json={"bestaetigt": True})
            self.assertEqual((r.status_code, r.get_json()["error"]), (409, fehler))
            self.assertEqual(db.vorgang_lesen("1001", vid)["status"], status)
            self.assertTrue(db.dokumente_liste("1001", vid))

    def test_nur_eigene_beratungsstelle(self):
        vid = self.neu()
        self.als("tb", me(email="b@test.example", ref="2002"))
        r = self.c.delete("/api/v1/vorgaenge/%d" % vid, json={"bestaetigt": True})
        self.assertEqual((r.status_code, r.get_json()["error"]), (404, "nicht_gefunden"))
        self.assertEqual(db.vorgang_lesen("1001", vid)["status"], "bereit")
        self.assertTrue(db.dokumente_liste("1001", vid))

    def test_unbekannte_nummer(self):
        self.assertEqual(self.c.delete("/api/v1/vorgaenge/99999", json={"bestaetigt": True}).status_code, 404)

    def test_offene_handy_uebergabe_wird_mit_geloescht(self):
        vid = self.neu()
        with db.verbindung() as c:
            c.execute("INSERT INTO handoff(handoff_id, token_hash, sitzung_id, vorgang_id, mandant_ref, person, status, erstellt, laeuft_ab, bild_enc) VALUES "
                      "('h-test-0001','th',1,?,'1001',1,'gezeichnet',?,?,'x')", (vid, _zeit(), _zeit(minutes=5)))
        self.assertEqual(self.c.delete("/api/v1/vorgaenge/%d" % vid, json={"bestaetigt": True}).status_code, 200)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM handoff WHERE vorgang_id=?", (vid,)).fetchone()[0], 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
