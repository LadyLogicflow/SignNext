"""Abschluss eines Vorgangs nach der letzten Unterschrift: Unterschriften in das Dokument einbetten, Nachweisprotokoll als eigenes PDF, Originale löschen.

Ablauf (jeder Schritt wiederholbar, Zustände: unterschrieben → abschluss_laeuft → abgeschlossen | abschluss_fehlgeschlagen):
1. atomarer Anspruch (nur ein Aufruf gewinnt, ``abschluss_versuche`` wird erhöht),
2. Unterschriftsbilder (PNG) je aktiver Stelle mit reportlab als Overlay erzeugen und per pypdf auf die Seite legen (ohne PyMuPDF),
3. Nachweisprotokoll (eigenes PDF) mit SHA-256 des Dokuments vor und nach dem Einbetten,
4. beide Dateien ablegen (Rollen 'S' = unterschriebenes Dokument, 'N' = Nachweis), Status 'abgeschlossen', Aufbewahrungsfrist der Beratungsstelle festhalten,
5. Originale (A, B, M) und das wiederherstellbare Token löschen, Löschprotokoll (nur Zahlen).
Das Dokument wird NICHT kryptografisch signiert: Der Nachweis belegt Ablauf, Zeitpunkte und Prüfsummen, mehr nicht."""
import hashlib
import io
import logging
from datetime import datetime, timedelta, timezone

from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import abgleich
import config
import db
import dokumente
import texte

LOG = logging.getLogger("signnext")
ABSCHLUSS_ROLLEN = ("S", "N", "S_A", "S_B", "N_A", "N_B")       # S/N: zusammengeführtes Dokument älterer Vorgänge; S_<Rolle>/N_<Rolle>: je Dokument
ORIGINAL_ROLLEN = ("A", "B", "M")


class Fehler(Exception):
    pass


from berlinzeit import berlin  # noqa: E402,F401  (Anzeige in deutscher Zeit; liegt in berlinzeit.py, hier weiter erreichbar)


# --- Einbetten -------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def _quelle(mandant_ref: str, vorgang_id: int, rolle: str) -> tuple[bytes, list]:
    """(PDF, Stellen) des Originals dieses Dokuments."""
    zeilen = {d["rolle"]: d for d in db.dokumente_liste(mandant_ref, vorgang_id)}
    daten = db.dokument_bytes(mandant_ref, vorgang_id, rolle) if rolle in zeilen else None
    if daten is None:
        raise Fehler("quelle_fehlt")
    return daten, zeilen[rolle]["stellen"]


def einbetten(daten: bytes, stellen: list, personen: int, bilder: dict) -> bytes:
    """Legt je aktiver Stelle das Unterschriftsbild (PNG, mit Transparenz) auf die Seite. ``bilder``: {person: png_bytes}. Seitenverhältnis bleibt erhalten."""
    aktiv = abgleich.aktive_stellen(stellen, personen)
    if not aktiv or any(s["person"] not in bilder for s in aktiv):
        raise Fehler("stellen_oder_unterschriften_fehlen")
    leser = PdfReader(io.BytesIO(daten))
    schreiber = PdfWriter(clone_from=leser)
    je_seite: dict[int, list] = {}
    for s in aktiv:
        je_seite.setdefault(int(s["seite"]), []).append(s)
    for nr, liste in je_seite.items():
        if not 1 <= nr <= len(schreiber.pages):
            raise Fehler("seite_fehlt")
        seite = schreiber.pages[nr - 1]
        if int(seite.get("/Rotate", 0) or 0) % 360:
            raise Fehler("seite_gedreht")
        mb = seite.mediabox
        links, unten, rechts, oben = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
        puffer = io.BytesIO()
        c = canvas.Canvas(puffer, pagesize=(rechts, oben))
        for s in liste:
            bild = ImageReader(io.BytesIO(bilder[s["person"]]))
            bw, bh = bild.getSize()
            breite, hoehe = float(s["x1"]) - float(s["x0"]), float(s["y1"]) - float(s["y0"])
            if breite <= 0 or hoehe <= 0 or bw <= 0 or bh <= 0:
                raise Fehler("stelle_ungueltig")
            skal = min(breite / bw, hoehe / bh)
            w, h = bw * skal, bh * skal
            c.drawImage(bild, links + float(s["x0"]) + (breite - w) / 2, unten + float(s["y0"]) + (hoehe - h) / 2, w, h, mask="auto")
        c.showPage()
        c.save()
        seite.merge_page(PdfReader(io.BytesIO(puffer.getvalue())).pages[0])
    aus = io.BytesIO()
    schreiber.write(aus)
    return aus.getvalue()


