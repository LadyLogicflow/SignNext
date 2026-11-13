"""Schnittstelle für Beratungsstellen (Portal-Cookie). Jede Route arbeitet nur mit dem Mandanten aus der Sitzung (g.nutzer['mandant_ref'])."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request

import db
import mail_zugang
import pdf_eingang
import pruefung
import schluessel
import versand
from flask import Response
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
    "eingabe_ungueltig": "Die Eingabe ist ungültig.",
    "email_ungueltig": "Die Mailadresse ist ungültig.",
    "geburtsdatum_ungueltig": "Das Geburtsdatum ist ungültig (Format TT.MM.JJJJ).",
    "personen_ungueltig": "Die Personenzahl muss 1 oder 2 sein.",
    "schon_versendet": "Dieser Vorgang wurde bereits versendet.",
    "versand_laeuft_oder_unklar": "Der Versand läuft oder sein Ergebnis ist unklar. Bitte nicht erneut versenden.",
    "basis_url_fehlt": "Der Dienst ist nicht vollständig eingerichtet (Adresse fehlt).",
    "nicht_bereit": "Der Dienst ist nicht vollständig eingerichtet.",
    "mail_zugang_fehlt": "Für Ihre Beratungsstelle ist noch kein Mail-Zugang hinterlegt.",
    "mail_zugang_nicht_lesbar": "Der Mail-Zugang lässt sich nicht lesen. Bitte neu eintragen.",
    "angaben_fehlen": "Es fehlen Angaben für den Versand.",
    "zu_viele_versaende": "Zu viele Versände in der letzten Stunde.",
    "bestaetigung_fehlt": "Bitte ausdrücklich bestätigen (bestaetigt: true).",
    "mail_tls": "Die verschlüsselte Verbindung zum Mailserver ist nicht möglich oder das Zertifikat ist ungültig.",
    "mail_verbindung": "Der Mailserver ist nicht erreichbar.",
    "mail_anmeldung": "Die Anmeldung am Mailserver wurde abgelehnt. Bitte Zugangsdaten prüfen.",
    "mail_absender_abgelehnt": "Der Mailserver hat die Absenderadresse abgelehnt.",
    "mail_empfaenger_abgelehnt": "Der Mailserver hat die Empfängeradresse abgelehnt. Bitte die Mailadresse prüfen.",
    "mail_abgelehnt": "Der Mailserver hat die Nachricht abgelehnt.",
    "mail_konfiguration": "Der Mail-Zugang ist nicht zulässig konfiguriert.",
    "versand_unklar": "Ob die Mail zugestellt wurde, ist unklar. Bitte im Postfach prüfen und nicht automatisch erneut senden.",
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


@bp.get("/vorgaenge/<int:vorgang_id>/pruefung")
@anmeldung_pflicht()
def vorgang_pruefung(vorgang_id):
    try:
        antwort = pruefung.ansicht(g.nutzer["mandant_ref"], vorgang_id)
    except pruefung.Fehler as f:
        return jsonify({"error": f.code, "meldung": MELDUNGEN.get(f.code, f.code)}), f.status
    except pdf_eingang.Fehler as f:
        return _fehler(f)
    except schluessel.HauptschluesselFehlt:
        return jsonify({"error": "nicht_bereit", "meldung": MELDUNGEN["nicht_bereit"]}), 503
    except schluessel.Entschluesselung:
        return jsonify({"error": "angaben_nicht_lesbar", "meldung": "Gespeicherte Angaben lassen sich nicht lesen. Bitte neu eintragen."}), 503
    audit("pruefansicht_gelesen", "id=%d" % vorgang_id)
    return jsonify(antwort)


@bp.put("/vorgaenge/<int:vorgang_id>/angaben")
@anmeldung_pflicht()
def vorgang_angaben(vorgang_id):
    try:
        ergebnis = pruefung.angaben_setzen(g.nutzer["mandant_ref"], vorgang_id, request.get_json(silent=True))
    except pruefung.Fehler as f:
        antwort = {"error": f.code, "meldung": MELDUNGEN.get(f.code, f.code)}
        if f.feld:
            antwort["feld"] = f.feld
        return jsonify(antwort), f.status
    except pdf_eingang.Fehler as f:
        return _fehler(f)
    except schluessel.HauptschluesselFehlt:
        return jsonify({"error": "nicht_bereit", "meldung": MELDUNGEN["nicht_bereit"]}), 503
    # Im Audit stehen nur die Namen der gesetzten Felder, nie Werte.
    audit("angaben_gesetzt", "id=%d felder=%s" % (vorgang_id, ",".join(ergebnis["gesetzt"])))
    return jsonify(ergebnis)


@bp.post("/vorgaenge/<int:vorgang_id>/versenden")
@anmeldung_pflicht()
def vorgang_versenden(vorgang_id):
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or daten.get("bestaetigt") is not True:
        return jsonify({"error": "bestaetigung_fehlt", "meldung": MELDUNGEN["bestaetigung_fehlt"]}), 400
    try:
        ergebnis = versand.versenden(g.nutzer["mandant_ref"], vorgang_id, g.nutzer["email"])
    except versand.Fehler as f:
        audit("versand_abgebrochen", "id=%d grund=%s" % (vorgang_id, f.code))
        antwort = {"error": f.code, "meldung": MELDUNGEN.get(f.code, f.code)}
        if f.einzelheiten:
            antwort["fehlend"] = f.einzelheiten
        return jsonify(antwort), f.status
    except pdf_eingang.Fehler as f:
        return _fehler(f)
    audit("versendet", "id=%d" % vorgang_id)
    return jsonify(ergebnis)


@bp.get("/vorgaenge/<int:vorgang_id>/dokument")
@anmeldung_pflicht()
def vorgang_dokument(vorgang_id):
    """Das zusammengeführte PDF (sonst das einzelne Dokument) zum Ansehen in der Prüfansicht. Nur eigener Mandant."""
    mandant = g.nutzer["mandant_ref"]
    if db.vorgang_lesen(mandant, vorgang_id) is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    daten = None
    for rolle in ("M", "B", "A"):
        daten = db.dokument_bytes(mandant, vorgang_id, rolle)
        if daten is not None:
            break
    if daten is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    audit("dokument_gelesen", "id=%d" % vorgang_id)
    antwort = Response(daten, mimetype="application/pdf")
    antwort.headers["Content-Disposition"] = 'inline; filename="dokument.pdf"'
    antwort.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return antwort


# --- Mail-Zugang der Beratungsstelle ---

@bp.get("/mail-zugang")
@anmeldung_pflicht()
def mail_zugang_lesen():
    return jsonify(mail_zugang.sicht(g.nutzer["mandant_ref"]))


@bp.put("/mail-zugang")
@anmeldung_pflicht("admin")
def mail_zugang_setzen():
    try:
        ergebnis = mail_zugang.setzen(g.nutzer["mandant_ref"], request.get_json(silent=True), g.nutzer["email"])
    except mail_zugang.Ungueltig as u:
        antwort = {"error": "eingabe_ungueltig", "meldung": u.text}
        if u.feld:
            antwort["feld"] = u.feld
        return jsonify(antwort), 422
    except schluessel.HauptschluesselFehlt:
        return jsonify({"error": "nicht_bereit", "meldung": MELDUNGEN["nicht_bereit"]}), 503
    audit("mailzugang_gesetzt", "")
    return jsonify(ergebnis)


@bp.delete("/mail-zugang")
@anmeldung_pflicht("admin")
def mail_zugang_loeschen():
    if not mail_zugang.loeschen(g.nutzer["mandant_ref"]):
        return jsonify({"error": "nicht_gefunden"}), 404
    audit("mailzugang_geloescht", "")
    return jsonify({"geloescht": True})


@bp.post("/mail-zugang/pruefen")
@anmeldung_pflicht("admin")
def mail_zugang_pruefen():
    try:
        ergebnis = mail_zugang.pruefen(g.nutzer["mandant_ref"], g.nutzer["email"])
    except schluessel.HauptschluesselFehlt:
        return jsonify({"error": "nicht_bereit", "meldung": MELDUNGEN["nicht_bereit"]}), 503
    except schluessel.Entschluesselung:
        return jsonify({"error": "mail_zugang_nicht_lesbar", "meldung": MELDUNGEN["mail_zugang_nicht_lesbar"]}), 503
    audit("mailzugang_geprueft", "ok=%s%s" % (ergebnis["ok"], "" if ergebnis["ok"] else " fehler=" + ergebnis["fehler"]))
    if ergebnis["ok"]:
        return jsonify({"ok": True})
    code = "mail_" + ergebnis["fehler"] if ergebnis["fehler"] != "nicht_konfiguriert" else "mail_zugang_fehlt"
    return jsonify({"ok": False, "error": code, "meldung": MELDUNGEN.get(code, code)}), (409 if code == "mail_zugang_fehlt" else 502)
