"""v0.7.0 Ampel und 'abgeholt' (catrin 18.11.2026): rot = nicht vollständig unterschrieben, gelb = unterschrieben, nicht alle Dokument-PDFs abgeholt, grün = alle Dokument-PDFs abgeholt.
Nachweise zählen nicht für grün, nur die Beratungsstelle zählt, nur tatsächlich Ausgeliefertes zählt. Nur erfundene Daten."""
import re

import db
import jobs
from tests import pdfs
from tests.test_baustein1 import me
from tests.test_baustein5 import B5, jetzt

DL = "/api/v1/vorgaenge/%d/download/%s?dokument=%s"


class TestAmpel(B5):
    def eintrag(self):
        return [x for x in self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"] if x["id"] == self.vid][0]

    def detail(self):
        return self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_json()

    def abholen(self, art, rolle):
        r = self.c.get(DL % (self.vid, art, rolle))
        self.assertEqual(r.status_code, 200, (art, rolle))
        return r

    def test_ampel_von_hochgeladen_bis_abgeholt(self):
        self.vid = self.bereit()
        self.assertIsNone(self.eintrag()["ampel"])                                          # noch nicht versendet
        self.assertIsNone(self.detail()["ampel"])
        self.starten()                                                                        # neuer Vorgang, versendet: rot
        self.assertEqual((self.eintrag()["ampel"], self.detail()["ampel"]), ("rot", "rot"))
        self.einloggen()
        self.lesen()
        h = self.texte_holen()["hash"]
        self.sign(1, h)
        self.assertEqual(self.eintrag()["ampel"], "rot")                                    # teilweise unterschrieben bleibt rot
        self.sign(2, self.texte_holen()["hash"])
        self.assertEqual(self.eintrag()["ampel"], "rot")                                    # Anschreiben fertig, Protokoll fehlt
        self.alles_unterschreiben()
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")
        e = self.eintrag()
        self.assertEqual((e["ampel"], e["dokumente_gesamt"], e["dokumente_fertig"], e["dokumente_abgeholt"]), ("gelb", 2, 2, 0))
        self.abholen("dokument", "B")
        e = self.eintrag()
        self.assertEqual((e["ampel"], e["dokumente_abgeholt"]), ("gelb", 1))                # ein Dokument fehlt noch
        self.abholen("dokument", "A")
        e = self.eintrag()
        self.assertEqual((e["ampel"], e["dokumente_abgeholt"]), ("gruen", 2))
        self.assertEqual(self.detail()["ampel"], "gruen")

    def test_nachweise_zaehlen_nicht_fuer_gruen_haben_aber_einen_eigenen_zeitpunkt(self):
        self.unterschreiben()
        self.abholen("nachweis", "B")
        self.abholen("nachweis", "A")
        j = self.detail()
        self.assertEqual(j["ampel"], "gelb")
        self.assertEqual([(d["rolle"], d["abgeholt_am"], bool(d["nachweis_abgeholt_am"])) for d in j["dokumente_fortschritt"]], [("B", None, True), ("A", None, True)])
        self.abholen("dokument", "B")
        j = self.detail()
        d = {x["rolle"]: x for x in j["dokumente_fortschritt"]}
        self.assertTrue(d["B"]["abgeholt_am"].endswith("Z"))                                # sofort nach dem PDF, nicht erst nach dem Nachweis
        self.assertIsNone(d["A"]["abgeholt_am"])
        self.assertEqual(j["ampel"], "gelb")

    def test_erste_abholung_bleibt_erhalten_und_wird_gezaehlt(self):
        self.unterschreiben()
        self.abholen("dokument", "B")
        with db.verbindung() as c:
            c.execute("UPDATE abholung SET erstmals_am='2026-01-01T10:00:00Z', letzte_am='2026-01-01T10:00:00Z'")
        self.abholen("dokument", "B")
        with db.verbindung() as c:
            z = dict(c.execute("SELECT * FROM abholung WHERE dokument_rolle='B' AND art='dokument'").fetchone())
        self.assertEqual((z["erstmals_am"], z["anzahl"]), ("2026-01-01T10:00:00Z", 2))
        self.assertNotEqual(z["letzte_am"], z["erstmals_am"])
        d = {x["rolle"]: x for x in self.detail()["dokumente_fortschritt"]}
        self.assertEqual(d["B"]["abgeholt_am"], "2026-01-01T10:00:00Z")

    def test_nur_ausgeliefertes_zaehlt(self):
        self.unterschreiben()
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/download/dokument" % self.vid).status_code, 400)       # keine Wahl: nichts ausgeliefert
        self.assertEqual(self.c.get(DL % (self.vid, "dokument", "M")).status_code, 404)
        self.assertEqual(self.c.get(DL % (self.vid, "egal", "B")).status_code, 404)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM abholung").fetchone()[0], 0)
        self.assertEqual(self.eintrag()["ampel"], "gelb")

    def test_mitglied_download_zaehlt_nicht(self):
        self.unterschreiben()
        jobs.lauf(jetzt())                                                                    # Link für das Mitglied entsteht nach dem Abschluss
        token = re.search(r"/d#([A-Za-z0-9_-]+)", self.mails(pdfs.MAIL)[-1][1]).group(1)
        self.assertEqual(self.hole_download(token, dokument="B").status_code, 200)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM abholung").fetchone()[0], 0)
        self.assertEqual(self.eintrag()["ampel"], "gelb")

    def test_andere_beratungsstelle_sieht_und_zaehlt_nichts(self):
        self.unterschreiben()
        self.abholen("dokument", "B")
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.c.get(DL % (self.vid, "dokument", "A")).status_code, 404)
        self.assertEqual(self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"], [])
        with db.verbindung() as c:
            self.assertEqual([(z["mandant_ref"], z["dokument_rolle"]) for z in c.execute("SELECT * FROM abholung")], [("1001", "B")])

    def test_einzeldokument_vorgang_und_alte_vorgaenge(self):
        self.unterschreiben()
        with db.verbindung() as c:
            c.execute("DELETE FROM abholung")
        self.assertEqual(self.eintrag()["ampel"], "gelb")                                   # alter, abgeschlossener Vorgang ohne Abholung: gelb (nicht bekannt)

    def test_loeschen_raeumt_die_abholung_auf(self):
        self.unterschreiben()
        self.abholen("dokument", "B")
        self.abholen("nachweis", "A")
        r = self.c.delete("/api/v1/vorgaenge/%d" % self.vid, json={"bestaetigt": True})
        self.assertEqual(r.status_code, 200)
        with db.verbindung() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM abholung WHERE vorgang_id=?", (self.vid,)).fetchone()[0], 0)

    def test_audit_je_datei_ohne_namen(self):
        self.unterschreiben()
        self.abholen("dokument", "B")
        self.abholen("nachweis", "B")
        zeilen = self.audit_zeilen("dokument_heruntergeladen")
        self.assertEqual(sorted(z["details"] for z in zeilen), sorted(["id=%d art=dokument dokument=B" % self.vid, "id=%d art=nachweis dokument=B" % self.vid]))
        self.assertNotIn(pdfs.DOB, str([z["details"] for z in zeilen]))


if __name__ == "__main__":
    import unittest
    unittest.main()
