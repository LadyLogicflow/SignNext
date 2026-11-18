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

    def test_fehlercodes_des_servers_und_ihre_saetze(self):
        faelle = [("reihenfolge", "Bitte der Reihe nach unterschreiben."), ("dokument_nicht_dran", "Bitte zuerst das vorherige Dokument unterschreiben."), ("seiten_fehlen", "Bitte zuerst alle Seiten des Dokuments ansehen."),
                  ("texte_fehlen", "Bitte zuerst die Datenschutzinformation lesen."), ("text_geaendert", "Die Texte haben sich geändert. Bitte lesen Sie sie erneut.")]
        ops, i = ablauf(("code", [CODE]), ("gelesen1", LESEN), ("sign", [{"op": "haken", "warte": 50}] + SIGNIEREN))
        for code, satz in faelle:
            o = lauf(seite="/u", fragment="#tok", api={"personen": 1, "dokumente": ZWEI_DOKUMENTE, "unterschriftFehler": {"status": 409, "error": code, "meldung": satz}}, ops=ops)
            s = z(o, i["sign"])
            self.assertEqual(s["fehler_unterschrift"], satz, code)
            self.assertTrue([r for r in s["requests"] if r["m"] == "GET" and r["p"] == "/api/v1/zugang/status"], code)      # Stand wird abgeglichen
        # 409 schon_unterschrieben kommt ohne Text: eigener Satz, nie der Code
        o = lauf(seite="/u", fragment="#tok", api={"personen": 1, "dokumente": ZWEI_DOKUMENTE, "unterschriftFehler": {"status": 409, "error": "schon_unterschrieben", "meldung": None}}, ops=ops)
        s = z(o, i["sign"])
        self.assertEqual(s["fehler_unterschrift"], "Diese Unterschrift wurde schon abgegeben. Die Seite wird aktualisiert.")
        self.assertNotIn("schon_unterschrieben", s["fehler_unterschrift"])

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


def fortschritt(b=(2, True), a=(0, False), erforderlich=(1, 2)):
    """dokumente_fortschritt wie von Sortys Backend: Anschreiben B und Protokoll A, je (Zahl der Unterschriften, fertig)."""
    return [{"rolle": "B", "titel": "Anschreiben", "erforderlich": list(erforderlich), "unterschrieben": list(erforderlich[:b[0]]), "fertig": b[1]},
            {"rolle": "A", "titel": "Protokoll", "erforderlich": list(erforderlich), "unterschrieben": list(erforderlich[:a[0]]), "fertig": a[1]}]


def vorg(status="versendet", **mehr):
    return {"vorgang": dict({"status": status, "link_status": "aktiv", "versendet_am": "2026-11-16T08:21:00Z"}, **mehr)}


def v(o, i=None):
    return (o["start"] if i is None else o["schritte"][i]["zustand"])["berater"]["vorgang"]


SEITE = "/pruefung?id=1"


