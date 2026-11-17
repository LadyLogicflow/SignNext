"""jsdom-Prüfung der Übersicht (Konzept 17.11.2026, T3): Karten, Filter, Zeilen mit einem Hauptknopf, "⋯"-Menü, Löschen und neuer Link mit Rückfrage, Download,
leere/fehlerhafte Zustände. Die Antwortformen von GET/DELETE /vorgaenge und POST .../neuer-link entsprechen dem Backend (tests/test_v070_vorgaenge.py)."""
import unittest
from datetime import datetime, timedelta, timezone

from ui_hilfen import BEREIT, HINWEIS, lauf


def iso(**d):
    return (datetime.now(timezone.utc) + timedelta(**d)).strftime("%Y-%m-%dT%H:%M:%SZ")


def v(id, status, name="Muster%d", personen=2, link=None, ab=None):
    return {"id": id, "status": status, "erstellt": "2026-11-17T13:05:00Z", "name": (name % id if name and "%d" in name else name), "personen": personen, "link_status": link, "link_laeuft_ab": ab}


LISTE = [
    v(17, "neu", "Mustermann_Steuer2025"), v(16, "bereit", "Schmidt_Protokoll_2025", 1), v(15, "versendet", "Becker_Steuer2025", 2, "aktiv", iso(days=2, hours=12)),
    v(14, "versendet", "Yilmaz_Protokoll_2025", 1, "abgelaufen", iso(days=-1)), v(13, "versand_unklar", "Weber_Steuer2025"), v(12, "abgeschlossen", "Neumann_Steuer2025"),
    v(11, "bestaetigung_noetig", "Koch_Steuer2025"), v(10, "versendet", "Lang_Steuer2025", 2, "gesperrt", iso(days=3)), v(9, "abschluss_fehlgeschlagen", "Roth_Steuer2025"),
    v(8, "unterschrieben", "Fuchs_Steuer2025"), v(7, "versenden", "Hahn_Steuer2025"), v(6, "abschluss_laeuft", "Vogel_Steuer2025"), v(5, "neu_vom_server", None, 2),
]


def u(schritt):
    return schritt["zustand"]["berater"]["uebersicht"]


def zeile(schritt, id):
    return next(z for z in u(schritt)["zeilen"] if z["id"] == str(id))


def aufrufe(schritt, methode):
    return [(r["m"], r["p"], r["body"]) for r in schritt["zustand"]["requests"] if r["m"] == methode and r["p"].startswith("/api/v1/vorgaenge/")]