# --- Nachweisprotokoll -----------------------------------------------------------------------------------------------------------------------------------------------------------

def nachweis_pdf(vorgang_id: int, hash_vorher: str, hash_nachher: str, unterschriften: list, abgeschlossen_am: str, textkennung_frei: bool, dokument_titel: str | None = None) -> bytes:
    """Eigenes PDF: Ablauf, Zeitpunkte (MEZ/MESZ), Prüfsummen. Keine Namen, kein Geburtsdatum, keine E-Mail-Adresse."""
    puffer = io.BytesIO()
    c = canvas.Canvas(puffer, pagesize=(595.28, 841.89))
    zustand = {"y": 800.0}

    def neue_seite():
        c.showPage()
        zustand["y"] = 800.0

    def zeile(text: str, groesse: float = 9.5, schrift: str = "Helvetica", abstand: float = 13.0, x: float = 56.0):
        """Eine Zeile; zu lange Zeilen werden an Wortgrenzen umbrochen (nichts darf über den Seitenrand laufen)."""
        breite = 595.28 - 56.0 - x
        zeilen, aktuell = [], ""
        for wort in text.split(" "):
            probe = (aktuell + " " + wort) if aktuell else wort
            if aktuell and stringWidth(probe, schrift, groesse) > breite:
                zeilen.append(aktuell)
                aktuell = wort
            else:
                aktuell = probe
        zeilen.append(aktuell)
        for z in zeilen:
            if zustand["y"] < 60:
                neue_seite()
            c.setFont(schrift, groesse)
            c.drawString(x, zustand["y"], z)
            zustand["y"] -= abstand

    zeile("Nachweisprotokoll zur Unterschrift", 15, "Helvetica-Bold", 22)
    zeile("Vorgang Nr. %d" % int(vorgang_id), 11, "Helvetica-Bold", 16)
    if dokument_titel:
        zeile("Dokument: %s" % dokument_titel, 11, "Helvetica-Bold", 16)
    zeile("Abgeschlossen am: %s" % berlin(abgeschlossen_am))
    zeile("")
    zeile("Prüfsummen (SHA-256) des Dokuments", 10.5, "Helvetica-Bold", 16)
    zeile("Vor dem Einbetten der Unterschriften:")
    zeile(hash_vorher, 8, "Courier", 12, 66)
    zeile("Nach dem Einbetten der Unterschriften:")
    zeile(hash_nachher, 8, "Courier", 12, 66)
    zeile("")
    for u in unterschriften:
        zeile("Unterschrift von Person %d" % u["person"], 10.5, "Helvetica-Bold", 16)
        if u.get("haken_uebernommen"):
            zeile("Datenschutzinformation und Zustimmung wurden in der ersten Unterschrift dieser Person in diesem Vorgang bestätigt (am %s) und für dieses Dokument übernommen." % berlin(u["zeit_texte_angezeigt"]))
            zeile("Unterschrift geleistet: %s" % berlin(u["zeit_unterschrift"]))
        else:
            zeile("Texte (Datenschutzinformation) angezeigt: %s" % berlin(u["zeit_texte_angezeigt"]))
            zeile("Unterschrift geleistet: %s" % berlin(u["zeit_unterschrift"]))
            zeile("Zustimmung per Haken und eigenem Klick bestätigt: %s" % ("ja" if u["haken"] and u["klick"] else "nein"))
        zeile("Textfassung (Kennung): %s" % u["text_kennung"])
        zeile("Prüfsumme der angezeigten Texte:")
        zeile(u["text_hash"], 8, "Courier", 12, 66)
        zeile("Prüfsumme des Unterschriftsbildes:")
        zeile(u["bild_sha256"], 8, "Courier", 12, 66)
        zeile("Netzadresse (gekürzt bzw. nach Einstellung): %s" % (u["ip"] or "nicht gespeichert"))
        zeile("Gerät: %s" % (u["geraet"] or "unbekannt"))
        zeile("")
    zeile("Hinweise", 10.5, "Helvetica-Bold", 16)
    hinweise = [
        "Dieses Protokoll belegt den Ablauf und die Zeitpunkte der Unterschrift sowie die Prüfsummen des Dokuments.",
        "Das Dokument ist nicht kryptografisch signiert. Es handelt sich nicht um eine qualifizierte elektronische Signatur.",
        "Die Prüfsumme nach dem Einbetten gilt für die Datei in der Form, wie sie von SignNext ausgegeben wurde.",
    ]
    if not textkennung_frei:
        hinweise.append("Die angezeigten Texte waren zum Zeitpunkt der Unterschrift noch nicht freigegeben (Freigabe offen).")
    for h in hinweise:
        zeile(h, 8.5, "Helvetica", 12)
    c.showPage()
    c.save()
    return puffer.getvalue()


