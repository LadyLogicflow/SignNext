"""Download des unterschriebenen Dokuments.

- Mitglied: Link ``/d#<token>`` (das Token steht im Fragment und erreicht den Server nur im Body von POST /api/v1/download). 7 Tage gültig, höchstens 3 Abrufe,
  jeder Abruf wird protokolliert (gekürzte IP) und löst eine Info an die Beratungsstelle aus (jobs.py). Zweiter Faktor: das Geburtsdatum (ttmmjjjj) wie beim Öffnen;
  nach 5 falschen Codes ist der Link dauerhaft gesperrt. Unbekannte, abgelaufene, aufgebrauchte oder gesperrte Links und falsche Codes antworten gleich (401
  angaben_stimmen_nicht mit versuch/von/gesperrt). Antworten sind nie zwischenspeicherbar und nicht für Suchmaschinen bestimmt.
- Beratungsstelle (angemeldet): unbegrenzt, protokolliert im Audit; Dokument oder Nachweisprotokoll."""
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, g, jsonify, request

import config
import db
import dokumente
import schluessel
from portal_auth import anmeldung_pflicht, audit, client_ip, kuerze_ip
from zugang import IP_FENSTER_MINUTEN, IP_LIMIT, _origin_pruefen

bp = Blueprint("download", __name__, url_prefix="/api/v1")


