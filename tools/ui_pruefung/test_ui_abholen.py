"""jsdom-Prüfung Ampel + "abgeholt am" (catrin 18.11.2026, Teil 19).
Die Schnittstelle spielt der Treiber (drive_sn.js) nach Sortys Beschreibung nach: ampel (rot/gelb/gruen, null = noch nicht versendet) in GET /vorgaenge und /vorgaenge/<id>,
dokumente_fortschritt[].abgeholt_am (Dokument-PDF) und .nachweis_abgeholt_am, GET /vorgaenge: dokumente_abgeholt. Grün zählt nur über die Dokument-PDFs."""
import unittest

from ui_hilfen import BEREIT, HINWEIS, lauf
from test_ui_dokumente import SEITE, fortschritt, v, vorg

FERTIG = dict(status="abgeschlossen", unterschrieben_am="2026-11-15T15:48:00Z", aufbewahrung_bis="2027-11-15T12:00:00Z", personen=2)
ZWEI = [{"rolle": "B", "titel": "Anschreiben", "seiten": 2}, {"rolle": "A", "titel": "Protokoll", "seiten": 3}]


def mit_abholung(b=(None, None), a=(None, None)):
    """dokumente_fortschritt mit abgeholt_am (Dokument) und nachweis_abgeholt_am je Dokument."""
    f = fortschritt((2, True), (2, True))
    for eintrag, (dok, nach) in zip(f, (b, a)):
        eintrag.update({"abgeholt_am": dok, "dokument_abgeholt_am": dok, "nachweis_abgeholt_am": nach})
    return f


GELB = mit_abholung(b=("2026-11-18T09:15:00Z", None))
ROT_STAND = ("rot", "✕", "Nicht vollständig unterschrieben")


def download_adressen(o):
    """Alle Download-Adressen über alle Schritte (die Abrufe laufen im ersten Schritt, die Speicherung danach)."""
    return [r["p"] + r["q"] for st in o["schritte"] for r in st["zustand"]["requests"] if "/download/" in r["p"]]


def ampel(a):
    return None if a is None else (a["wert"], a["symbol"], a["text"])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestAmpelStand(unittest.TestCase):
    def test_rot_mit_symbol_und_text(self):
        d = v(lauf(seite=SEITE, api=vorg(ampel="rot", dokumente_fortschritt=fortschritt((2, True), (0, False))), ops=[]))
        self.assertEqual(ampel(d["status"]["ampel"]), ROT_STAND)
        self.assertEqual(d["status"]["ampel"]["klasse"], "ampel ampel-rot")
        self.assertEqual(d["status"]["ampel"]["symbol_versteckt"], "true")                  # das Symbol wird nicht vorgelesen, der Text trägt die Aussage

    def test_ohne_ampel_keine_marke(self):
        for w in (None, "blau", ""):                                                        # null = noch nicht versendet; unbekannte Werte erfinden nichts
            self.assertIsNone(v(lauf(seite=SEITE, api=vorg(ampel=w), ops=[]))["status"]["ampel"], w)

    def test_gelb_und_gruen_auf_der_abholen_seite(self):
        o = lauf(seite=SEITE, api=vorg(ampel="gelb", dokumente_fortschritt=GELB, **FERTIG), ops=[])
        self.assertEqual(ampel(v(o)["abholen"]["ampel"]), ("gelb", "◔", "Unterschrieben, noch nicht abgeholt"))
        o = lauf(seite=SEITE, api=vorg(ampel="gruen", dokumente_fortschritt=mit_abholung(("2026-11-18T09:15:00Z", None), ("2026-11-18T09:20:00Z", None)), **FERTIG), ops=[])
        self.assertEqual(ampel(v(o)["abholen"]["ampel"]), ("gruen", "✓", "Alles abgeholt"))