# --- Ablauf ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def _dateien_der_rollen_loeschen(mandant_ref: str, vorgang_id: int, rollen: tuple) -> int:
    """Entfernt Dateien und Zeilen der Rollen. Liefert die Zahl der Dateien."""
    verz = db.dokumente_verzeichnis(mandant_ref) / str(int(vorgang_id))
    n = 0
    marken = ",".join("?" * len(rollen))
    with db.verbindung() as c:
        zeilen = c.execute("SELECT id, datei FROM dokument WHERE mandant_ref=? AND vorgang_id=? AND rolle IN (%s)" % marken, (mandant_ref, int(vorgang_id), *rollen)).fetchall()
        for z in zeilen:
            p = verz / z["datei"]
            if p.is_file():
                p.unlink()
                n += 1
            c.execute("DELETE FROM dokument WHERE id=?", (z["id"],))
    return n


def originale_loeschen(mandant_ref: str, vorgang_id: int) -> int:
    """Löscht A, B und M eines abgeschlossenen Vorgangs und schreibt das Löschprotokoll (nur Zahlen). Wiederholbar (der Job räumt Reste auf)."""
    n = _dateien_der_rollen_loeschen(mandant_ref, vorgang_id, ORIGINAL_ROLLEN)
    if n:
        with db.verbindung() as c:
            db.loeschprotokoll_schreiben(c, "originale", mandant_ref, vorgang_id, {"dateien": n})
    return n


def anspruch(mandant_ref: str, vorgang_id: int, von: tuple = ("unterschrieben", "abschluss_fehlgeschlagen")) -> bool:
    marken = ",".join("?" * len(von))
    with db.verbindung() as c:
        return c.execute("UPDATE vorgang SET status='abschluss_laeuft', abschluss_seit=?, abschluss_versuche=abschluss_versuche+1 WHERE id=? AND mandant_ref=? AND status IN (%s)" % marken,
                         (db.jetzt(), int(vorgang_id), mandant_ref, *von)).rowcount == 1


def ausfuehren(mandant_ref: str, vorgang_id: int) -> bool:
    """Führt den Abschluss aus. True, wenn dieser Aufruf ihn abgeschlossen hat; False, wenn ein anderer Aufruf zuständig ist. Bei Fehlern: Zustand 'abschluss_fehlgeschlagen', Ausnahme."""
    if not anspruch(mandant_ref, vorgang_id):
        return False
    try:
        _abschliessen(mandant_ref, vorgang_id)
    except Exception:
        db.vorgang_status_wechseln(mandant_ref, vorgang_id, "abschluss_laeuft", "abschluss_fehlgeschlagen")
        LOG.error("Abschluss fehlgeschlagen: vorgang=%d", int(vorgang_id))
        raise
    return True