@unittest.skipUnless(BEREIT, HINWEIS)
class TestBeraterPruefenJeDokument(unittest.TestCase):
    def test_vorschau_je_dokument_mit_reitern(self):
        o = lauf(seite=SEITE, api=vorg("bereit", dokumente_fortschritt=fortschritt((0, False), (0, False))),
                 ops=[{"op": "klick_text", "sel": "#dokument-reiter button", "text": "Protokoll", "warte": 200}])
        p = v(o)["pruefen"]
        self.assertEqual([(r["text"], r["gedrueckt"]) for r in p["reiter"]], [("Anschreiben", "true"), ("Protokoll", "false")])
        self.assertEqual(p["pdf"], "/api/v1/vorgaenge/1/dokument?dokument=B")                                # erstes Dokument zuerst
        self.assertEqual(p["dokument_links"], ["/api/v1/vorgaenge/1/dokument?dokument=B"] * 2)                # Ausweg-Links passend zum Reiter
        self.assertIn("Jedes Dokument wird einzeln unterschrieben", p["dokument_hinweis"])
        q = v(o, 0)["pruefen"]
        self.assertEqual([(r["text"], r["gedrueckt"]) for r in q["reiter"]], [("Anschreiben", "false"), ("Protokoll", "true")])
        self.assertEqual((q["pdf"], q["dokument_links"]), ("/api/v1/vorgaenge/1/dokument?dokument=A", ["/api/v1/vorgaenge/1/dokument?dokument=A"] * 2))

    def test_ein_dokument_bleibt_wie_bisher(self):
        p = v(lauf(seite=SEITE, api=vorg("bereit"), ops=[]))["pruefen"]
        self.assertEqual((p["reiter"], p["pdf"]), (None, "/api/v1/vorgaenge/1/dokument"))
        self.assertIn("Vorn steht das Anschreiben", p["dokument_hinweis"])

    def test_vorher_noch_ohne_fortschritt_reihenfolge_anschreiben_vor_protokoll(self):
        # vor dem Abschluss zeigt die alte Liste "dokumente" die Rollen A/B; die Reiter folgen immer der Reihenfolge des Mitglieds (B vor A)
        o = lauf(seite=SEITE, api=vorg("bereit", dokumente=[{"id": 1, "rolle": "A"}, {"id": 2, "rolle": "B"}]), ops=[])
        self.assertEqual([r["text"] for r in v(o)["pruefen"]["reiter"]], ["Anschreiben", "Protokoll"])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestBeraterStandJeDokument(unittest.TestCase):
    def test_teilweise_unterschrieben_mit_hinweis_und_dokumentkarte(self):
        o = lauf(seite=SEITE, api=vorg(dokumente_fortschritt=fortschritt((2, True), (0, False))), ops=[])
        d = v(o)
        self.assertEqual(d["titel"], "Mustermann_Steuer2025 · Teilweise unterschrieben")
        self.assertEqual(d["status"]["hinweis"], {"klasse": "hinweis-blau", "text": "Anschreiben ist unterschrieben, Protokoll fehlt noch. Das Mitglied kann mit demselben Link weitermachen.", "aktion": None})
        self.assertEqual(d["status"]["dokumente"], [{"titel": "Anschreiben", "stand": "✓ unterschrieben (2 von 2 Unterschriften)"}, {"titel": "Protokoll", "stand": "wartet auf Unterschrift (0 von 2 Unterschriften)"}])

    def test_eine_von_zwei_unterschriften_im_dokument(self):
        o = lauf(seite=SEITE, api=vorg(dokumente_fortschritt=fortschritt((2, True), (1, False))), ops=[])
        self.assertEqual(v(o)["status"]["dokumente"][1], {"titel": "Protokoll", "stand": "wartet auf weitere Unterschrift (1 von 2 Unterschriften)"})

    def test_noch_nichts_unterschrieben_kein_hinweis_aber_karte(self):
        d = v(lauf(seite=SEITE, api=vorg(dokumente_fortschritt=fortschritt((0, False), (0, False))), ops=[]))
        self.assertEqual(d["titel"], "Mustermann_Steuer2025 · Wartet auf Unterschrift")
        self.assertIsNone(d["status"]["hinweis"])
        self.assertEqual([x["stand"][:20] for x in d["status"]["dokumente"]], ["wartet auf Unterschr"] * 2)

    def test_abgelaufener_link_hat_vorrang_vor_dem_teilhinweis(self):
        d = v(lauf(seite=SEITE, api=vorg(link_status="abgelaufen", dokumente_fortschritt=fortschritt((2, True), (0, False))), ops=[]))
        self.assertIn("Der Link ist abgelaufen.", d["status"]["hinweis"]["text"])
        self.assertEqual(d["status"]["hinweis"]["klasse"], "hinweis-gelb")

    def test_ein_dokument_zeigt_keine_dokumentkarte(self):
        self.assertIsNone(v(lauf(seite=SEITE, api=vorg(), ops=[]))["status"]["dokumente"])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestBeraterAbholenJeDokument(unittest.TestCase):
    FERTIG = dict(status="abgeschlossen", unterschrieben_am="2026-11-15T15:48:00Z", aufbewahrung_bis="2027-11-15T12:00:00Z", personen=2)

    def api(self, **mehr):
        return dict(vorg(**dict(self.FERTIG, dokumente_fortschritt=fortschritt((2, True), (2, True)))), **mehr)

    def test_je_dokument_ein_block_mit_dokument_und_nachweis(self):
        a = v(lauf(seite=SEITE, api=self.api(), ops=[]))["abholen"]
        self.assertFalse(a["einzeln_sichtbar"])
        self.assertEqual(a["bloecke"], [{"rolle": "B", "titel": "Anschreiben", "knoepfe": ["Anschreiben herunterladen", "Nachweis zum Anschreiben herunterladen"]},
                                        {"rolle": "A", "titel": "Protokoll", "knoepfe": ["Protokoll herunterladen", "Nachweis zum Protokoll herunterladen"]}])
        self.assertEqual(a["hinweis"], "Beide Personen haben unterschrieben (15.11.2026, 16:48). Alle Dokumente sind fertig.")
        self.assertEqual(a["frist"], "Alle Dateien bleiben bis 15.11.2027 hier abrufbar und werden danach automatisch gelöscht.")

    def test_jeder_download_nennt_sein_dokument(self):
        ops = [{"op": "klick_text", "sel": ".abholen-block button", "text": t, "warte": 300} for t in ("Anschreiben herunterladen", "Nachweis zum Anschreiben herunterladen", "Protokoll herunterladen", "Nachweis zum Protokoll herunterladen")]
        o = lauf(seite=SEITE, api=self.api(), ops=ops)
        adressen = []
        for i in range(4):
            adressen += [r["p"] + r["q"] for r in o["schritte"][i]["zustand"]["requests"] if "/download/" in r["p"]]
        self.assertEqual(adressen, ["/api/v1/vorgaenge/1/download/dokument?dokument=B", "/api/v1/vorgaenge/1/download/nachweis?dokument=B",
                                    "/api/v1/vorgaenge/1/download/dokument?dokument=A", "/api/v1/vorgaenge/1/download/nachweis?dokument=A"])
        self.assertEqual(v(o, 3)["downloads"], ["dokument_Anschreiben_1.pdf", "nachweis_Anschreiben_1.pdf", "dokument_Protokoll_1.pdf", "nachweis_Protokoll_1.pdf"])

    def test_fehler_des_servers_steht_im_text(self):
        o = lauf(seite=SEITE, api=self.api(downloadFehler={"status": 400, "body": {"error": "dokument_waehlen", "meldung": "Bitte wählen Sie ein Dokument."}}),
                 ops=[{"op": "klick_text", "sel": ".abholen-block button", "text": "Protokoll herunterladen", "warte": 300}])
        self.assertEqual(v(o, 0)["abholen"]["fehler"], "Bitte wählen Sie ein Dokument.")

    def test_ein_dokument_hat_weiter_die_beiden_karten(self):
        a = v(lauf(seite=SEITE, api=vorg(**self.FERTIG), ops=[]))["abholen"]
        self.assertEqual((a["einzeln_sichtbar"], a["bloecke"]), (True, []))
        self.assertIn("Das Dokument ist fertig.", a["hinweis"])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestUebersichtJeDokument(unittest.TestCase):
    @staticmethod
    def eintrag(id, status, gesamt, fertig, link="aktiv"):
        return {"id": id, "status": status, "erstellt": "2026-11-17T13:05:00Z", "name": "Muster%d" % id, "personen": 2, "link_status": link, "link_laeuft_ab": None, "dokumente_gesamt": gesamt, "dokumente_fertig": fertig}

    def test_marke_zusatz_und_knoepfe(self):
        liste = [self.eintrag(5, "versendet", 2, 1), self.eintrag(4, "versendet", 2, 0), self.eintrag(3, "abgeschlossen", 2, 2), self.eintrag(2, "abgeschlossen", 1, 1),
                 self.eintrag(1, "versendet", 2, 1, "abgelaufen")]
        o = lauf(seite="/vorgaenge", api={"vorgaenge": liste}, ops=[])
        z = {x["id"]: x for x in o["start"]["berater"]["uebersicht"]["zeilen"]}
        self.assertEqual((z["5"]["pille"], z["5"]["pille_klasse"], z["5"]["zusatz"], z["5"]["haupt"]["text"]), ("Teilweise unterschrieben", "p-mitglied", "1 von 2 Dokumenten unterschrieben", "Ansehen"))
        self.assertEqual(z["4"]["pille"], "Wartet auf Unterschrift")                                       # noch kein Dokument fertig
        self.assertEqual((z["3"]["haupt"]["text"], z["3"]["haupt"]["href"], z["3"]["menue"]), ("Abholen", "/pruefung?id=3", ["Löschen …"]))      # kein einzelner Download bei mehreren Dokumenten
        self.assertEqual((z["2"]["haupt"]["text"], z["2"]["menue"]), ("Dokument laden", ["Nachweis herunterladen", "Löschen …"]))                # ein Dokument wie bisher
        self.assertEqual(z["1"]["pille"], "Link abgelaufen")                                               # der abgelaufene Link hat Vorrang
        self.assertEqual(o["fehler_js"], [])


if __name__ == "__main__":
    unittest.main()