def _zeit(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def link_erzeugen(c, mandant_ref: str, vorgang_id: int) -> str:
    """Neuer Download-Link (alte Links des Vorgangs werden gesperrt). Liefert das Token; nur der SHA-256 wird gespeichert."""
    token, th = schluessel.neues_token()
    jetzt = datetime.now(timezone.utc)
    c.execute("UPDATE download_link SET gesperrt=1 WHERE vorgang_id=? AND mandant_ref=?", (int(vorgang_id), mandant_ref))
    c.execute("INSERT INTO download_link(vorgang_id, mandant_ref, token_hash, erstellt, laeuft_ab, max_abrufe) VALUES (?,?,?,?,?,?)",
              (int(vorgang_id), mandant_ref, th, _zeit(jetzt), _zeit(jetzt + timedelta(days=config.DOWNLOAD_TAGE)), config.DOWNLOAD_MAX_ABRUFE))
    return token


_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def _content_disposition(dateiname: str) -> str:
    """Nur ASCII im einfachen ``filename`` (Ersatz, Umlaute ausgeschrieben), der echte Name mit Umlauten steht in ``filename*`` (RFC 5987, UTF-8)."""
    import re
    import unicodedata
    from urllib.parse import quote
    ascii_name = unicodedata.normalize("NFKD", dateiname.translate(_UMLAUTE)).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", ascii_name)
    return 'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (ascii_name, quote(dateiname, safe=""))


def _pdf_antwort(daten: bytes, dateiname: str) -> Response:
    r = Response(daten, mimetype="application/pdf")
    r.headers["Content-Disposition"] = _content_disposition(dateiname)
    r.headers["Cache-Control"] = "no-store, private"
    r.headers["Pragma"] = "no-cache"
    r.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


MAX_VERSUCHE = 5


def _falsch(versuche: int, gesperrt: bool):
    """Einheitliche Antwort für alles, was nicht zum PDF führt (falscher Code, unbekannt, abgelaufen, gesperrt, aufgebraucht): dieselbe Form wie beim Öffnen des Dokuments."""
    return jsonify({"error": "angaben_stimmen_nicht", "meldung": "Angaben stimmen nicht.", "versuch": min(versuche, MAX_VERSUCHE), "von": MAX_VERSUCHE, "gesperrt": bool(gesperrt)}), 401


def _abrufen_intern(token: str, code: str, dokument=None) -> dict:
    """{'art': 'ok', 'daten', 'zaehler', 'max'} | {'art': 'auswahl', 'dokumente'} (richtiger Code, aber bei mehreren Dokumenten fehlt die Wahl: zählt keinen Abruf) | {'art': 'falsch', 'versuche', 'gesperrt'} | {'art': 'ratelimit'}. Eine Transaktion (atomar)."""
    jetzt = datetime.now(timezone.utc)
    iph = schluessel.ip_hmac(client_ip())
    th = schluessel.token_hash(token)
    format_ok = bool(re.fullmatch(r"\d{8}", code))
    with db.verbindung() as c:
        c.execute("DELETE FROM zugang_ip WHERE zeit < ?", (_zeit(jetzt - timedelta(hours=24)),))
        c.execute("DELETE FROM zugang_unbekannt WHERE zeit < ?", (_zeit(jetzt - timedelta(hours=24)),))
        n = c.execute("SELECT COUNT(*) FROM zugang_ip WHERE ip_hmac=? AND zeit>=?", (iph, _zeit(jetzt - timedelta(minutes=IP_FENSTER_MINUTEN)))).fetchone()[0]
        c.execute("INSERT INTO zugang_ip(ip_hmac, zeit) VALUES (?,?)", (iph, _zeit(jetzt)))
        if n >= IP_LIMIT:
            return {"art": "ratelimit"}
        link = c.execute("SELECT l.*, v.status AS vstatus, v.code_hmac AS vcode, v.code_kid AS vkid FROM download_link l JOIN vorgang v ON v.id=l.vorgang_id WHERE l.token_hash=?", (th,)).fetchone()
        if link is None:
            # Unbekanntes Token: gleiche Zustandsmaschine, gleiche Rechenarbeit, gleiche Antwort.
            schluessel.code_pruefen("0", schluessel.kid(schluessel.aktuell()), "0" * 64, code if format_ok else "00000000")
            z = c.execute("SELECT versuche FROM zugang_unbekannt WHERE token_hash=?", (th,)).fetchone()
            versuche = z["versuche"] if z else 0
            if versuche >= MAX_VERSUCHE:
                return {"art": "falsch", "versuche": MAX_VERSUCHE, "gesperrt": True}
            versuche += 1
            c.execute("INSERT INTO zugang_unbekannt(token_hash, versuche, zeit) VALUES (?,?,?) ON CONFLICT(token_hash) DO UPDATE SET versuche=excluded.versuche, zeit=excluded.zeit", (th, versuche, _zeit(jetzt)))
            return {"art": "falsch", "versuche": versuche, "gesperrt": versuche >= MAX_VERSUCHE}
        link = dict(link)
        docs = dokumente.rollen(link["mandant_ref"], link["vorgang_id"]) if link["vstatus"] == "abgeschlossen" else []
        if link["gesperrt"] or link["laeuft_ab"] <= _zeit(jetzt) or link["abrufe"] >= link["max_abrufe"] or not docs:
            schluessel.code_pruefen(link["mandant_ref"], link["vkid"] or schluessel.kid(schluessel.aktuell()), link["vcode"] or "0" * 64, code if format_ok else "00000000")
            return {"art": "falsch", "versuche": MAX_VERSUCHE, "gesperrt": True}
        if format_ok and schluessel.code_pruefen(link["mandant_ref"], link["vkid"], link["vcode"], code):
            wahl = dokument if dokument in docs else (docs[0] if len(docs) == 1 and dokument is None else None)
            if wahl is None:
                return {"art": "auswahl", "dokumente": [{"rolle": r, "titel": dokumente.TITEL[r]} for r in docs]}
            daten = db.dokument_bytes(link["mandant_ref"], link["vorgang_id"], dokumente.ergebnis_rolle("dokument", wahl))
            if daten is None:
                return {"art": "falsch", "versuche": MAX_VERSUCHE, "gesperrt": True}
            # atomar zählen: zwei gleichzeitige Abrufe können das Limit nicht überschreiten
            if c.execute("UPDATE download_link SET abrufe=abrufe+1 WHERE id=? AND abrufe<max_abrufe AND gesperrt=0", (link["id"],)).rowcount != 1:
                return {"art": "falsch", "versuche": MAX_VERSUCHE, "gesperrt": True}
            zaehler = link["abrufe"] + 1
            c.execute("INSERT INTO download_log(link_id, vorgang_id, mandant_ref, zeit, ip, zaehler) VALUES (?,?,?,?,?,?)",
                      (link["id"], link["vorgang_id"], link["mandant_ref"], db.jetzt(), kuerze_ip(client_ip()), zaehler))
            db.audit_in(c, link["mandant_ref"], "mitglied", "dokument_abgerufen", "vorgang=%d abruf=%d dokument=%s" % (link["vorgang_id"], zaehler, wahl))
            return {"art": "ok", "daten": daten, "zaehler": zaehler, "max": link["max_abrufe"]}
        versuche = link["fehlversuche"] + 1
        gesperrt = versuche >= MAX_VERSUCHE
        c.execute("UPDATE download_link SET fehlversuche=?, gesperrt=? WHERE id=?", (versuche, 1 if gesperrt else 0, link["id"]))
        db.audit_in(c, link["mandant_ref"], "mitglied", "download_link_gesperrt" if gesperrt else "download_code_falsch", "vorgang=%d" % link["vorgang_id"])
        return {"art": "falsch", "versuche": versuche, "gesperrt": gesperrt}


@bp.post("/download")
def mitglied_download():
    """Body {token, code}: ``code`` = Geburtsdatum wie beim Öffnen (acht Ziffern ttmmjjjj). Ohne richtigen Code kein Abruf und kein PDF."""
    fehler = _origin_pruefen()
    if fehler:
        return fehler
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or set(daten) - {"token", "code", "dokument"}:
        return jsonify({"error": "eingabe_ungueltig"}), 400
    token = daten.get("token") if isinstance(daten.get("token"), str) else ""
    code = daten.get("code") if isinstance(daten.get("code"), str) else ""
    dokument = daten.get("dokument") if isinstance(daten.get("dokument"), str) else None
    try:
        e = _abrufen_intern(token[:256], code[:32], dokument)
    except schluessel.HauptschluesselFehlt:
        return jsonify({"error": "nicht_bereit"}), 503
    if e["art"] == "ratelimit":
        return jsonify({"error": "zu_viele_versuche", "meldung": "Zu viele Versuche. Bitte später erneut versuchen."}), 429
    if e["art"] == "falsch":
        return _falsch(e["versuche"], e["gesperrt"])
    if e["art"] == "auswahl":
        return jsonify({"dokumente": e["dokumente"]})
    antwort = _pdf_antwort(e["daten"], "dokument.pdf")
    antwort.headers["X-Abruf"] = str(e["zaehler"])                                  # nur Zahlen: "Abruf x von y" für die Seite
    antwort.headers["X-Abruf-Max"] = str(e["max"])
    return antwort


@bp.get("/vorgaenge/<int:vorgang_id>/download/<art>")
@anmeldung_pflicht()
def stelle_download(vorgang_id, art):
    """Unterschriebenes Dokument ('dokument') oder Nachweisprotokoll ('nachweis'), unbegrenzt, nur eigener Mandant."""
    mandant = g.nutzer["mandant_ref"]
    v = db.vorgang_lesen(mandant, vorgang_id)
    if art not in ("dokument", "nachweis") or v is None or v["status"] != "abgeschlossen":
        return jsonify({"error": "nicht_gefunden"}), 404
    docs = dokumente.rollen(mandant, vorgang_id)
    angabe = request.args.get("dokument")
    if angabe is None and len(docs) > 1:
        return jsonify({"error": "dokument_waehlen", "meldung": "Bitte das Dokument wählen.", "dokumente": [{"rolle": r, "titel": dokumente.TITEL[r]} for r in docs]}), 400
    rolle = docs[0] if angabe is None and docs else angabe
    if rolle not in docs:
        return jsonify({"error": "nicht_gefunden"}), 404
    daten = db.dokument_bytes(mandant, vorgang_id, dokumente.ergebnis_rolle(art, rolle))
    if daten is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    db.abholung_vermerken(mandant, vorgang_id, rolle, art)                          # Ampel: nur was tatsächlich ausgeliefert wird, zählt als abgeholt
    audit("dokument_heruntergeladen", "id=%d art=%s dokument=%s" % (vorgang_id, art, rolle))
    mnr, nachname = db.dateiname_quellen(mandant, vorgang_id)
    dok_name, nachweis_name = dokumente.dateinamen(vorgang_id, rolle, len(docs), mnr, nachname)
    return _pdf_antwort(daten, dok_name if art == "dokument" else nachweis_name)