@unittest.skipUnless(BEREIT, HINWEIS)
class TestAbgeholtAm(unittest.TestCase):
    def test_je_dokument_dokument_und_nachweis_mit_zeit(self):
        a = v(lauf(seite=SEITE, api=vorg(ampel="gelb", dokumente_fortschritt=mit_abholung(("2026-11-18T09:15:00Z", "2026-11-18T09:16:00Z"), (None, None)), **FERTIG), ops=[]))["abholen"]
        self.assertEqual(a["abgeholt"], [["B", "Dokument: abgeholt am 18.11.2026, 10:15 · Nachweis: abgeholt am 18.11.2026, 10:16"],
                                         ["A", "Dokument: noch nicht abgeholt · Nachweis: noch nicht abgeholt"]])

    def test_nachweis_allein_macht_das_dokument_nicht_abgeholt(self):
        a = v(lauf(seite=SEITE, api=vorg(ampel="gelb", dokumente_fortschritt=mit_abholung((None, "2026-11-18T09:16:00Z"), (None, None)), **FERTIG), ops=[]))["abholen"]
        self.assertEqual(a["abgeholt"][0], ["B", "Dokument: noch nicht abgeholt · Nachweis: abgeholt am 18.11.2026, 10:16"])

    def test_ein_dokument_zeigt_es_unter_den_beiden_karten(self):
        f = [{"rolle": "B", "titel": "Anschreiben", "erforderlich": [1, 2], "unterschrieben": [1, 2], "fertig": True, "abgeholt_am": "2026-11-18T09:15:00Z", "nachweis_abgeholt_am": None}]
        a = v(lauf(seite=SEITE, api=vorg(ampel="gelb", dokumente_fortschritt=f, **FERTIG), ops=[]))["abholen"]
        self.assertEqual(a["abgeholt_einzeln"], ["Dokument: abgeholt am 18.11.2026, 10:15", "Nachweis: noch nicht abgeholt"])
        self.assertEqual(a["abgeholt"], [])
        b = v(lauf(seite=SEITE, api=vorg(**FERTIG), ops=[]))["abholen"]                                  # ältere Antwort ohne Felder: nichts erfinden
        self.assertEqual(b["abgeholt_einzeln"], ["Dokument: noch nicht abgeholt", "Nachweis: noch nicht abgeholt"])
        self.assertIsNone(b["ampel"])

    def test_nach_dem_download_holt_die_seite_den_neuen_stand(self):
        # der Treiber spielt das Backend nach: erster Abruf je Datei setzt die Zeit; Nachweise zählen nicht für grün
        api = dict(vorg(ampel="gelb", **FERTIG), dokumente=ZWEI, abholSicht=True)
        klick = lambda t: {"op": "klick_text", "sel": ".abholen-block button", "text": t, "warte": 400}
        o = lauf(seite=SEITE, api=api, ops=[klick("Nachweis zum Anschreiben herunterladen"), klick("Anschreiben herunterladen"), klick("Protokoll herunterladen")])
        s0, s1, s2 = (v(o, i) for i in range(3))
        self.assertEqual(ampel(s0["abholen"]["ampel"])[0], "gelb")                                       # nur ein Nachweis geholt
        self.assertEqual(s0["abholen"]["abgeholt"][0][1], "Dokument: noch nicht abgeholt · Nachweis: abgeholt am 18.11.2026, 10:11")
        self.assertEqual(ampel(s1["abholen"]["ampel"])[0], "gelb")                                       # ein Dokument von zwei
        self.assertEqual(ampel(s2["abholen"]["ampel"])[0], "gruen")                                      # beide Dokument-PDFs
        self.assertEqual(o["fehler_js"], [])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestAmpelListe(unittest.TestCase):
    @staticmethod
    def eintrag(id, status, ampel, gesamt=2, fertig=2, abgeholt=0):
        return {"id": id, "status": status, "erstellt": "2026-11-17T13:05:00Z", "name": "Muster%d" % id, "personen": 2, "link_status": None, "link_laeuft_ab": None,
                "dokumente_gesamt": gesamt, "dokumente_fertig": fertig, "dokumente_abgeholt": abgeholt, "ampel": ampel}

    def test_ampel_je_zeile_mit_text_und_symbol(self):
        liste = [self.eintrag(4, "versendet", "rot", fertig=1), self.eintrag(3, "abgeschlossen", "gelb", abgeholt=1), self.eintrag(2, "abgeschlossen", "gruen", abgeholt=2), self.eintrag(1, "bereit", None, fertig=0)]
        o = lauf(seite="/vorgaenge", api={"vorgaenge": liste}, ops=[])
        z = {x["id"]: x for x in o["start"]["berater"]["uebersicht"]["zeilen"]}
        self.assertEqual(z["4"]["ampel"], ["rot", "✕", "Nicht vollständig unterschrieben"])
        self.assertEqual(z["3"]["ampel"], ["gelb", "◔", "Unterschrieben, noch nicht abgeholt"])
        self.assertEqual(z["2"]["ampel"], ["gruen", "✓", "Alles abgeholt"])
        self.assertEqual((z["3"]["ampel_zusatz"], z["2"]["ampel_zusatz"], z["4"]["ampel_zusatz"]), ("1 von 2 Dokumenten abgeholt", "2 von 2 Dokumenten abgeholt", None))
        self.assertIsNone(z["1"]["ampel"])                                                              # null = nur die Status-Pille
        self.assertEqual(z["1"]["pille"], "Bereit zum Senden")
        self.assertEqual(z["4"]["pille"], "Teilweise unterschrieben")                                   # die feine Status-Pille bleibt
        self.assertEqual(o["fehler_js"], [])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestAlleHerunterladen(unittest.TestCase):
    ALLES = {"op": "klick_id", "id": "btn-alles-dl", "warte": 100}
    WARTEN = {"op": "warten", "warte": 3500}

    def api(self, **mehr):
        return dict(vorg(ampel="gelb", **FERTIG), dokumente=ZWEI, abholSicht=True, **mehr)

    def test_alle_vier_dateien_nacheinander_ohne_zip(self):
        o = lauf(seite=SEITE, api=self.api(), ops=[self.ALLES, self.WARTEN])
        self.assertEqual(v(o, 1)["abholen"]["abrufe"], ["dokument:B", "nachweis:B", "dokument:A", "nachweis:A"])         # je Datei ein Abruf, Reihenfolge Anschreiben, Protokoll
        self.assertEqual(v(o, 1)["downloads"], ["dokument_Anschreiben_1.pdf", "nachweis_Anschreiben_1.pdf", "dokument_Protokoll_1.pdf", "nachweis_Protokoll_1.pdf"])
        adressen = download_adressen(o)
        self.assertEqual(adressen, ["/api/v1/vorgaenge/1/download/dokument?dokument=B", "/api/v1/vorgaenge/1/download/nachweis?dokument=B",
                                    "/api/v1/vorgaenge/1/download/dokument?dokument=A", "/api/v1/vorgaenge/1/download/nachweis?dokument=A"])
        self.assertFalse([a for a in adressen if "zip" in a.lower()])
        a = v(o, 1)["abholen"]
        self.assertEqual(a["alles"]["gesperrt"], False)
        self.assertTrue(a["alles"]["fortschritt"].startswith("4 von 4 Dateien wurden an Ihren Browser übergeben."))
        self.assertIn("laden Sie sie unten einzeln herunter", a["alles"]["fortschritt"])                                     # ehrlicher Hinweis: der Browser kann nachfragen
        self.assertEqual((a["alles"]["live"], a["alles"]["rolle"]), ("polite", "status"))
        self.assertEqual(ampel(a["ampel"])[0], "gruen")                                                                      # beide Dokument-PDFs abgeholt, Stand nachgeladen
        self.assertEqual(o["fehler_js"], [])

    def test_fortschritt_und_abstand_zwischen_den_downloads(self):
        o = lauf(seite=SEITE, api=self.api(), ops=[self.ALLES, {"op": "warten", "warte": 700}, self.WARTEN])
        mitten, ende = v(o, 1)["abholen"], v(o, 2)["abholen"]
        self.assertTrue(mitten["alles"]["gesperrt"])                                                                          # während des Laufs kein zweiter Start
        self.assertRegex(mitten["alles"]["fortschritt"], r"^Datei \d von 4: .+ wird gespeichert …$")
        self.assertLess(len(v(o, 1)["downloads"]), 4)                                                                         # 400 ms Abstand: nach 0,8 s noch nicht alle
        self.assertEqual(len(v(o, 2)["downloads"]), 4)
        self.assertFalse(ende["alles"]["gesperrt"])

    def test_eine_datei_scheitert_der_rest_laeuft_weiter(self):
        fehler = {"status": 404, "body": {"error": "nicht_gefunden", "meldung": "Die Datei ist nicht mehr vorhanden."}}
        o = lauf(seite=SEITE, api=self.api(downloadFehlerDatei=dict(fehler, art="nachweis", rolle="A")), ops=[self.ALLES, self.WARTEN])
        a = v(o, 1)["abholen"]
        self.assertEqual(v(o, 1)["downloads"], ["dokument_Anschreiben_1.pdf", "nachweis_Anschreiben_1.pdf", "dokument_Protokoll_1.pdf"])
        self.assertIn("Nachweis zum Protokoll: Die Datei ist nicht mehr vorhanden.", a["fehler"])
        self.assertIn("unten einzeln herunter", a["fehler"])
        self.assertTrue(a["alles"]["fortschritt"].startswith("3 von 4 Dateien"))
        self.assertEqual(ampel(a["ampel"])[0], "gruen")                                                                      # beide Dokument-PDFs sind da, der fehlende Nachweis ändert das nicht

    def test_fehlt_ein_dokument_bleibt_die_ampel_gelb(self):
        o = lauf(seite=SEITE, api=self.api(downloadFehlerDatei={"status": 500, "art": "dokument", "rolle": "A", "body": {"meldung": "Fehler"}}), ops=[self.ALLES, self.WARTEN])
        self.assertEqual(ampel(v(o, 1)["abholen"]["ampel"])[0], "gelb")

    def test_alles_scheitert_es_wird_nichts_vorgetaeuscht(self):
        o = lauf(seite=SEITE, api=self.api(downloadFehler={"status": 500, "body": {"meldung": "Der Server ist gerade nicht erreichbar."}}), ops=[self.ALLES, self.WARTEN])
        a = v(o, 1)["abholen"]
        self.assertEqual(v(o, 1)["downloads"], [])
        self.assertEqual(a["alles"]["fortschritt"], "Es wurde keine Datei heruntergeladen.")
        self.assertIn("Der Server ist gerade nicht erreichbar.", a["fehler"])

    def test_ein_dokument_zwei_dateien_und_einzelknoepfe_bleiben(self):
        o = lauf(seite=SEITE, api=dict(vorg(ampel="gelb", **FERTIG), abholSicht=True), ops=[self.ALLES, self.WARTEN])
        self.assertEqual(v(o, 1)["downloads"], ["dokument_1.pdf", "nachweis_1.pdf"])
        adressen = download_adressen(o)
        self.assertEqual(adressen, ["/api/v1/vorgaenge/1/download/dokument", "/api/v1/vorgaenge/1/download/nachweis"])
        self.assertTrue(v(o, 1)["abholen"]["einzeln_sichtbar"])
        mehr = lauf(seite=SEITE, api=self.api(), ops=[])
        self.assertEqual([b["knoepfe"] for b in v(mehr)["abholen"]["bloecke"]], [["Anschreiben herunterladen", "Nachweis zum Anschreiben herunterladen"], ["Protokoll herunterladen", "Nachweis zum Protokoll herunterladen"]])
        self.assertEqual(v(mehr)["abholen"]["alles"]["text"], "Alles herunterladen")


