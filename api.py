"""Schnittstelle für Beratungsstellen (Portal-Cookie). Jede Route arbeitet nur mit dem Mandanten aus der Sitzung (g.nutzer['mandant_ref'])."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request

import db
import pdf_eingang
from pdf_pruefung import MAX_BYTES
from portal_auth import anmeldung_pflicht, audit

bp = Blueprint("api", __name__, url_prefix="/api/v1")


@bp.get("/me")
@anmeldung_pflicht()
def me():
    return jsonify({"email": g.nutzer["email"], "rolle": g.nutzer["rolle"], "produkt": "signnext"})


@bp.get("/vorgaenge")
@anmeldung_pflicht()
def vorgaenge():
    return jsonify({"vorgaenge": db.vorgaenge_liste(g.nutzer["mandant_ref"])})


@bp.get("/vorgaenge/<int:vorgang_id>")
@anmeldung_pflicht()
def vorgang(vorgang_id):
    v = db.vorgang_lesen(g.nutzer["mandant_ref"], vorgang_id)
    if v is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    audit("vorgang_gelesen", "id=%d" % vorgang_id)
    v["dokumente"] = [{k: d[k] for k in ("id", "rolle", "seiten", "groesse", "sha256", "stellen")} for d in db.dokumente_liste(g.nutzer["mandant_ref"], vorgang_id)]
    return jsonify(v)


MELDUNGEN = {
    "dateizahl": "Bitte eine oder zwei PDF-Dateien senden (Feld dateien).",
    "zu_gross": "Die Datei ist zu groß.",
    "kein_pdf": "Das ist keine PDF-Datei.",
    "defekt": "Die PDF-Datei lässt sich nicht lesen.",
    "verschluesselt": "Verschlüsselte PDF-Dateien werden nicht angenommen.",
    "zu_viele_seiten": "Die PDF-Datei hat zu viele Seiten.",
    "aktive_inhalte": "Die PDF-Datei enthält aktive Inhalte und wird nicht angenommen.",
    "keine_textebene": "Die PDF-Datei enthält keinen Text (vermutlich ein Scan). Bitte die Datei direkt aus der Steuersoft bzw. Textverarbeitung als PDF erzeugen.",
    "typ_unbekannt": "Das Dokument wurde nicht als Anschreiben oder Steuerprotokoll erkannt.",
    "stellen_unklar": "Die Unterschriftsstellen sind nicht eindeutig erkennbar.",
    "typen_passen_nicht": "Es werden ein Anschreiben und ein Steuerprotokoll erwartet (je eines).",
    "zeitueberschreitung": "Die Auswertung hat zu lange gedauert.",
    "analyse_fehlgeschlagen": "Die Auswertung ist fehlgeschlagen.",
    "nicht_gefunden": "Nicht gefunden.",
    "zustand_passt_nicht": "Der Vorgang befindet sich nicht in diesem Zustand.",
    "dokumente_fehlen": "Die Dokumente des Vorgangs fehlen.",
    "zu_viele_uploads": "Zu viele Uploads in der letzten Stunde.",
}


def _fehler(f: pdf_eingang.Fehler):
    return jsonify({"error": f.code, "meldung": MELDUNGEN.get(f.code, f.code)}), f.status


@bp.post("/vorgaenge")
@anmeldung_pflicht()
def vorgang_anlegen():
    mandant = g.nutzer["mandant_ref"]
    seit = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.verbindung() as c:
        n = c.execute("SELECT COUNT(*) FROM audit_log WHERE mandant_ref=? AND aktion='vorgang_angelegt' AND zeit>=?", (mandant, seit)).fetchone()[0]
    if n >= pdf_eingang.UPLOADS_JE_STUNDE:
        return _fehler(pdf_eingang.Fehler("zu_viele_uploads", 429))
    dateien = []
    for f in request.files.getlist("dateien"):
        daten = f.stream.read(MAX_BYTES + 1)
        dateien.append(daten)
    try:
        ergebnis = pdf_eingang.anlegen(mandant, dateien)
    except pdf_eingang.Fehler as f:
        audit("upload_abgelehnt", f.code)
        return _fehler(f)
    audit("vorgang_angelegt", "id=%d status=%s dokumente=%s abgleich=%s" % (
        ergebnis["vorgang_id"], ergebnis["status"], "+".join(d["rolle"] for d in ergebnis["dokumente"]), ",".join(ergebnis["abgleich"]["treffer"]) or "-"))
    return jsonify(ergebnis), 201


@bp.post("/vorgaenge/<int:vorgang_id>/abgleich-bestaetigen")
@anmeldung_pflicht()
def abgleich_bestaetigen(vorgang_id):
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or daten.get("bestaetigt") is not True:
        return jsonify({"error": "bestaetigung_fehlt", "meldung": "Bitte ausdrücklich bestätigen (bestaetigt: true)."}), 400
    try:
        ergebnis = pdf_eingang.bestaetigen(g.nutzer["mandant_ref"], vorgang_id, g.nutzer["email"])
    except pdf_eingang.Fehler as f:
        return _fehler(f)
    audit("abgleich_bestaetigt", "id=%d" % vorgang_id)
    return jsonify(ergebnis)
