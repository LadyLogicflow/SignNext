"""jsdom-Prüfung des Ablaufs "jedes Dokument einzeln lesen, zeichnen und signieren" (catrin 18.11.2026) auf /u, /h und /d.
Die Schnittstelle spielt der Treiber (drive_sn.js) nach der Beschreibung von Sorty nach: GET /status mit dokument/dokumente/texte_noetig, ?dokument=<rolle> bei /seite,
POST /gelesen und /unterschrift mit dokument, Handoff je Dokument, Download-Liste bei mehreren Dokumenten. Reihenfolge: Dokument für Dokument, darin Person 1, dann Person 2."""
import unittest

from ui_hilfen import BEREIT, HINWEIS, lauf
from test_ui_unterschrift import PNG, STRICH

ZWEI_DOKUMENTE = [{"rolle": "B", "titel": "Anschreiben", "seiten": 2}, {"rolle": "A", "titel": "Protokoll", "seiten": 3}]
LESEN = [{"op": "seiten_geladen", "warte": 100}, {"op": "gelesen", "warte": 600}]
SIGNIEREN = [{"op": "zeichne", "punkte": STRICH}, {"op": "unterschreiben", "warte": 700}]
CODE = {"op": "code", "wert": "01.02.1960", "warte": 700}


def ablauf(*teile):
    """Baut die Schrittliste und merkt sich, an welcher Stelle welcher Schritt steht: (ops, {name: index})."""
    ops, stellen = [], {}
    for name, schritte in teile:
        ops.extend(schritte)
        stellen[name] = len(ops) - 1
    return ops, stellen


def z(o, i):
    return o["schritte"][i]["zustand"]


def bodies(zustand, pfad):
    return [r["body"] for r in zustand["requests"] if r["m"] == "POST" and r["p"] == pfad]


def seiten_adressen(zustand):
    return [r["p"] + r["q"] for r in zustand["requests"] if r["p"].startswith("/api/v1/zugang/seite/")]


