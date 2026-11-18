"""Dokumente eines Vorgangs (v0.7.0, Einzel-Signatur, catrin 18.11.2026): Jedes Dokument (Anschreiben 'B', Protokoll 'A') wird einzeln gelesen, unterschrieben und abgeschlossen.

Reihenfolge: Anschreiben vor Protokoll; je Dokument unterschreiben nur Personen mit Unterschriftsstelle in diesem Dokument, Person 1 vor Person 2.
Vorgänge aus früheren Fassungen haben ein zusammengeführtes Dokument 'M' (Rolle 'M', Ergebnis 'S'/'N'); sie laufen als ein einziges Dokument weiter.
Rollen der Ergebnisse: 'S_<Rolle>' = unterschriebenes Dokument, 'N_<Rolle>' = Nachweis (bei 'M': 'S' und 'N')."""
import abgleich
import db

TITEL = {"B": "Vollständigkeitserklärung", "A": "Freizeichnungsprotokoll", "M": "Dokument"}      # Anzeige (catrin 18.11.2026 14:47)
NACHWEIS_TITEL = {"B": "Anschreiben", "A": "Protokoll"}      # Zeile "Dokument: …" im Nachweis: bleibt, bis catrin den neuen Nachweis-Wortlaut freigibt
ERSATZ_NAME = {"B": "anschreiben", "A": "protokoll"}          # Ersatz-Dateinamen (vorgang-<id>-<anschreiben|protokoll>-…): unabhängig vom Anzeige-Titel
REIHENFOLGE = ("B", "A")
KUERZEL = {"A": "FZ", "B": "VE"}             # Dateinamen (catrin 18.11.2026): A = Freizeichnungsprotokoll, B = Vollständigkeitserklärung (Anschreiben)


def rollen(mandant_ref: str, vorgang_id: int) -> list:
    """Die Dokument-Rollen des Vorgangs in Unterschriftsreihenfolge (auch nach dem Abschluss, wenn die Originale gelöscht sind)."""
    vorhanden = {z["rolle"] for z in db.dokumente_liste(mandant_ref, vorgang_id)}
    if vorhanden & {"M", "S"}:
        return ["M"]
    r = [x for x in REIHENFOLGE if x in vorhanden]
    if r:
        return r
    return [x for x in REIHENFOLGE if "S_" + x in vorhanden]


def _namensteil(nachname) -> str:
    """Nachname für den Dateinamen: Steuerzeichen und / \\ : * ? " < > | _ fallen weg (werden zu Leerzeichen), Umlaute bleiben, Leerraum zusammengezogen, höchstens 60 Zeichen."""
    import re
    if not isinstance(nachname, str):
        return ""
    t = " ".join(re.sub(r'[\x00-\x1f\x7f-\x9f/\\:*?"<>|_]+', " ", nachname).split()).strip(" .")
    return t[:60].strip(" .")


def dateinamen(vorgang_id: int, rolle: str, anzahl: int, mitgliedsnr, nachname) -> tuple:
    """(Dateiname des unterschriebenen Dokuments, Dateiname des Nachweises). ``<Mitgliedsnummer>_<Nachname>_<FZ|VE>[_NWU].pdf``; fehlt die siebenstellige Nummer oder der Nachname
    (und bei zusammengeführten Altvorgängen, Rolle 'M') gilt der bisherige Name ``vorgang-<id>[-<titel>]-<dokument|nachweis>.pdf``. ``anzahl`` = Zahl der Dokumente des Vorgangs."""
    import re
    teil = _namensteil(nachname)
    if rolle in KUERZEL and teil and isinstance(mitgliedsnr, str) and re.fullmatch(r"\d{7}", mitgliedsnr):
        basis = "%s_%s_%s" % (mitgliedsnr, teil, KUERZEL[rolle])
        return basis + ".pdf", basis + "_NWU.pdf"
    mitte = "" if rolle == "M" or anzahl == 1 else "-" + ERSATZ_NAME[rolle]
    return "vorgang-%d%s-dokument.pdf" % (vorgang_id, mitte), "vorgang-%d%s-nachweis.pdf" % (vorgang_id, mitte)


def ergebnis_rolle(art: str, rolle: str) -> str:
    """Rolle der Ergebnisdatei: art 'dokument' (unterschrieben) oder 'nachweis'."""
    if rolle == "M":
        return "S" if art == "dokument" else "N"
    return ("S_" if art == "dokument" else "N_") + rolle