@unittest.skipUnless(BEREIT, HINWEIS)
class TestKartenFilterZeilen(unittest.TestCase):
    def setUp(self):
        self.o = lauf(seite="/vorgaenge", api={"vorgaenge": LISTE}, ops=[])
        self.start = {"zustand": self.o["start"]}

    def test_karten_und_filter_zaehlen_nach_klartext_gruppen(self):
        ue = self.o["start"]["berater"]["uebersicht"]
        self.assertEqual([(k["titel"], k["zahl"]) for k in ue["karten"]], [("Wartet auf Sie", "3"), ("Beim Mitglied", "4"), ("Fertig", "1")])
        self.assertEqual([f["text"] for f in ue["filter"]], ["Alle (13)", "Wartet auf Sie (3)", "Beim Mitglied (4)", "Fertig (1)", "Probleme (4)", "↻ Aktualisieren"])
        self.assertEqual(len(ue["zeilen"]), 13)
        self.assertEqual(self.o["fehler_js"], [])

    def test_zeilen_zeigen_klartext_und_einen_hauptknopf(self):
        ue = self.o["start"]["berater"]["uebersicht"]
        z = {x["id"]: x for x in ue["zeilen"]}
        self.assertEqual((z["17"]["name"], z["17"]["sub"], z["17"]["personen"]), ("Mustermann_Steuer2025", "Vorgang 17 · angelegt 17.11.2026, 14:05", "2 Personen"))
        self.assertEqual((z["17"]["pille"], z["17"]["haupt"]["text"], z["17"]["haupt"]["href"], z["17"]["haupt"]["primaer"]), ("Bitte prüfen", "Prüfen", "/pruefung?id=17", True))
        self.assertEqual((z["16"]["pille"], z["16"]["haupt"]["text"], z["16"]["personen"]), ("Bereit zum Senden", "Senden", "1 Person"))
        self.assertEqual((z["11"]["pille"], z["11"]["zusatz"]), ("Bitte prüfen", "Angaben weichen ab"))
        self.assertEqual((z["15"]["pille"], z["15"]["zusatz"], z["15"]["haupt"]["text"], z["15"]["haupt"]["primaer"]), ("Wartet auf Unterschrift", "noch 3 Tage gültig", "Ansehen", False))
        self.assertEqual((z["14"]["pille"], z["14"]["pille_klasse"], z["14"]["haupt"]["text"]), ("Link abgelaufen", "p-problem", "Neuen Link senden"))
        self.assertEqual((z["10"]["pille"], z["10"]["haupt"]["text"]), ("Link gesperrt", "Neuen Link senden"))
        self.assertEqual((z["13"]["pille"], z["13"]["zusatz"]), ("Versand unklar", "Bitte Postfach prüfen"))
        self.assertEqual((z["12"]["pille"], z["12"]["pille_klasse"], z["12"]["haupt"]["text"]), ("Fertig – abholen", "p-fertig", "Dokument laden"))
        self.assertEqual((z["9"]["pille"], z["9"]["zusatz"], z["9"]["haupt"]), ("Fertigstellung hakt", "Wir versuchen es erneut", None))
        for warten in ("8", "6"):
            self.assertEqual((z[warten]["pille"], z[warten]["haupt"]), ("Unterschrieben – wird fertiggestellt", None))
        self.assertEqual((z["7"]["pille"], z["7"]["haupt"]), ("Wird gerade gesendet …", None))                      # nichts zu tun, kein Knopf
        for x in ue["zeilen"]:                                                                                       # nie ein Statusname aus dem Code
            self.assertNotRegex(x["pille"], r"_|^neu$|^bereit$|versendet$")

    def test_unbekannter_status_wird_lesbar_gezeigt_und_ohne_name_steht_die_nummer(self):
        z = zeile(self.start, 5)
        self.assertEqual((z["pille"], z["pille_klasse"], z["name"]), ("Neuer Wert vom Server", "p-grau", "Vorgang 5"))
        ohne_text = lauf(seite="/vorgaenge", api={"vorgaenge": [v(2, "ganz_neu_und_komisch", None)], "statusTexte": {}}, ops=[])["start"]["berater"]["uebersicht"]["zeilen"][0]
        self.assertEqual(ohne_text["pille"], "ganz neu und komisch")                                                  # nichts geraten, nur lesbar gemacht

    def test_name_wird_nie_als_markup_eingesetzt(self):
        o = lauf(seite="/vorgaenge", api={"vorgaenge": [v(3, "bereit", "<b>fett</b><img src=x onerror=alert(1)>")]}, ops=[])
        self.assertEqual(o["start"]["berater"]["uebersicht"]["zeilen"][0]["name"], "<b>fett</b><img src=x onerror=alert(1)>")
        self.assertEqual(o["fehler_js"], [])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestFilterBedienung(unittest.TestCase):
    def test_karte_und_filter_schalten_die_ansicht_um(self):
        o = lauf(seite="/vorgaenge", api={"vorgaenge": LISTE}, ops=[
            {"op": "klick_text", "sel": "#karten .karte-zahl", "text": "Wartet auf Sie3Prüfen und senden", "warte": 100},
            {"op": "klick_text", "sel": "#karten .karte-zahl", "text": "Wartet auf Sie3Prüfen und senden", "warte": 100},
            {"op": "klick_text", "sel": "#filter .filter-knopf", "text": "Probleme (4)", "warte": 100}])
        eins, zwei, drei = (u(s) for s in o["schritte"])
        self.assertEqual([z["id"] for z in eins["zeilen"]], ["17", "16", "11"])
        self.assertEqual(eins["karten"][0]["gedrueckt"], "true")
        self.assertEqual([f["gedrueckt"] for f in eins["filter"][:2]], ["false", "true"])
        self.assertEqual(len(zwei["zeilen"]), 13)                                                                    # zweiter Klick auf die Karte: wieder alle
        self.assertEqual(sorted(z["id"] for z in drei["zeilen"]), ["10", "13", "14", "9"])
        self.assertEqual(drei["karten"][0]["gedrueckt"], "false")

    def test_filter_ohne_treffer_sagt_es(self):
        o = lauf(seite="/vorgaenge", api={"vorgaenge": [v(1, "bereit")]}, ops=[{"op": "klick_text", "sel": "#filter .filter-knopf", "text": "Fertig (0)", "warte": 100}])
        self.assertEqual(u(o["schritte"][0])["leer_in_liste"], "Keine Vorgänge in dieser Ansicht.")


