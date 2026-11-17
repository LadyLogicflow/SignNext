"""Schnittstelle für Beratungsstellen (Portal-Cookie). Jede Route arbeitet nur mit dem Mandanten aus der Sitzung (g.nutzer['mandant_ref'])."""
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request

import config
import db
import loeschen
import mail_zugang
import pdf_eingang
import pruefung
import schluessel
import tarif
import versand
from flask import Response
from pdf_pruefung import MAX_BYTES
from portal_auth import anmeldung_pflicht, audit

bp = Blueprint("api", __name__, url_prefix="/api/v1")


@bp.get("/me")
@anmeldung_pflicht()
def me():
    antwort = {"email": g.nutzer["email"], "rolle": g.nutzer["rolle"], "produkt": "signnext", "demo": bool(g.nutzer.get("demo"))}
    if antwort["demo"]:
        antwort["demo_banner"] = "DEMO – Testdaten, keine echten Personendaten"
    return jsonify(antwort)


@bp.get("/vorgaenge")
@anmeldung_pflicht()
def vorgaenge():
    return jsonify({"vorgaenge": db.vorgaenge_liste(g.nutzer["mandant_ref"], ohne_geloeschte=True), "status_texte": STATUS_TEXTE})


@bp.delete("/vorgaenge/<int:vorgang_id>")
@anmeldung_pflicht()
def vorgang_loeschen(vorgang_id):
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or set(daten) != {"bestaetigt"} or daten["bestaetigt"] is not True:
        return jsonify({"error": "bestaetigung_fehlt", "meldung": "Bitte ausdrücklich bestätigen (bestaetigt: true)."}), 400
    mandant = g.nutzer["mandant_ref"]
    v = db.vorgang_lesen(mandant, vorgang_id)
    if v is None or v["status"] == "geloescht":
        return jsonify({"error": "nicht_gefunden"}), 404
    if v["status"] in ("abschluss_laeuft", "versenden"):
        return jsonify({"error": "abschluss_laeuft" if v["status"] == "abschluss_laeuft" else "versand_laeuft",
                        "meldung": "Der Vorgang wird gerade bearbeitet und kann jetzt nicht gelöscht werden. Bitte in einigen Minuten erneut versuchen."}), 409
    if loeschen.vorgang_loeschen(mandant, vorgang_id, "storno") is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    audit("vorgang_geloescht_durch_beratungsstelle", "id=%d" % vorgang_id)
    return jsonify({"ok": True})


@bp.get("/vorgaenge/<int:vorgang_id>")
@anmeldung_pflicht()
def vorgang(vorgang_id):
    v = db.vorgang_lesen(g.nutzer["mandant_ref"], vorgang_id)
    if v is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    audit("vorgang_gelesen", "id=%d" % vorgang_id)
    v["link_status"] = db.link_status(g.nutzer["mandant_ref"], vorgang_id)
    v["link_laeuft_ab"] = db.link_laeuft_ab(g.nutzer["mandant_ref"], vorgang_id)
    v["dokumente"] = [{k: d[k] for k in ("id", "rolle", "seiten", "groesse", "sha256", "stellen")} for d in db.dokumente_liste(g.nutzer["mandant_ref"], vorgang_id)]
    return jsonify(v)


STATUS_TEXTE = {
    "neu": "Angelegt, noch nicht geprüft",
    "bestaetigung_noetig": "Abgleich bitte bestätigen",
    "bereit": "Bereit zum Versand",
    "versenden": "Wird gerade versendet",
    "versendet": "Link versendet, wartet auf Unterschrift",
    "versand_unklar": "Versand unklar, bitte im Postfach prüfen",
    "unterschrieben": "Unterschrieben, wird abgeschlossen",
    "abschluss_laeuft": "Wird abgeschlossen",
    "abschluss_fehlgeschlagen": "Abschluss fehlgeschlagen",
    "abgeschlossen": "Abgeschlossen",
    "geloescht": "Gelöscht",
}