@unittest.skipUnless(BEREIT, HINWEIS)
class TestZweiDokumenteEinePerson(unittest.TestCase):
    def test_jedes_dokument_wird_einzeln_gelesen_und_unterschrieben(self):
        ops, i = ablauf(("code", [CODE]), ("gelesen1", LESEN), ("unterschrieben1", [{"op": "haken", "warte": 50}] + SIGNIEREN),
                        ("weiter", [{"op": "klick_id", "id": "btn-naechstes-dokument", "warte": 700}]), ("gelesen2", LESEN), ("unterschrieben2", SIGNIEREN))
        o = lauf(seite="/u", fragment="#tok", api={"personen": 1, "dokumente": ZWEI_DOKUMENTE}, ops=ops)
        # Dokument 1 lesen
        a = z(o, i["code"])
        self.assertEqual(a["schritt"], ["step-seiten"])
        self.assertEqual(a["dokument_titel"], "Dokument 1 von 2: Anschreiben")
        self.assertEqual([f["text"] for f in a["fortschritt"]], ["Dokument 1: Anschreiben", "Dokument 2: Protokoll"])
        self.assertEqual([f["klasse"] for f in a["fortschritt"]], ["dok-aktuell", "dok-offen"])
        self.assertEqual(seiten_adressen(a), ["/api/v1/zugang/seite/1?dokument=B", "/api/v1/zugang/seite/2?dokument=B"])
        # Dokument 1 zeichnen: Dokumentname als eigene Zeile, Datenschutz und Haken beim ersten Mal
        b = z(o, i["gelesen1"])
        self.assertEqual(bodies(b, "/api/v1/zugang/gelesen"), [{"bestaetigt": True, "dokument": "B"}])
        self.assertEqual((b["schritt"], b["dokument_zeile"], b["label"], b["texte_block_sichtbar"]), (["step-unterschrift"], "Sie unterschreiben: Dokument 1 von 2: Anschreiben", "Unterschrift Dokument 1 von 2", True))
        # Dokument 1 unterschrieben: Zwischenschritt
        c = z(o, i["unterschrieben1"])
        self.assertEqual(bodies(c, "/api/v1/zugang/unterschrift"), [{"dokument": "B", "person": 1, "bild": PNG, "ich_unterschreibe": True, "haken": True, "text_hash": "h1"}])
        self.assertEqual(c["schritt"], ["step-dokument-fertig"])
        self.assertEqual(c["dokument_fertig"], {"titel": "Dokument 1 von 2 unterschrieben ✓", "text": "Anschreiben ist von allen Personen unterschrieben.", "knopf": "Weiter zu Dokument 2: Protokoll"})
        self.assertEqual([f["klasse"] for f in c["fortschritt"]], ["dok-fertig", "dok-offen"])
        self.assertEqual(c["fortschritt"][0]["marke"], "✓")
        # Dokument 2 lesen
        d = z(o, i["weiter"])
        self.assertEqual((d["schritt"], d["dokument_titel"]), (["step-seiten"], "Dokument 2 von 2: Protokoll"))
        self.assertEqual(seiten_adressen(d), ["/api/v1/zugang/seite/1?dokument=A", "/api/v1/zugang/seite/2?dokument=A", "/api/v1/zugang/seite/3?dokument=A"])
        self.assertEqual([f["klasse"] for f in d["fortschritt"]], ["dok-fertig", "dok-aktuell"])
        # Dokument 2 zeichnen: Datenschutz und Haken entfallen (schon bestätigt), kein haken/text_hash
        e = z(o, i["gelesen2"])
        self.assertEqual((e["dokument_zeile"], e["label"], e["texte_block_sichtbar"]), ("Sie unterschreiben: Dokument 2 von 2: Protokoll", "Unterschrift Dokument 2 von 2", False))
        f = z(o, i["unterschrieben2"])
        self.assertEqual(bodies(f, "/api/v1/zugang/unterschrift"), [{"dokument": "A", "person": 1, "bild": PNG, "ich_unterschreibe": True}])
        self.assertEqual(f["schritt"], ["step-fertig"])
        self.assertEqual(o["fehler_js"], [])
        self.assertEqual([z(o, k)["alerts"] for k in range(len(ops))], [[]] * len(ops))

    def test_haken_wird_beim_ersten_dokument_verlangt(self):
        ops, i = ablauf(("code", [CODE]), ("gelesen1", LESEN), ("ohne", SIGNIEREN))
        o = lauf(seite="/u", fragment="#tok", api={"personen": 1, "dokumente": ZWEI_DOKUMENTE}, ops=ops)
        self.assertEqual(bodies(z(o, i["ohne"]), "/api/v1/zugang/unterschrift"), [])                      # ohne Haken wird nichts gesendet
        self.assertEqual(z(o, i["ohne"])["fehler_unterschrift"], "Bitte bestätigen Sie, dass Sie die Datenschutzinformation gelesen haben")


