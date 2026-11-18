"""v0.7.0 Einzel-Signatur (catrin 18.11.2026): jedes Dokument wird einzeln gelesen, unterschrieben und mit eigenem Nachweis abgeschlossen.
Datenmodell und Umbau der Tabelle, Altvorgänge mit zusammengeführtem Dokument, Personen je Dokument, Lese-Stand je Dokument, Zustimmung einmal je Person, Fortschritt für die Beratungsstelle. Nur erfundene Daten."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import abgleich
import db
from tests.test_baustein4 import SEITEN_A, SEITEN_B, png_bytes, uri
from tests.test_baustein5 import B5

ALTES_SCHEMA = """CREATE TABLE unterschrift (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vorgang_id INTEGER NOT NULL, mandant_ref TEXT NOT NULL, person INTEGER NOT NULL, bild BLOB NOT NULL, bild_sha256 TEXT NOT NULL,
    zeit_texte_angezeigt TEXT NOT NULL, zeit_unterschrift TEXT NOT NULL, text_kennung TEXT NOT NULL, text_hash TEXT NOT NULL, haken INTEGER NOT NULL, klick INTEGER NOT NULL,
    ip TEXT NOT NULL DEFAULT '', geraet TEXT NOT NULL DEFAULT '', UNIQUE(vorgang_id, person));"""


class TestTabellenumbau(unittest.TestCase):
    def test_alte_unterschriften_gehoeren_zum_zusammengefuehrten_dokument(self):
        with tempfile.TemporaryDirectory() as t:
            pfad = str(Path(t) / "alt.db")
            db.init(pfad)                                                                                              # aktuelle Tabellen, daraus entsteht der Zustand einer Datenbank vor v0.7.0
            with db.verbindung() as c:
                c.execute("INSERT INTO vorgang(id, mandant_ref, status, erstellt) VALUES (7, '1001', 'unterschrieben', '2026-11-01T10:00:00Z')")
                c.execute("DROP TABLE unterschrift")
            c = sqlite3.connect(pfad)
            c.executescript(ALTES_SCHEMA)
            c.execute("INSERT INTO unterschrift(vorgang_id, mandant_ref, person, bild, bild_sha256, zeit_texte_angezeigt, zeit_unterschrift, text_kennung, text_hash, haken, klick) "
                      "VALUES (7,'1001',1,x'89504e47','h','2026-11-01T10:00:00Z','2026-11-01T10:05:00Z','kennung','th',1,1)")
            c.commit()
            c.close()
            db.init(pfad)
            with db.verbindung() as c:
                zeilen = [dict(z) for z in c.execute("SELECT * FROM unterschrift")]
                self.assertEqual([(z["vorgang_id"], z["person"], z["dokument_rolle"], z["haken_uebernommen"]) for z in zeilen], [(7, 1, "M", 0)])
                self.assertEqual(bytes(zeilen[0]["bild"]), bytes.fromhex("89504e47"))
                self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='unterschrift_alt'").fetchone())
                c.execute("INSERT INTO unterschrift(vorgang_id, mandant_ref, person, dokument_rolle, bild, bild_sha256, zeit_texte_angezeigt, zeit_unterschrift, text_kennung, text_hash, haken, klick) "
                          "VALUES (7,'1001',1,'A',x'00','h','z','z','k','t',1,1)")                                  # dieselbe Person, anderes Dokument: jetzt erlaubt
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute("INSERT INTO unterschrift(vorgang_id, mandant_ref, person, dokument_rolle, bild, bild_sha256, zeit_texte_angezeigt, zeit_unterschrift, text_kennung, text_hash, haken, klick) "
                              "VALUES (7,'1001',1,'A',x'00','h','z','z','k','t',1,1)")                              # dasselbe Dokument und dieselbe Person: nicht erlaubt
            db.init(pfad)                                                                                              # zweiter Start: nichts passiert, nichts geht verloren
            with db.verbindung() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM unterschrift").fetchone()[0], 2)
            for tabelle, spalte in (("sitzung", "dok_stand"), ("handoff", "dokument_rolle")):
                with db.verbindung() as c:
                    self.assertIn(spalte, {z["name"] for z in c.execute("PRAGMA table_info(%s)" % tabelle)})


class TestEinzelSignatur(B5):
    def test_lese_stand_gilt_je_dokument(self):
        self.starten()
        self.einloggen()
        self.assertEqual(self.hole("/seite/1?dokument=A").status_code, 200)                  # Protokoll ansehen geht jederzeit
        self.assertEqual(self.hole("/seite/%d?dokument=A" % (SEITEN_A + 1)).status_code, 404)
        self.assertEqual(self.hole("/seite/1?dokument=X").status_code, 404)
        r = self.post("/gelesen", {"bestaetigt": True, "dokument": "A"})                     # nicht alle Seiten des Protokolls gesehen
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "seiten_fehlen"))
        r = self.post("/gelesen", {"bestaetigt": True, "dokument": "X"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "dokument_ungueltig"))
        self.lesen()                                                                          # aktuelles Dokument: Anschreiben
        st = self.status()
        self.assertEqual((st["schritt"], st["dokument"]["rolle"], st["geliefert"]), ("texte", "B", SEITEN_B))
        self.assertEqual(self.sign(1, "x", dokument="A").get_json()["error"], "lesen_fehlt")                                # Lesen des Anschreibens zählt nicht für das Protokoll

    def test_zustimmung_einmal_je_person_und_vorgang(self):
        self.starten()
        self.einloggen()
        self.lesen()
        r = self.sign(1, "", haken=None)                                                      # erste Unterschrift dieser Person: Texte und Haken sind Pflicht
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "texte_fehlen"))
        h = self.texte_holen()["hash"]
        self.assertEqual(self.status()["texte_noetig"], True)
        r = self.sign(1, h, haken=False)
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "zustimmung_fehlt"))
        self.assertEqual(self.sign(1, h).status_code, 200)
        self.assertEqual(self.status()["texte_noetig"], True)                                # Person 2 hat noch nicht zugestimmt
        self.assertEqual(self.sign(2, self.texte_holen()["hash"]).status_code, 200)
        self.lesen()                                                                          # Protokoll
        st = self.status()
        self.assertEqual((st["dokument"]["rolle"], st["texte_noetig"], st["schritt"]), ("A", False, "unterschreiben"))
        r = self.post("/unterschrift", {"dokument": "A", "person": 1, "bild": uri(png_bytes()), "ich_unterschreibe": True})      # ohne haken und text_hash
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        r = self.post("/unterschrift", {"dokument": "A", "person": 2, "bild": uri(png_bytes()), "ich_unterschreibe": False})
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "zustimmung_fehlt"))                                   # eigener Klick bleibt immer Pflicht
        self.assertEqual(self.post("/unterschrift", {"dokument": "A", "person": 2, "bild": uri(png_bytes()), "ich_unterschreibe": True}).get_json(), {"fertig": True})
        zeilen = {(z["dokument_rolle"], z["person"]): z for z in self.alle_unterschriften()}
        self.assertEqual({k: z["haken_uebernommen"] for k, z in zeilen.items()}, {("B", 1): 0, ("B", 2): 0, ("A", 1): 1, ("A", 2): 1})
        for person in (1, 2):                                                                 # Zeit, Kennung und Prüfsumme der Texte stammen aus der ersten Unterschrift
            for feld in ("zeit_texte_angezeigt", "text_kennung", "text_hash"):
                self.assertEqual(zeilen[("A", person)][feld], zeilen[("B", person)][feld], feld)

    def alle_unterschriften(self):
        with db.verbindung() as c:
            return [dict(z) for z in c.execute("SELECT * FROM unterschrift ORDER BY dokument_rolle, person")]

    def test_nur_personen_mit_stelle_im_dokument_unterschreiben(self):
        self.starten()
        with db.verbindung() as c:                                                            # im Anschreiben gibt es nur eine Stelle für Person 1
            z = c.execute("SELECT id, stellen FROM dokument WHERE rolle='B'").fetchone()
            nur_eins = [s for s in json.loads(z["stellen"]) if s["person"] == 1]
            c.execute("UPDATE dokument SET stellen=? WHERE id=?", (json.dumps(nur_eins), z["id"]))
        self.einloggen()
        st = self.status()
        self.assertEqual([(d["rolle"], d["erforderlich"]) for d in st["dokumente"]], [("B", [1]), ("A", [1, 2])])
        self.lesen()
        h = self.texte_holen()["hash"]
        r = self.sign(2, h)
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "reihenfolge"))
        r = self.sign(1, h)
        self.assertEqual(r.get_json()["naechste"], {"dokument": "A", "person": 1})
        self.assertTrue(r.get_json()["dokument_fertig"])
        self.alles_unterschreiben()
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")
        self.assertEqual(self.rollen(), ["N_A", "N_B", "S_A", "S_B"])
        self.assertEqual([(z["dokument_rolle"], z["person"]) for z in self.alle_unterschriften()], [("A", 1), ("A", 2), ("B", 1)])
        self.assertEqual(self.bilder_je_seite_rolle("S_B"), {1: 1, 4: 1})

    def test_altvorgang_mit_zusammengefuehrtem_dokument_laeuft_als_ein_dokument(self):
        self.starten()
        zeilen = {d["rolle"]: d for d in db.dokumente_liste("1001", self.vid)}               # Zustand wie vor v0.7.0: M = Anschreiben + Protokoll, Originale daneben
        daten, stellen = abgleich.zusammenfuehren(db.dokument_bytes("1001", self.vid, "B"), db.dokument_bytes("1001", self.vid, "A"), zeilen["B"]["stellen"], zeilen["A"]["stellen"])
        db.dokument_speichern("1001", self.vid, "M", daten, hashlib.sha256(daten).hexdigest(), zeilen["B"]["seiten"] + zeilen["A"]["seiten"], stellen)
        self.einloggen()
        st = self.status()
        self.assertEqual((st["dokument"]["rolle"], st["dokument"]["von"], st["seiten"], [d["rolle"] for d in st["dokumente"]]), ("M", 1, SEITEN_B + SEITEN_A, ["M"]))
        self.assertEqual(self.alles_unterschreiben().get_json(), {"fertig": True})
        self.assertEqual(self.vorgang()["status"], "abgeschlossen")
        self.assertEqual(self.rollen(), ["N", "S"])                                          # wie früher: ein unterschriebenes Dokument, ein Nachweis
        self.assertEqual({z["dokument_rolle"] for z in self.alle_unterschriften()}, {"M"})
        self.assertEqual(self.bilder_je_seite_rolle("S"), {1: 2, 4: 2, 15: 2})
        nachweis = db.dokument_bytes("1001", self.vid, "N")
        from pypdf import PdfReader
        import io
        text = " ".join(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(nachweis)).pages).split())
        self.assertNotIn("Dokument: ", text)                                                 # kein Dokumentname beim zusammengeführten Altdokument

    def bilder_je_seite_rolle(self, rolle):
        import io
        from pypdf import PdfReader
        leser = PdfReader(io.BytesIO(db.dokument_bytes("1001", self.vid, rolle)))
        return {i: len(p.images) for i, p in enumerate(leser.pages, 1) if len(p.images)}

    def test_fortschritt_fuer_die_beratungsstelle(self):
        self.starten()
        j = self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_json()
        self.assertEqual([(d["rolle"], d["titel"], d["erforderlich"], d["unterschrieben"], d["fertig"]) for d in j["dokumente_fortschritt"]],
                         [("B", "Vollständigkeitserklärung", [1, 2], [], False), ("A", "Freizeichnungsprotokoll", [1, 2], [], False)])
        e = [x for x in self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"] if x["id"] == self.vid][0]
        self.assertEqual((e["dokumente_gesamt"], e["dokumente_fertig"]), (2, 0))
        self.einloggen()
        self.lesen()
        h = self.texte_holen()["hash"]
        self.sign(1, h)
        self.sign(2, self.texte_holen()["hash"])                                              # Anschreiben fertig
        j = self.c.get("/api/v1/vorgaenge/%d" % self.vid).get_json()
        self.assertEqual([(d["rolle"], d["unterschrieben"], d["fertig"]) for d in j["dokumente_fortschritt"]], [("B", [1, 2], True), ("A", [], False)])
        e = [x for x in self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"] if x["id"] == self.vid][0]
        self.assertEqual((e["dokumente_gesamt"], e["dokumente_fertig"]), (2, 1))
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/download/dokument?dokument=B" % self.vid).status_code, 404)     # noch nicht abgeschlossen

    def test_ansicht_der_originale_je_dokument(self):
        self.starten()
        b = self.c.get("/api/v1/vorgaenge/%d/dokument?dokument=B" % self.vid)
        a = self.c.get("/api/v1/vorgaenge/%d/dokument?dokument=A" % self.vid)
        self.assertEqual((b.status_code, b.get_data()), (200, db.dokument_bytes("1001", self.vid, "B")))
        self.assertEqual((a.status_code, a.get_data()), (200, db.dokument_bytes("1001", self.vid, "A")))
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/dokument" % self.vid).get_data(), db.dokument_bytes("1001", self.vid, "B"))      # ohne Angabe: das erste
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d/dokument?dokument=M" % self.vid).status_code, 404)

    def test_hochladen_erzeugt_kein_zusammengefuehrtes_dokument(self):
        self.starten()
        self.assertEqual(sorted(d["rolle"] for d in db.dokumente_liste("1001", self.vid)), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