@unittest.skipUnless(BEREIT, HINWEIS)
class TestZustaende(unittest.TestCase):
    def test_leere_liste_erklaert_die_vier_schritte(self):
        o = lauf(seite="/vorgaenge", api={"vorgaenge": []}, ops=[])
        ue = o["start"]["berater"]["uebersicht"]
        self.assertTrue(ue["leer_hinweis"])
        self.assertFalse(ue["listen_karte"])
        self.assertEqual([k["zahl"] for k in ue["karten"]], ["0", "0", "0"])

    def test_ladefehler_steht_im_text_ohne_alert(self):
        for kaputt in ({"status": 500}, {"status": 502, "body": {"x": 1}}, {"status": 500, "body": {"error": "intern", "meldung": "Die Datenbank ist gerade nicht erreichbar."}}):
            o = lauf(seite="/vorgaenge", api={"listeFehler": kaputt}, ops=[])
            ue = o["start"]["berater"]["uebersicht"]
            self.assertTrue(ue["meldung"]["fehler"])
            self.assertEqual(ue["meldung"]["rolle"], "alert")
            self.assertTrue(ue["meldung"]["text"].startswith("Die Vorgänge konnten nicht geladen werden. "), ue["meldung"]["text"])
            self.assertNotRegex(ue["meldung"]["text"], r"Fehler\b|\d{3}|<")
            self.assertFalse(ue["leer_hinweis"])                                                                      # kein falsches "Noch kein Vorgang"
            self.assertEqual(o["start"]["alerts"], [])
        self.assertIn("Die Datenbank ist gerade nicht erreichbar.", lauf(seite="/vorgaenge", api={"listeFehler": kaputt}, ops=[])["start"]["berater"]["uebersicht"]["meldung"]["text"])


