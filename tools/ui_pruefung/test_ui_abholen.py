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


if __name__ == "__main__":
    unittest.main()
