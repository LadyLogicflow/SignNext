"""Download des unterschriebenen Dokuments.

- Mitglied: Link ``/d#<token>`` (das Token steht im Fragment und erreicht den Server nur im Body von POST /api/v1/download). 7 Tage gültig, höchstens 3 Abrufe,
  jeder Abruf wird protokolliert (gekürzte IP) und löst eine Info an die Beratungsstelle aus (jobs.py). Unbekannte, abgelaufene, aufgebrauchte oder gesperrte Links
  antworten gleich (404 nicht_verfuegbar). Antworten sind nie zwischenspeicherbar und nicht für Suchmaschinen bestimmt.
- Beratungsstelle (angemeldet): unbegrenzt, protokolliert im Audit; Dokument oder Nachweisprotokoll."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, g, jsonify, request

import config
import db
import schluessel
from portal_auth import anmeldung_pflicht, audit, client_ip, kuerze_ip
from zugang import IP_FENSTER_MINUTEN, IP_LIMIT, _origin_pruefen

bp = Blueprint("download", __name__, url_prefix="/api/v1")
NICHT_VERFUEGBAR = ({"error": "nicht_verfuegbar", "meldung": "Dieser Link ist nicht mehr gültig. Bitte wenden Sie sich an Ihre Beratungsstelle."}, 404)


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


def _pdf_antwort(daten: bytes, dateiname: str) -> Response:
    r = Response(daten, mimetype="application/pdf")
    r.headers["Content-Disposition"] = 'attachment; filename="%s"' % dateiname
    r.headers["Cache-Control"] = "no-store, private"
    r.headers["Pragma"] = "no-cache"
    r.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


@bp.post("/download")
def mitglied_download():
    fehler = _origin_pruefen()
    if fehler:
        return fehler
    d = request.get_json(silent=True)
    token = d.get("token") if isinstance(d, dict) and set(d) == {"token"} else None
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        return jsonify(NICHT_VERFUEGBAR[0]), 404
    jetzt = datetime.now(timezone.utc)
    iph = schluessel.ip_hmac(client_ip())
    th = schluessel.token_hash(token)
    with db.verbindung() as c:
        c.execute("DELETE FROM zugang_ip WHERE zeit < ?", (_zeit(jetzt - timedelta(hours=24)),))
        n = c.execute("SELECT COUNT(*) FROM zugang_ip WHERE ip_hmac=? AND zeit>=?", (iph, _zeit(jetzt - timedelta(minutes=IP_FENSTER_MINUTEN)))).fetchone()[0]
        c.execute("INSERT INTO zugang_ip(ip_hmac, zeit) VALUES (?,?)", (iph, _zeit(jetzt)))
        if n >= IP_LIMIT:
            return jsonify({"error": "zu_viele_versuche", "meldung": "Zu viele Versuche. Bitte später erneut versuchen."}), 429
        link = c.execute("SELECT l.*, v.status AS vstatus FROM download_link l JOIN vorgang v ON v.id=l.vorgang_id WHERE l.token_hash=?", (th,)).fetchone()
        if link is None or link["gesperrt"] or link["laeuft_ab"] <= _zeit(jetzt) or link["abrufe"] >= link["max_abrufe"] or link["vstatus"] != "abgeschlossen":
            return jsonify(NICHT_VERFUEGBAR[0]), 404
        daten = db.dokument_bytes(link["mandant_ref"], link["vorgang_id"], "S")
        if daten is None:
            return jsonify(NICHT_VERFUEGBAR[0]), 404
        # atomar zählen: zwei gleichzeitige Abrufe können das Limit nicht überschreiten
        if c.execute("UPDATE download_link SET abrufe=abrufe+1 WHERE id=? AND abrufe<max_abrufe AND gesperrt=0", (link["id"],)).rowcount != 1:
            return jsonify(NICHT_VERFUEGBAR[0]), 404
        zaehler = link["abrufe"] + 1
        c.execute("INSERT INTO download_log(link_id, vorgang_id, mandant_ref, zeit, ip, zaehler) VALUES (?,?,?,?,?,?)",
                  (link["id"], link["vorgang_id"], link["mandant_ref"], db.jetzt(), kuerze_ip(client_ip()), zaehler))
        db.audit_in(c, link["mandant_ref"], "mitglied", "dokument_abgerufen", "vorgang=%d abruf=%d" % (link["vorgang_id"], zaehler))
    return _pdf_antwort(daten, "dokument.pdf")


@bp.get("/vorgaenge/<int:vorgang_id>/download/<art>")
@anmeldung_pflicht()
def stelle_download(vorgang_id, art):
    """Unterschriebenes Dokument ('dokument') oder Nachweisprotokoll ('nachweis'), unbegrenzt, nur eigener Mandant."""
    mandant = g.nutzer["mandant_ref"]
    rolle = {"dokument": "S", "nachweis": "N"}.get(art)
    v = db.vorgang_lesen(mandant, vorgang_id)
    if rolle is None or v is None or v["status"] != "abgeschlossen":
        return jsonify({"error": "nicht_gefunden"}), 404
    daten = db.dokument_bytes(mandant, vorgang_id, rolle)
    if daten is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    audit("dokument_heruntergeladen", "id=%d art=%s" % (vorgang_id, art))
    return _pdf_antwort(daten, "vorgang-%d-%s.pdf" % (vorgang_id, art))