def liste(mandant_ref: str, vorgang_id: int, personen: int) -> list:
    """[{rolle, titel, seiten, erforderlich}] in Reihenfolge. ``erforderlich`` = Personen mit Unterschriftsstelle (ohne Stellen: alle Personen, der Abschluss meldet dann den Fehler)."""
    zeilen = {z["rolle"]: z for z in db.dokumente_liste(mandant_ref, vorgang_id)}
    ergebnis = []
    for r in rollen(mandant_ref, vorgang_id):
        quelle = zeilen.get(r)
        if quelle is not None:
            if personen in (1, 2):
                erforderlich = sorted({int(s["person"]) for s in abgleich.aktive_stellen(quelle["stellen"], personen)}) or list(range(1, personen + 1))
            else:
                erforderlich = []                                  # Personenzahl noch nicht bekannt (Angaben fehlen)
            seiten = quelle["seiten"]
        else:                                                      # nach dem Abschluss: nur noch die Ergebnisse
            erforderlich = list(range(1, personen + 1)) if personen in (1, 2) else []
            seiten = (zeilen.get(ergebnis_rolle("dokument", r)) or {}).get("seiten", 0)
        ergebnis.append({"rolle": r, "titel": TITEL[r], "seiten": seiten, "erforderlich": erforderlich})
    return ergebnis


def mit_stand(c, mandant_ref: str, vorgang_id: int, dokumente: list) -> list:
    """Ergänzt ``unterschrieben`` (Personen) und ``fertig`` je Dokument aus den gespeicherten Unterschriften."""
    je: dict = {}
    for z in c.execute("SELECT dokument_rolle, person FROM unterschrift WHERE vorgang_id=? AND mandant_ref=? ORDER BY person", (int(vorgang_id), mandant_ref)):
        je.setdefault(z["dokument_rolle"], []).append(int(z["person"]))
    for d in dokumente:
        d["unterschrieben"] = je.get(d["rolle"], [])
        d["fertig"] = set(d["erforderlich"]) <= set(d["unterschrieben"])
    return dokumente


def abholungen(c, mandant_ref: str) -> dict:
    """{(Vorgang, Dokument-Rolle, art): Zeitpunkt der ersten Abholung} für die Beratungsstelle (art 'dokument' oder 'nachweis')."""
    return {(z["vorgang_id"], z["dokument_rolle"], z["art"]): z["erstmals_am"]
            for z in c.execute("SELECT vorgang_id, dokument_rolle, art, erstmals_am FROM abholung WHERE mandant_ref=?", (mandant_ref,))}


def mit_abholung(dokumente: list, vorgang_id: int, abgeholt: dict) -> list:
    """Ergänzt ``abgeholt_am`` (unterschriebenes Dokument) und ``nachweis_abgeholt_am`` je Dokument (None, solange nicht abgeholt)."""
    for d in dokumente:
        d["abgeholt_am"] = abgeholt.get((int(vorgang_id), d["rolle"], "dokument"))
        d["nachweis_abgeholt_am"] = abgeholt.get((int(vorgang_id), d["rolle"], "nachweis"))
    return dokumente


VOLLSTAENDIG_UNTERSCHRIEBEN = ("unterschrieben", "abschluss_laeuft", "abschluss_fehlgeschlagen", "abgeschlossen")
NOCH_NICHT_VERSENDET = ("neu", "bereit", "bestaetigung_noetig")


def ampel(status: str, dokumente: list):
    """Ampel je Vorgang: None = noch nicht versendet; 'rot' = nicht vollständig unterschrieben (auch teilweise); 'gelb' = vollständig unterschrieben, aber nicht alle
    Dokumente von der Beratungsstelle abgeholt; 'gruen' = alle Dokumente (PDF, ohne Nachweis) abgeholt. ``dokumente`` mit Abholung (mit_abholung)."""
    if status in NOCH_NICHT_VERSENDET:
        return None
    if status not in VOLLSTAENDIG_UNTERSCHRIEBEN:
        return "rot"
    return "gruen" if dokumente and all(d["abgeholt_am"] for d in dokumente) else "gelb"


def naechste(dokumente: list):
    """(Dokument, Person), die als Nächstes unterschreiben; (None, None), wenn alle fertig sind. ``dokumente`` mit Stand (mit_stand)."""
    for d in dokumente:
        if not d["fertig"]:
            return d, [p for p in d["erforderlich"] if p not in d["unterschrieben"]][0]
    return None, None


def zustimmung_fehlt(c, mandant_ref: str, vorgang_id: int, person: int) -> bool:
    """True, wenn diese Person im Vorgang noch nichts unterschrieben hat: Datenschutzinformation und Haken sind dann zu bestätigen (einmal je Person und Vorgang)."""
    return c.execute("SELECT 1 FROM unterschrift WHERE vorgang_id=? AND mandant_ref=? AND person=? LIMIT 1", (int(vorgang_id), mandant_ref, int(person))).fetchone() is None


def aufloesen(dokumente: list, rolle) -> dict | None:
    """Das Dokument zur Angabe ``rolle`` (None: bei genau einem Dokument dieses, sonst None)."""
    if rolle is None:
        return dokumente[0] if len(dokumente) == 1 else None
    return next((d for d in dokumente if d["rolle"] == rolle), None) if isinstance(rolle, str) else None
