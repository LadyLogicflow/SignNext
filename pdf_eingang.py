"""PDF-Eingang: fremde PDFs im Kindprozess auswerten, abgleichen, ablegen, zusammenführen.

Personendaten (Namen, Steuernummer, Steuer-ID, Geburtsdatum, Mailadresse) bleiben im Arbeitsspeicher dieser Funktionen und werden weder gespeichert noch
protokolliert; abgelegt werden nur Dateien, Hash, Größe, Seitenzahl, Stellen und das Abgleichergebnis."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

import abgleich
import db
from pdf_pruefung import MAX_BYTES

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_worker.py")
TIMEOUT_SEKUNDEN = 40
MAX_AUSGABE = 1024 * 1024
UPLOADS_JE_STUNDE = 20


class Fehler(Exception):
    def __init__(self, code: str, status: int = 422):
        super().__init__(code)
        self.code = code
        self.status = status


def auswerten(daten: bytes) -> dict:
    """Startet den Kindprozess (eigene Speicher-/Zeitgrenze, leere Umgebung). Wirft Fehler(code)."""
    if len(daten) > MAX_BYTES:
        raise Fehler("zu_gross", 413)
    umgebung = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run([sys.executable, WORKER], input=daten, capture_output=True, timeout=TIMEOUT_SEKUNDEN,
                           cwd=tempfile.gettempdir(), env=umgebung)
    except subprocess.TimeoutExpired:
        raise Fehler("zeitueberschreitung")
    if p.returncode != 0 or len(p.stdout) > MAX_AUSGABE:
        raise Fehler("analyse_fehlgeschlagen")
    try:
        ergebnis = json.loads(p.stdout.decode("utf-8"))
    except ValueError:
        raise Fehler("analyse_fehlgeschlagen")
    if not isinstance(ergebnis, dict) or not ergebnis.get("ok"):
        raise Fehler(str(ergebnis.get("fehler", "analyse_fehlgeschlagen")) if isinstance(ergebnis, dict) else "analyse_fehlgeschlagen")
    return ergebnis


def _speichern(mandant_ref: str, vorgang_id: int, rolle: str, daten: bytes, e: dict) -> int:
    return db.dokument_speichern(mandant_ref, vorgang_id, rolle, daten, hashlib.sha256(daten).hexdigest(), e["seiten"], e["stellen"])


NAME_MAX = 120


def name_aus_dateiname(dateiname) -> str | None:
    """Vorgangsname aus dem Dateinamen: Pfad und Endung .pdf weg, Steuerzeichen raus, auf NAME_MAX gekürzt. Leer -> None. Enthält oft Personennamen: nie in Audit, Log oder Mail."""
    import unicodedata
    if not isinstance(dateiname, str):
        return None
    n = dateiname.replace("\\", "/").rsplit("/", 1)[-1]
    if n.lower().endswith(".pdf"):
        n = n[:-4]
    n = "".join(" " if unicodedata.category(ch)[0] in ("C", "Z") else ch for ch in n)
    n = " ".join(n.split())[:NAME_MAX].strip()
    return n or None


def name_aus_werten(werte: dict) -> str | None:
    """'Vorname Name' aus den Werten eines PDFs (die erste Person), bereinigt; sonst None. Enthält Personendaten: nie in Audit, Log oder Mail."""
    import unicodedata
    text = " ".join(x for x in (werte.get("vorname"), werte.get("name")) if isinstance(x, str) and x.strip())
    text = "".join(" " if unicodedata.category(ch)[0] in ("C", "Z") else ch for ch in text)
    return " ".join(text.split())[:NAME_MAX].strip() or None


def _bereinigt(text) -> str | None:
    import unicodedata
    if not isinstance(text, str):
        return None
    text = "".join(" " if unicodedata.category(ch)[0] in ("C", "Z") else ch for ch in text)
    return " ".join(text.split())[:NAME_MAX].strip() or None


def dateiname_felder(aus_a: dict, aus_b: dict) -> tuple:
    """(Mitgliedsnummer, Nachname) für die Download-Dateinamen: 7-stellige Nummer und Nachname der ersten Person, zuerst aus dem Protokoll (A), sonst aus dem Anschreiben (B);
    fehlt etwas, bleibt es None (die Dateinamen fallen dann auf die bisherigen zurück). Enthält Personendaten: nie in Audit, Log oder Mail."""
    import re
    nr = next((w["mitgliedsnr"] for w in (aus_a, aus_b) if isinstance(w.get("mitgliedsnr"), str) and re.fullmatch(r"\d{7}", w["mitgliedsnr"])), None)
    nachname = next((n for n in (_bereinigt(aus_a.get("name")), _bereinigt(aus_b.get("name"))) if n), None)
    return nr, nachname


def anlegen(mandant_ref: str, dateien: list, namen: list | None = None) -> dict:
    """1 oder 2 PDFs (Bytes) → neuer Vorgang. Wirft Fehler(code). Der Typ kommt aus dem Inhalt, nie aus dem Dateinamen.
    Zwei Dokumente müssen je eines vom Typ A und B sein. Bei 2 von 3 Treffern wird sofort zusammengeführt, sonst muss die Beratungsstelle bestätigen."""
    if not 1 <= len(dateien) <= 2:
        raise Fehler("dateizahl", 400)
    ergebnisse = [auswerten(d) for d in dateien]
    typen = sorted(e["typ"] for e in ergebnisse)
    if len(dateien) == 2 and typen != ["A", "B"]:
        raise Fehler("typen_passen_nicht")
    by = {e["typ"]: (d, e) for d, e in zip(dateien, ergebnisse)}
    namen = list(namen or []) + [None] * len(dateien)
    name_je_typ = {e["typ"]: name_aus_dateiname(n) for e, n in zip(ergebnisse, namen)}
    aus_pdf = {typ: name_aus_werten(e["werte"]) for typ, (_, e) in by.items()}       # catrin 18.11.: der Name des Mitglieds, wie ihn auch die Prüfansicht nimmt (Protokoll, sonst Anschreiben)
    name = aus_pdf.get("A") or aus_pdf.get("B") or name_je_typ.get("A") or name_je_typ.get("B")      # Ersatz: Dateiname
    mitgliedsnr, nachname = dateiname_felder(by["A"][1]["werte"] if "A" in by else {}, by["B"][1]["werte"] if "B" in by else {})
    vorgang_id = db.vorgang_anlegen(mandant_ref)
    try:
        for typ, (d, e) in sorted(by.items()):
            _speichern(mandant_ref, vorgang_id, typ, d, e)
        personen = by["B"][1]["personen"] if "B" in by else None
        if len(dateien) == 1:
            ab = {"status": "einzeln", "treffer": [], "abweichend": [], "fehlend": []}
            status = "bereit"
        else:
            ab = abgleich.abgleichen(by["A"][1]["werte"], by["B"][1]["werte"])
            status = "bereit" if ab["status"] == "automatisch" else "bestaetigung_noetig"
        db.vorgang_aendern(mandant_ref, vorgang_id, status=status, personen=personen, abgleich=json.dumps(ab), name=name, mitgliedsnr=mitgliedsnr, nachname=nachname)
    except Exception:
        loeschen(mandant_ref, vorgang_id)
        raise
    return {"vorgang_id": vorgang_id, "status": status, "name": name, "abgleich": ab, "personen": personen,
            "dokumente": [{"rolle": t, "seiten": e["seiten"], "stellen": len(e["stellen"])} for t, (_, e) in sorted(by.items())],
            "hinweise": sorted({h for _, (_, e) in by.items() for h in e["hinweise"]}), "mail_erkannt": "mail" in by.get("B", (None, {"werte": {}}))[1]["werte"]}


def bestaetigen(mandant_ref: str, vorgang_id: int, nutzer: str) -> dict:
    """Die Beratungsstelle bestätigt, dass A und B zusammengehören (weniger als 2 von 3 Treffern). Wirft Fehler."""
    v = db.vorgang_lesen(mandant_ref, vorgang_id)
    if v is None:
        raise Fehler("nicht_gefunden", 404)
    if v["status"] != "bestaetigung_noetig":
        raise Fehler("zustand_passt_nicht", 409)
    a = db.dokument_bytes(mandant_ref, vorgang_id, "A")
    b = db.dokument_bytes(mandant_ref, vorgang_id, "B")
    if a is None or b is None:
        raise Fehler("dokumente_fehlen", 409)
    db.vorgang_aendern(mandant_ref, vorgang_id, status="bereit", bestaetigt_von=nutzer, bestaetigt_am=db.jetzt())
    return {"vorgang_id": vorgang_id, "status": "bereit"}


def loeschen(mandant_ref: str, vorgang_id: int) -> None:
    """Räumt einen halb angelegten Vorgang samt Dateien und Zeilen ab (nur eigener Mandant)."""
    import shutil
    verz = db.dokumente_verzeichnis(mandant_ref) / str(int(vorgang_id))
    shutil.rmtree(verz, ignore_errors=True)
    with db.verbindung() as c:
        c.execute("DELETE FROM dokument WHERE vorgang_id=? AND mandant_ref=?", (int(vorgang_id), mandant_ref))
        c.execute("DELETE FROM vorgang WHERE id=? AND mandant_ref=?", (int(vorgang_id), mandant_ref))