@unittest.skipUnless(BEREIT, HINWEIS)
class TestZweiDokumenteZweiPersonen(unittest.TestCase):
    def test_reihenfolge_dokument_fuer_dokument_darin_person_1_dann_person_2(self):
        ops, i = ablauf(("code", [CODE]), ("gelesen1", LESEN), ("b1", [{"op": "haken", "warte": 50}] + SIGNIEREN), ("b2", [{"op": "haken", "warte": 50}] + SIGNIEREN),
                        ("weiter", [{"op": "klick_id", "id": "btn-naechstes-dokument", "warte": 700}]), ("gelesen2", LESEN), ("a1", SIGNIEREN), ("a2", SIGNIEREN))
        o = lauf(seite="/u", fragment="#tok", api={"personen": 2, "dokumente": ZWEI_DOKUMENTE}, ops=ops)
        gesendet = []
        for k in range(len(ops)):
            gesendet += [(b["dokument"], b["person"], "haken" in b) for b in bodies(z(o, k), "/api/v1/zugang/unterschrift")]
        self.assertEqual(gesendet, [("B", 1, True), ("B", 2, True), ("A", 1, False), ("A", 2, False)])
        # nach der ersten Unterschrift: gleiches Dokument, Person 2, Datenschutz noch einmal (einmal je Person)
        nach_b1 = z(o, i["b1"])
        self.assertEqual(nach_b1["schritt"], ["step-unterschrift"])
        self.assertEqual(nach_b1["person_info"], "Person 1 hat unterschrieben. Jetzt ist Person 2 an der Reihe.")
        self.assertEqual((nach_b1["label"], nach_b1["texte_block_sichtbar"]), ("Unterschrift Dokument 1 von 2 · Person 2 von 2", True))
        self.assertEqual(nach_b1["fortschritt"][0]["text"], "Dokument 1: Anschreiben (1 von 2 unterschrieben)")
        # Dokument 1 ist mit beiden Personen fertig
        self.assertEqual(z(o, i["b2"])["schritt"], ["step-dokument-fertig"])
        self.assertEqual(z(o, i["b2"])["dokument_fertig"]["text"], "Anschreiben ist von allen Personen unterschrieben.")
        # Dokument 2: beide Personen ohne Datenschutz/Haken
        nach_gelesen2 = z(o, i["gelesen2"])
        self.assertEqual((nach_gelesen2["label"], nach_gelesen2["texte_block_sichtbar"]), ("Unterschrift Dokument 2 von 2 · Person 1 von 2", False))
        nach_a1 = z(o, i["a1"])
        self.assertEqual((nach_a1["label"], nach_a1["texte_block_sichtbar"]), ("Unterschrift Dokument 2 von 2 · Person 2 von 2", False))
        self.assertEqual(z(o, i["a2"])["schritt"], ["step-fertig"])
        self.assertEqual(o["fehler_js"], [])

    def test_ein_dokument_mit_nur_einer_unterschriftsstelle_zaehlt_nur_diese_person(self):
        dokumente = [{"rolle": "B", "titel": "Anschreiben", "seiten": 2, "erforderlich": [1]}, {"rolle": "A", "titel": "Protokoll", "seiten": 3}]
        ops, i = ablauf(("code", [CODE]), ("gelesen1", LESEN))
        o = lauf(seite="/u", fragment="#tok", api={"personen": 2, "dokumente": dokumente}, ops=ops)
        self.assertEqual(z(o, i["gelesen1"])["label"], "Unterschrift Dokument 1 von 2")                  # nur eine Person unterschreibt dieses Dokument: kein "Person 1 von 2"


@unittest.skipUnless(BEREIT, HINWEIS)
class TestEinDokumentBleibtWieBisher(unittest.TestCase):
    def test_ohne_dokumentzeile_fortschritt_und_dokumentparameter(self):
        ops, i = ablauf(("code", [CODE]), ("gelesen", LESEN), ("sign", [{"op": "haken", "warte": 50}] + SIGNIEREN))
        o = lauf(seite="/u", fragment="#tok", api={"personen": 1}, ops=ops)
        a, b = z(o, i["code"]), z(o, i["gelesen"])
        self.assertEqual((a["fortschritt"], a["dokument_titel"], b["dokument_zeile"], b["label"]), (None, None, None, "Unterschrift"))
        self.assertEqual(seiten_adressen(a), ["/api/v1/zugang/seite/1", "/api/v1/zugang/seite/2"])        # kein ?dokument=
        self.assertEqual(bodies(b, "/api/v1/zugang/gelesen"), [{"bestaetigt": True}])
        self.assertEqual(bodies(z(o, i["sign"]), "/api/v1/zugang/unterschrift"), [{"person": 1, "bild": PNG, "ich_unterschreibe": True, "haken": True, "text_hash": "h1"}])
        self.assertEqual(z(o, i["sign"])["schritt"], ["step-fertig"])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestFehlerUndHandoff(unittest.TestCase):
    def test_nach_einem_stand_fehler_wird_neu_abgeglichen_und_der_text_des_servers_steht_da(self):
        fehler = {"status": 409, "error": "lesen_fehlt", "meldung": "Bitte lesen Sie dieses Dokument zuerst vollständig."}
        ops, i = ablauf(("code", [CODE]), ("gelesen1", LESEN), ("sign", [{"op": "haken", "warte": 50}] + SIGNIEREN))
        o = lauf(seite="/u", fragment="#tok", api={"personen": 1, "dokumente": ZWEI_DOKUMENTE, "unterschriftFehler": fehler}, ops=ops)
        s = z(o, i["sign"])
        self.assertEqual(s["fehler_unterschrift"], "Bitte lesen Sie dieses Dokument zuerst vollständig.")
        status_nach = [r for r in s["requests"] if r["m"] == "GET" and r["p"] == "/api/v1/zugang/status"]
        self.assertTrue(status_nach, "die Seite gleicht ihren Stand mit dem Server ab")
        self.assertEqual(s["alerts"], [])

    def test_handoff_nennt_bei_mehreren_dokumenten_das_dokument(self):
        ops, i = ablauf(("code", [CODE]), ("gelesen1", LESEN), ("handy", [{"op": "handy", "warte": 400}]))
        mehrere = lauf(seite="/u", fragment="#tok", api={"personen": 2, "dokumente": ZWEI_DOKUMENTE}, ops=ops)
        self.assertEqual(bodies(z(mehrere, i["handy"]), "/api/v1/zugang/handoff"), [{"person": 1, "dokument": "B"}])
        ein = lauf(seite="/u", fragment="#tok", api={"personen": 2}, ops=ops)
        self.assertEqual(bodies(z(ein, i["handy"]), "/api/v1/zugang/handoff"), [{"person": 1}])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestHandySeite(unittest.TestCase):
    def test_handy_zeigt_dokument_und_person(self):
        info = {"person": 1, "personen": 2, "dokument": {"rolle": "A", "titel": "Protokoll", "nr": 2, "von": 2}, "status": "wartet"}
        o = lauf(seite="/h", fragment="#tok", api={"handyInfo": info}, ops=[])
        self.assertEqual((o["start"]["label"], o["start"]["handy_dokument"]), ("Unterschrift Dokument 2 von 2 · Person 1 von 2", "Sie unterschreiben: Dokument 2 von 2: Protokoll"))
        nur_eins = lauf(seite="/h", fragment="#tok", api={"handyInfo": dict(info, dokument={"rolle": "B", "titel": "Anschreiben", "nr": 1, "von": 1})}, ops=[])
        self.assertEqual((nur_eins["start"]["label"], nur_eins["start"]["handy_dokument"]), ("Unterschrift Person 1 von 2", None))
        ohne = lauf(seite="/h", fragment="#tok", api={"handyInfo": {"person": 1, "personen": 1, "status": "wartet"}}, ops=[])
        self.assertEqual((ohne["start"]["label"], ohne["start"]["handy_dokument"]), ("Unterschrift", None))