MELDUNGEN = {
    "kontingent_erreicht": "Das Kontingent für diesen Monat ist erreicht. Bitte wenden Sie sich an den Betreiber.",
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
    "schon_unterschrieben": "Dieser Vorgang ist bereits unterschrieben.",
    "stelleneinstellungen_fehlen": "Bitte zuerst die Angaben für die Datenschutzinformation eintragen (Einstellungen).",
    "versand_laeuft_oder_unklar": "Der Versand läuft oder sein Ergebnis ist unklar. Bitte nicht erneut versenden.",
    "basis_url_fehlt": "Der Dienst ist nicht vollständig eingerichtet (Adresse fehlt).",
    "nicht_bereit": "Der Dienst ist nicht vollständig eingerichtet.",
    "mail_zugang_fehlt": "Für Ihre Beratungsstelle ist noch kein Mail-Zugang hinterlegt.",
    "mail_zugang_nicht_lesbar": "Der Mail-Zugang lässt sich nicht lesen. Bitte neu eintragen.",
    "angaben_fehlen": "Es fehlen Angaben für den Versand.",
    "zu_viele_versaende": "Zu viele Versände in der letzten Stunde.",
    "bestaetigung_fehlt": "Bitte ausdrücklich bestätigen (bestaetigt: true).",
    "mail_tls_zertifikat": "Zertifikat des Mailservers nicht vertrauenswürdig.",
    "mail_tls_nicht_angeboten": "Der Mailserver bietet keine verschlüsselte Verbindung an.",
    "mail_tls_fehler": "Die verschlüsselte Verbindung zum Mailserver ist fehlgeschlagen.",
    "versand_gesperrt": "Der Versand ist derzeit nicht freigeschaltet.",
    "mail_zugang_ungeprueft": "Bitte zuerst den Mail-Zugang prüfen (Testmail).",
    "empfaenger_nicht_freigegeben": "Diese Adresse ist nicht freigegeben.",
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
    dateien, namen = [], []
    for f in request.files.getlist("dateien"):
        daten = f.stream.read(MAX_BYTES + 1)
        dateien.append(daten)
        namen.append(f.filename)
    try:
        ergebnis = pdf_eingang.anlegen(mandant, dateien, namen)
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
    ohne_mail = daten.get("ohne_mail", False)
    if not isinstance(ohne_mail, bool) or set(daten) - {"bestaetigt", "ohne_mail"} or (ohne_mail and not config.demo_aktiv()):
        return jsonify({"error": "eingabe_ungueltig", "meldung": "Ungültige Eingabe."}), 400
    try:
        if ohne_mail:                                                    # nur Demo: Link und QR am Bildschirm statt Mail
            ergebnis = versand.versenden_demo_ohne_mail(g.nutzer["mandant_ref"], vorgang_id, g.nutzer["email"])
        else:
            ergebnis = versand.versenden(g.nutzer["mandant_ref"], vorgang_id, g.nutzer["email"])
    except versand.Fehler as f:
        audit("versand_abgebrochen", "id=%d grund=%s" % (vorgang_id, f.code))
        antwort = {"error": f.code, "meldung": MELDUNGEN.get(f.code, f.code)}
        if f.code == "kontingent_erreicht":
            antwort["meldung"] = (antwort["meldung"] + " " + config.upgrade_hinweis()).strip()
            antwort.update({k: tarif.info(g.nutzer["mandant_ref"])[k] for k in ("tarif", "limit", "genutzt", "zeitraum")})
        if f.einzelheiten:
            antwort["fehlend"] = f.einzelheiten
        if config.demo_aktiv() and (f.code.startswith("mail_") or f.code in ("empfaenger_nicht_freigegeben", "versand_gesperrt")):
            antwort["fallback"] = "ohne_mail"                            # Demo: die Oberfläche bietet „Ohne Mail fortfahren“ (erneut senden mit ohne_mail: true)
        return jsonify(antwort), f.status
    except pdf_eingang.Fehler as f:
        return _fehler(f)
    audit("versendet", "id=%d%s" % (vorgang_id, " ohne_mail" if ohne_mail else ""))
    link = ergebnis.pop("demo_link", None)
    if link:                                                             # nur Demo: Link, QR und Hinweis für die Anzeige am Bildschirm
        import qr
        ergebnis["demo"] = {"link": link, "qr_svg": qr.svg(link), "ohne_mail": ohne_mail,
                            "geburtsdatum_hinweis": "Zum Öffnen gibt das Mitglied sein Geburtsdatum ein (sechs Ziffern TTMMJJ)."}
    return jsonify(ergebnis)


@bp.get("/vorgaenge/<int:vorgang_id>/dokument")
@anmeldung_pflicht()
def vorgang_dokument(vorgang_id):
    """Das zusammengeführte PDF (sonst das einzelne Dokument) zum Ansehen in der Prüfansicht. Nur eigener Mandant."""
    mandant = g.nutzer["mandant_ref"]
    if db.vorgang_lesen(mandant, vorgang_id) is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    daten = None
    for rolle in ("M", "B", "A", "S"):
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
    if ergebnis["ok"]:
        v = ergebnis["verbindung"]
        audit("mailzugang_geprueft", "ok=True tls=%s cipher=%s zertifikat_bis=%s aussteller=%s" % (
            v.get("tls_version", ""), v.get("cipher", ""), v.get("zertifikat_gueltig_bis", ""), v.get("aussteller", "")))
    else:
        audit("mailzugang_geprueft", "ok=False fehler=" + ergebnis["fehler"])
    if ergebnis["ok"]:
        return jsonify(ergebnis)
    code = "mail_" + ergebnis["fehler"] if ergebnis["fehler"] != "nicht_konfiguriert" else "mail_zugang_fehlt"
    return jsonify({"ok": False, "error": code, "meldung": MELDUNGEN.get(code, code)}), (409 if code == "mail_zugang_fehlt" else 502)


@bp.post("/vorgaenge/<int:vorgang_id>/neuer-link")
@anmeldung_pflicht()
def vorgang_neuer_link(vorgang_id):
    """Neuer Zugangslink (z. B. nach Sperre oder Ablauf); die alten Links werden ungültig. Zählt nicht als neuer Vorgang."""
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or daten.get("bestaetigt") is not True:
        return jsonify({"error": "bestaetigung_fehlt", "meldung": MELDUNGEN["bestaetigung_fehlt"]}), 400
    try:
        ergebnis = versand.neuer_link(g.nutzer["mandant_ref"], vorgang_id, g.nutzer["email"])
    except versand.Fehler as f:
        audit("neuer_link_abgebrochen", "id=%d grund=%s" % (vorgang_id, f.code))
        return jsonify({"error": f.code, "meldung": MELDUNGEN.get(f.code, f.code)}), f.status
    except pdf_eingang.Fehler as f:
        return _fehler(f)
    audit("neuer_link", "id=%d" % vorgang_id)
    return jsonify(ergebnis)


# --- Angaben der Beratungsstelle für die Datenschutzinformation ---

@bp.get("/einstellungen")
@anmeldung_pflicht()
def einstellungen_lesen():
    e = db.stelle_einstellung_lesen(g.nutzer["mandant_ref"])
    if not e:
        return jsonify({"gesetzt": False})
    e["aufbewahrung_tage"] = e["aufbewahrung_tage"] or config.aufbewahrung_standard()
    e["mitglied_download_link"] = bool(e["mitglied_download_link"])
    return jsonify(dict(e, gesetzt=True))


@bp.put("/einstellungen")
@anmeldung_pflicht("admin")
def einstellungen_setzen():
    """Angaben der Beratungsstelle. Beim ersten Mal sind ``verantwortlicher`` und ``datenschutz_link`` Pflicht; danach darf jede Teilmenge der vier Felder gesendet werden
    (nicht gesendete Felder bleiben unverändert)."""
    erlaubt = {"verantwortlicher", "datenschutz_link", "aufbewahrung_tage", "mitglied_download_link"}
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or not d or set(d) - erlaubt:
        return jsonify({"error": "eingabe_ungueltig", "meldung": MELDUNGEN["eingabe_ungueltig"]}), 422
    mandant = g.nutzer["mandant_ref"]
    bisher = db.stelle_einstellung_lesen(mandant)
    if bisher is None and not {"verantwortlicher", "datenschutz_link"} <= set(d):
        return jsonify({"error": "eingabe_ungueltig", "meldung": "Bitte zuerst Name, Anschrift und Kontakt sowie den Link zur Datenschutzerklärung angeben."}), 422
    v = d["verantwortlicher"] if "verantwortlicher" in d else bisher["verantwortlicher"]
    link = d["datenschutz_link"] if "datenschutz_link" in d else bisher["datenschutz_link"]
    if not isinstance(v, str) or not 5 <= len(v.strip()) <= 500 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", v):
        return jsonify({"error": "eingabe_ungueltig", "meldung": "Bitte Name, Anschrift und Kontakt der Beratungsstelle angeben.", "feld": "verantwortlicher"}), 422
    if not isinstance(link, str) or not re.fullmatch(r"https://[^\s<>\"']{4,300}", link.strip()):
        return jsonify({"error": "eingabe_ungueltig", "meldung": "Bitte einen Link zur Datenschutzerklärung (https) angeben.", "feld": "datenschutz_link"}), 422
    tage = d.get("aufbewahrung_tage")
    if "aufbewahrung_tage" in d and (isinstance(tage, bool) or not isinstance(tage, int) or not config.AUFBEWAHRUNG_MIN_TAGE <= tage <= config.AUFBEWAHRUNG_MAX_TAGE):
        return jsonify({"error": "eingabe_ungueltig", "meldung": "Die Aufbewahrungsfrist muss zwischen %d und %d Tagen liegen." % (config.AUFBEWAHRUNG_MIN_TAGE, config.AUFBEWAHRUNG_MAX_TAGE),
                        "feld": "aufbewahrung_tage"}), 422
    dl = d.get("mitglied_download_link")
    if "mitglied_download_link" in d and not isinstance(dl, bool):
        return jsonify({"error": "eingabe_ungueltig", "meldung": MELDUNGEN["eingabe_ungueltig"], "feld": "mitglied_download_link"}), 422
    # Die Frist gilt für Vorgänge, die NACH der Änderung abgeschlossen werden; bereits abgeschlossene behalten ihre Frist (keine rückwirkende Verkürzung).
    with db.verbindung() as c:
        c.execute("INSERT INTO stelle_einstellung(mandant_ref, verantwortlicher, datenschutz_link, geaendert_am, geaendert_von, aufbewahrung_tage, mitglied_download_link) VALUES (?,?,?,?,?,?,?) "
                  "ON CONFLICT(mandant_ref) DO UPDATE SET verantwortlicher=excluded.verantwortlicher, datenschutz_link=excluded.datenschutz_link, "
                  "geaendert_am=excluded.geaendert_am, geaendert_von=excluded.geaendert_von, "
                  "aufbewahrung_tage=COALESCE(?, stelle_einstellung.aufbewahrung_tage), mitglied_download_link=COALESCE(?, stelle_einstellung.mitglied_download_link)",
                  (mandant, v.strip(), link.strip(), db.jetzt(), g.nutzer["email"], tage, 1 if dl is None else int(dl),
                   tage, None if dl is None else int(dl)))
    audit("einstellungen_gesetzt", "")
    return jsonify({"gesetzt": True})


@bp.put("/vorgaenge/<int:vorgang_id>/erinnerungen")
@anmeldung_pflicht()
def erinnerungen_schalten(vorgang_id):
    """Erinnerungen an das Mitglied für diesen Vorgang aus- oder einschalten. Body: {"an": true|false}."""
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or set(d) != {"an"} or not isinstance(d["an"], bool):
        return jsonify({"error": "eingabe_ungueltig", "meldung": MELDUNGEN["eingabe_ungueltig"]}), 422
    mandant = g.nutzer["mandant_ref"]
    v = db.vorgang_lesen(mandant, vorgang_id)
    if v is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    if v["status"] not in ("versendet", "versand_unklar"):
        return jsonify({"error": "zustand_passt_nicht"}), 409
    db.vorgang_aendern(mandant, vorgang_id, erinnerungen_aus=0 if d["an"] else 1)
    audit("erinnerungen_an" if d["an"] else "erinnerungen_aus", "id=%d" % vorgang_id)
    return jsonify({"vorgang_id": vorgang_id, "erinnerungen_aus": not d["an"]})


@bp.get("/tarif")
@anmeldung_pflicht()
def tarif_lesen():
    """Tarif und Zählstand der Beratungsstelle (Kalendermonat, deutsche Zeit). Ohne Preis: Tarife und Preise setzt nur der Betreiber."""
    return jsonify(tarif.info(g.nutzer["mandant_ref"]))
