"""Dokumente eines Vorgangs (v0.7.0, Einzel-Signatur, catrin 18.11.2026): Jedes Dokument (Anschreiben 'B', Protokoll 'A') wird einzeln gelesen, unterschrieben und abgeschlossen.

Reihenfolge: Anschreiben vor Protokoll; je Dokument unterschreiben nur Personen mit Unterschriftsstelle in diesem Dokument, Person 1 vor Person 2.
Vorgänge aus früheren Fassungen haben ein zusammengeführtes Dokument 'M' (Rolle 'M', Ergebnis 'S'/'N'); sie laufen als ein einziges Dokument weiter.
Rollen der Ergebnisse: 'S_<Rolle>' = unterschriebenes Dokument, 'N_<Rolle>' = Nachweis (bei 'M': 'S' und 'N')."""
import abgleich
import db

TITEL = {"B": "Anschreiben", "A": "Protokoll", "M": "Dokument"}
REIHENFOLGE = ("B", "A")


def rollen(mandant_ref: str, vorgang_id: int) -> list:
    """Die Dokument-Rollen des Vorgangs in Unterschriftsreihenfolge (auch nach dem Abschluss, wenn die Originale gelöscht sind)."""
    vorhanden = {z["rolle"] for z in db.dokumente_liste(mandant_ref, vorgang_id)}
    if vorhanden & {"M", "S"}:
        return ["M"]
    r = [x for x in REIHENFOLGE if x in vorhanden]
    if r:
        return r
    return [x for x in REIHENFOLGE if "S_" + x in vorhanden]


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
