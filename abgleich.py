"""Abgleich der Dokumente A und B (2 von 3 Kriterien) und Zusammenführen zu einem PDF.

Kriterien (nur wenn in BEIDEN Dokumenten vorhanden): Steuernummer (die aus dem Hauptvordruck von A muss unter den Nummern von B stehen),
Steuer-ID (alle vorhandenen IDs müssen übereinstimmen, bei Ehegatten beide), Mitgliedsnummer. Mehrdeutiges oder Fehlendes zählt nie als Treffer."""
import io

from pypdf import PdfReader, PdfWriter

KRITERIEN = ("steuernummer", "steuer_id", "mitgliedsnummer")


def _vergleiche(a: dict, b: dict) -> dict:
    ergebnis = {}
    if a.get("steuernummern") and b.get("steuernummern"):
        ergebnis["steuernummer"] = len(a["steuernummern"]) == 1 and a["steuernummern"][0] in b["steuernummern"]
    if a.get("ids") and b.get("ids"):
        ergebnis["steuer_id"] = sorted(a["ids"]) == sorted(b["ids"])
    if a.get("mitgliedsnr") and b.get("mitgliedsnr"):
        ergebnis["mitgliedsnummer"] = a["mitgliedsnr"] == b["mitgliedsnr"]
    return ergebnis


def abgleichen(werte_a: dict, werte_b: dict) -> dict:
    """{'status': 'automatisch'|'bestaetigung_noetig', 'treffer': [...], 'abweichend': [...], 'fehlend': [...]}. Enthält keine Personendaten."""
    v = _vergleiche(werte_a, werte_b)
    treffer = [k for k in KRITERIEN if v.get(k) is True]
    abweichend = [k for k in KRITERIEN if v.get(k) is False]
    fehlend = [k for k in KRITERIEN if k not in v]
    return {"status": "automatisch" if len(treffer) >= 2 else "bestaetigung_noetig", "treffer": treffer, "abweichend": abweichend, "fehlend": fehlend}


def zusammenfuehren(daten_b: bytes, daten_a: bytes, stellen_b: list, stellen_a: list) -> tuple[bytes, list]:
    """Anschreiben (B) vorn, Protokoll (A) dahinter. Liefert (PDF-Bytes, Stellen im neuen Dokument; Seiten von A um die Seitenzahl von B verschoben)."""
    rb, ra = PdfReader(io.BytesIO(daten_b)), PdfReader(io.BytesIO(daten_a))
    versatz = len(rb.pages)
    w = PdfWriter()
    w.append(rb)
    w.append(ra)
    if len(w.pages) != versatz + len(ra.pages):
        raise ValueError("Seitenzahl nach dem Zusammenführen stimmt nicht")
    puffer = io.BytesIO()
    w.write(puffer)
    stellen = [dict(s, dokument="B") for s in stellen_b] + [dict(s, dokument="A", seite=s["seite"] + versatz) for s in stellen_a]
    return puffer.getvalue(), stellen


def aktive_stellen(stellen: list, personen: int | None) -> list:
    """Stellen, an denen unterschrieben wird: Personen 1..personen (bei einer Person nur die jeweils erste Stelle je Block)."""
    if personen not in (1, 2):
        raise ValueError("personenzahl_fehlt")
    return [s for s in stellen if s["person"] <= personen]