def _abschliessen(mandant_ref: str, vorgang_id: int) -> None:
    v = db.vorgang_intern(mandant_ref, vorgang_id)
    if v is None or v["personen"] not in (1, 2):
        raise Fehler("vorgang_unvollstaendig")
    docs = dokumente.liste(mandant_ref, vorgang_id, v["personen"])
    if not docs:
        raise Fehler("quelle_fehlt")
    with db.verbindung() as c:
        alle = [dict(z) for z in c.execute("SELECT * FROM unterschrift WHERE mandant_ref=? AND vorgang_id=? ORDER BY dokument_rolle, person", (mandant_ref, int(vorgang_id)))]
    _dateien_der_rollen_loeschen(mandant_ref, vorgang_id, ABSCHLUSS_ROLLEN)       # Reste eines früheren, abgebrochenen Versuchs
    jetzt = db.jetzt()
    ergebnisse = []                                                                # erst alle Dokumente berechnen, dann ablegen
    for d in docs:
        zeilen = [u for u in alle if u["dokument_rolle"] == d["rolle"]]
        if [u["person"] for u in zeilen] != d["erforderlich"]:
            raise Fehler("unterschriften_fehlen")
        quelle, stellen = _quelle(mandant_ref, vorgang_id, d["rolle"])
        hash_vorher = hashlib.sha256(quelle).hexdigest()
        fertig = einbetten(quelle, stellen, v["personen"], {u["person"]: bytes(u["bild"]) for u in zeilen})
        hash_nachher = hashlib.sha256(fertig).hexdigest()
        titel = None if d["rolle"] == "M" else dokumente.NACHWEIS_TITEL[d["rolle"]]
        protokoll = nachweis_pdf(vorgang_id, hash_vorher, hash_nachher, zeilen, jetzt, all(texte.FREIGABE_OFFEN not in (u["text_kennung"] or "") for u in zeilen), titel)
        ergebnisse.append((d["rolle"], fertig, hash_nachher, protokoll))
    for rolle, fertig, hash_nachher, protokoll in ergebnisse:
        db.dokument_speichern(mandant_ref, vorgang_id, dokumente.ergebnis_rolle("dokument", rolle), fertig, hash_nachher, len(PdfReader(io.BytesIO(fertig)).pages), [])
        db.dokument_speichern(mandant_ref, vorgang_id, dokumente.ergebnis_rolle("nachweis", rolle), protokoll, hashlib.sha256(protokoll).hexdigest(), len(PdfReader(io.BytesIO(protokoll)).pages), [])
    einstellung = db.stelle_einstellung_lesen(mandant_ref) or {}
    tage = int(einstellung.get("aufbewahrung_tage") or config.aufbewahrung_standard())
    tage = min(config.AUFBEWAHRUNG_MAX_TAGE, max(config.AUFBEWAHRUNG_MIN_TAGE, tage))
    bis = (datetime.strptime(jetzt, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=tage)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.verbindung() as c:
        ok = c.execute("UPDATE vorgang SET status='abgeschlossen', abgeschlossen_am=?, aufbewahrung_tage=?, aufbewahrung_bis=?, abschluss_seit=NULL WHERE id=? AND mandant_ref=? AND status='abschluss_laeuft'",
                       (jetzt, tage, bis, int(vorgang_id), mandant_ref)).rowcount == 1
        if not ok:
            raise Fehler("zustand_geaendert")
        db.token_entziehen(c, vorgang_id=vorgang_id)
        db.audit_in(c, mandant_ref, "system", "abgeschlossen", "vorgang=%d" % int(vorgang_id))
    originale_loeschen(mandant_ref, vorgang_id)