@unittest.skipUnless(BEREIT, HINWEIS)
class TestLoeschenUndNeuerLink(unittest.TestCase):
    MENUE = '.zeile[data-id="%d"] .mehr-eintrag'

    def lauf_liste(self, ops, **api):
        return lauf(seite="/vorgaenge", api=dict({"vorgaenge": LISTE}, **api), ops=ops)

    def test_menue_je_nach_status(self):
        z = {x["id"]: x["menue"] for x in lauf(seite="/vorgaenge", api={"vorgaenge": LISTE}, ops=[])["start"]["berater"]["uebersicht"]["zeilen"]}
        self.assertEqual(z["16"], ["Löschen …"])
        self.assertEqual(z["15"], ["Neuen Link senden …", "Löschen …"])
        self.assertEqual(z["13"], ["Neuen Link senden …", "Löschen …"])
        self.assertEqual(z["14"], ["Löschen …"])                                                                      # abgelaufen: der Neuer-Link-Knopf ist schon der Hauptknopf
        self.assertEqual(z["12"], ["Nachweis herunterladen", "Löschen …"])
        self.assertEqual((z["7"], z["6"]), ([], []))                                                                 # wird gerade bearbeitet: nichts löschen

    def test_loeschen_erst_nach_rueckfrage_und_abbrechen_aendert_nichts(self):
        o = self.lauf_liste([{"op": "klick_text", "sel": self.MENUE % 16, "text": "Löschen …", "warte": 100},
                             {"op": "klick_text", "sel": '.zeile[data-id="16"] .rueckfrage button', "text": "Abbrechen", "warte": 100},
                             {"op": "klick_text", "sel": self.MENUE % 16, "text": "Löschen …", "warte": 100},
                             {"op": "klick_text", "sel": '.zeile[data-id="16"] .rueckfrage button', "text": "Ja, löschen", "warte": 400}])
        frage, zurueck, wieder, weg = o["schritte"]
        self.assertEqual(zeile(frage, 16)["rueckfrage"], "„Schmidt_Protokoll_2025“ endgültig löschen? Die Dokumente und der Vorgang werden entfernt. Das kann nicht rückgängig gemacht werden.")
        self.assertEqual(zeile(frage, 16)["rueckfrage_knoepfe"], ["Ja, löschen", "Abbrechen"])
        self.assertEqual(aufrufe(frage, "DELETE"), [])                                                               # nichts passiert ohne "Ja"
        self.assertIsNone(zeile(zurueck, 16)["rueckfrage"])
        self.assertEqual(zeile(zurueck, 16)["haupt"]["text"], "Senden")
        self.assertEqual(aufrufe(zurueck, "DELETE"), [])
        self.assertEqual(aufrufe(weg, "DELETE"), [("DELETE", "/api/v1/vorgaenge/16", {"bestaetigt": True})])
        self.assertIs(aufrufe(weg, "DELETE")[0][2]["bestaetigt"], True)                                              # wirklich true, nicht 1 (das Backend verlangt ausdrücklich True)
        ue = u(weg)
        self.assertNotIn("16", [z["id"] for z in ue["zeilen"]])
        self.assertEqual(ue["meldung"]["text"], "„Schmidt_Protokoll_2025“ wurde gelöscht.")
        self.assertEqual(ue["karten"][0]["zahl"], "2")                                                               # Zähler aktualisiert
        self.assertEqual(o["fehler_js"], [])

    def test_loeschen_scheitert_der_vorgang_bleibt_und_der_grund_steht_in_der_zeile(self):
        meldung = "Der Vorgang wird gerade bearbeitet und kann jetzt nicht gelöscht werden. Bitte in einigen Minuten erneut versuchen."
        o = self.lauf_liste([{"op": "klick_text", "sel": self.MENUE % 17, "text": "Löschen …", "warte": 100},
                             {"op": "klick_text", "sel": '.zeile[data-id="17"] .rueckfrage button', "text": "Ja, löschen", "warte": 400}],
                            loeschen={"status": 409, "body": {"error": "abschluss_laeuft", "meldung": meldung}})
        z = zeile(o["schritte"][1], 17)
        self.assertEqual(z["fehler"], meldung)
        self.assertIsNone(z["rueckfrage"])
        self.assertEqual(z["haupt"]["text"], "Prüfen")                                                               # Zeile ist wieder bedienbar
        o2 = self.lauf_liste([{"op": "klick_text", "sel": self.MENUE % 17, "text": "Löschen …", "warte": 100},
                              {"op": "klick_text", "sel": '.zeile[data-id="17"] .rueckfrage button', "text": "Ja, löschen", "warte": 400}], loeschen={"status": 404, "body": {"error": "nicht_gefunden"}})
        self.assertEqual(zeile(o2["schritte"][1], 17)["fehler"], "Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.")

    def test_neuer_link_mit_rueckfrage_dann_ist_der_link_wieder_aktiv(self):
        o = self.lauf_liste([{"op": "klick_text", "sel": '.zeile[data-id="14"] .zeile-aktion > button', "text": "Neuen Link senden", "warte": 100},
                             {"op": "klick_text", "sel": '.zeile[data-id="14"] .rueckfrage button', "text": "Ja, neuen Link senden", "warte": 600}])
        frage, fertig = o["schritte"]
        self.assertEqual(zeile(frage, 14)["rueckfrage"], "Für „Yilmaz_Protokoll_2025“ einen neuen Link per E-Mail senden? Der bisherige Link funktioniert dann nicht mehr.")
        self.assertEqual([a for a in aufrufe(frage, "POST") if a[1].endswith("neuer-link")], [])
        self.assertEqual([a for a in aufrufe(fertig, "POST") if a[1].endswith("neuer-link")], [("POST", "/api/v1/vorgaenge/14/neuer-link", {"bestaetigt": True})])
        self.assertIs([a for a in aufrufe(fertig, "POST") if a[1].endswith("neuer-link")][0][2]["bestaetigt"], True)
        self.assertEqual(u(fertig)["meldung"]["text"], "Ein neuer Link für „Yilmaz_Protokoll_2025“ wurde per E-Mail gesendet.")
        self.assertEqual(zeile(fertig, 14)["pille"], "Wartet auf Unterschrift")                                       # Liste wurde neu geladen
        self.assertTrue(u(fertig)["meldung"] and not u(fertig)["meldung"]["fehler"])

    def test_neuer_link_scheitert_text_in_der_zeile(self):
        o = self.lauf_liste([{"op": "klick_text", "sel": '.zeile[data-id="14"] .zeile-aktion > button', "text": "Neuen Link senden", "warte": 100},
                             {"op": "klick_text", "sel": '.zeile[data-id="14"] .rueckfrage button', "text": "Ja, neuen Link senden", "warte": 400}],
                            neuerLink={"status": 409, "body": {"error": "mail_zugang_fehlt", "meldung": "Für Ihre Beratungsstelle ist noch kein Mail-Zugang hinterlegt."}})
        z = zeile(o["schritte"][1], 14)
        self.assertEqual(z["fehler"], "Für Ihre Beratungsstelle ist noch kein Mail-Zugang hinterlegt.")
        self.assertEqual(z["pille"], "Link abgelaufen")

    def test_download_von_dokument_und_nachweis(self):
        o = self.lauf_liste([{"op": "klick_text", "sel": '.zeile[data-id="12"] .zeile-aktion > button', "text": "Dokument laden", "warte": 400},
                             {"op": "klick_text", "sel": self.MENUE % 12, "text": "Nachweis herunterladen", "warte": 400}])
        self.assertEqual(u(o["schritte"][1])["downloads"], ["dokument_12.pdf", "nachweis_12.pdf"])
        kaputt = self.lauf_liste([{"op": "klick_text", "sel": '.zeile[data-id="12"] .zeile-aktion > button', "text": "Dokument laden", "warte": 400}], downloadFehler={"status": 404, "body": {"error": "nicht_gefunden"}})
        self.assertEqual(zeile(kaputt["schritte"][0], 12)["fehler"], "Der Download hat nicht geklappt. Bitte versuchen Sie es noch einmal.")
        self.assertEqual(kaputt["schritte"][0]["zustand"]["alerts"], [])


if __name__ == "__main__":
    unittest.main()