@unittest.skipUnless(BEREIT, HINWEIS)
class TestDateinamenVomServer(unittest.TestCase):
    """catrin 18.11.2026 (14:14): die Namen der Downloads kommen vom Server (dateiname, nachweis_dateiname je Dokument); fehlen sie, bleiben die bisherigen Namen."""
    NAMEN = [{"rolle": "B", "titel": "Anschreiben", "seiten": 2, "dateiname": "4711_Muster_VE.pdf", "nachweis_dateiname": "4711_Muster_VE_NWU.pdf"},
             {"rolle": "A", "titel": "Protokoll", "seiten": 3, "dateiname": "4711_Muster_FZ.pdf", "nachweis_dateiname": "4711_Muster_FZ_NWU.pdf"}]
    KLICK = lambda self, t: {"op": "klick_text", "sel": ".abholen-block button", "text": t, "warte": 400}

    def api(self, dokumente):
        return dict(vorg(ampel="gelb", **FERTIG), dokumente=dokumente, abholSicht=True)

    def test_einzelknoepfe_benutzen_die_namen_des_servers(self):
        ops = [self.KLICK(t) for t in ("Anschreiben herunterladen", "Nachweis zum Anschreiben herunterladen", "Protokoll herunterladen", "Nachweis zum Protokoll herunterladen")]
        o = lauf(seite=SEITE, api=self.api(self.NAMEN), ops=ops)
        self.assertEqual(v(o, 3)["downloads"], ["4711_Muster_VE.pdf", "4711_Muster_VE_NWU.pdf", "4711_Muster_FZ.pdf", "4711_Muster_FZ_NWU.pdf"])

    def test_alles_herunterladen_benutzt_die_namen_des_servers(self):
        o = lauf(seite=SEITE, api=self.api(self.NAMEN), ops=[{"op": "klick_id", "id": "btn-alles-dl", "warte": 100}, {"op": "warten", "warte": 3500}])
        self.assertEqual(v(o, 1)["downloads"], ["4711_Muster_VE.pdf", "4711_Muster_VE_NWU.pdf", "4711_Muster_FZ.pdf", "4711_Muster_FZ_NWU.pdf"])
        self.assertEqual(o["fehler_js"], [])

    def test_fehlende_felder_fallen_auf_die_bisherigen_namen_zurueck(self):
        teil = [dict(self.NAMEN[0], nachweis_dateiname=None), {"rolle": "A", "titel": "Protokoll", "seiten": 3, "dateiname": "  "}]          # leer, None, nur Leerzeichen
        ops = [self.KLICK(t) for t in ("Anschreiben herunterladen", "Nachweis zum Anschreiben herunterladen", "Protokoll herunterladen", "Nachweis zum Protokoll herunterladen")]
        o = lauf(seite=SEITE, api=self.api(teil), ops=ops)
        self.assertEqual(v(o, 3)["downloads"], ["4711_Muster_VE.pdf", "nachweis_Anschreiben_1.pdf", "dokument_Protokoll_1.pdf", "nachweis_Protokoll_1.pdf"])

    def test_ein_dokument_benutzt_den_namen_aus_dokumente_fortschritt_sonst_den_obersten(self):
        eins = [{"rolle": "B", "titel": "Anschreiben", "seiten": 2, "dateiname": "4711_Muster_FZ.pdf", "nachweis_dateiname": "4711_Muster_FZ_NWU.pdf"}]
        o = lauf(seite=SEITE, api=self.api(eins), ops=[{"op": "klick_id", "id": "btn-dl-dokument", "warte": 300}, {"op": "klick_id", "id": "btn-dl-nachweis", "warte": 300}])
        self.assertEqual(v(o, 1)["downloads"], ["4711_Muster_FZ.pdf", "4711_Muster_FZ_NWU.pdf"])
        ohne = lauf(seite=SEITE, api=vorg(dateiname="4711_Muster_VE.pdf", nachweis_dateiname="4711_Muster_VE_NWU.pdf", **FERTIG), ops=[{"op": "klick_id", "id": "btn-dl-dokument", "warte": 300}, {"op": "klick_id", "id": "btn-dl-nachweis", "warte": 300}])
        self.assertEqual(v(ohne, 1)["downloads"], ["4711_Muster_VE.pdf", "4711_Muster_VE_NWU.pdf"])
        alt = lauf(seite=SEITE, api=dict(vorg(**FERTIG)), ops=[{"op": "klick_id", "id": "btn-dl-dokument", "warte": 300}])
        self.assertEqual(v(alt, 0)["downloads"], ["dokument_1.pdf"])


if __name__ == "__main__":
    unittest.main()