@unittest.skipUnless(BEREIT, HINWEIS)
class TestMitgliedDownload(unittest.TestCase):
    LISTE = [{"rolle": "B", "titel": "Anschreiben"}, {"rolle": "A", "titel": "Protokoll"}]

    def test_bei_mehreren_dokumenten_erst_die_auswahl_dann_der_abruf(self):
        ops = [{"op": "code", "form": "form-download", "wert": "01.02.1960", "warte": 400}, {"op": "klick_text", "sel": "#dokument-wahl-knoepfe button", "text": "Protokoll herunterladen", "warte": 400}]
        o = lauf(seite="/d", fragment="#tok", api={"downloadDokumente": self.LISTE}, ops=ops)
        erst, dann = z(o, 0), z(o, 1)
        self.assertEqual(erst["dokument_wahl"], ["Anschreiben herunterladen", "Protokoll herunterladen"])
        self.assertEqual(bodies(erst, "/api/v1/download"), [{"token": "tok", "code": "01021960"}])         # erst nur Code, noch kein Abruf
        self.assertEqual(erst["downloads_mitglied"], [])
        self.assertEqual(bodies(dann, "/api/v1/download"), [{"token": "tok", "code": "01021960", "dokument": "A"}])
        self.assertEqual(dann["downloads_mitglied"], ["Protokoll.pdf"])
        self.assertEqual(o["schritte"][1]["zustand"]["alerts"], [])

    def test_ein_dokument_wird_sofort_geladen(self):
        o = lauf(seite="/d", fragment="#tok", ops=[{"op": "code", "form": "form-download", "wert": "01021960", "warte": 400}])
        self.assertEqual((z(o, 0)["dokument_wahl"], z(o, 0)["downloads_mitglied"]), (None, ["dokument.pdf"]))
        self.assertEqual(bodies(z(o, 0), "/api/v1/download"), [{"token": "tok", "code": "01021960"}])

    def test_falscher_code_zeigt_keine_dokumentliste(self):
        o = lauf(seite="/d", fragment="#tok", api={"downloadDokumente": self.LISTE}, ops=[{"op": "code", "form": "form-download", "wert": "01.01.1990", "warte": 400}])
        self.assertEqual((z(o, 0)["dokument_wahl"], z(o, 0)["code_fehler"]), (None, "Das Geburtsdatum stimmt nicht"))


if __name__ == "__main__":
    unittest.main()
