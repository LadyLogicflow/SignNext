"""Zugang des Mitglieds zur Unterschriftsseite (ohne Portal-Cookie): Code-Prüfung, Sperre, Sitzung, Schrittfolge, Unterschrift.

Alles Sicherheitsrelevante entscheidet der Server, nicht die Seite:
- Code ttmmjjjj gegen den HMAC (konstantzeitig). Jedes vorgelegte Token, auch ein unbekanntes, durchläuft dieselbe Zustandsmaschine mit Fehlversuchszähler:
  Antwort immer {"error": "angaben_stimmen_nicht", "versuch": x, "von": 5, "gesperrt": bool}; abgelaufene, verbrauchte und gesperrte Links antworten wie "gesperrt".
  Nach 5 Fehlversuchen ist ein Link DAUERHAFT gesperrt (nichts setzt den Zähler zurück). Grobe Begrenzung je IP (nur HMAC der Adresse).
- Sitzung: 256-Bit-Zufallswert im Cookie (HttpOnly, Secure, SameSite=Strict), nur der Hash gespeichert, 60 Minuten, an genau einen Link/Vorgang gebunden.
- Schrittfolge: alle Seiten des echten Dokuments ausgeliefert → "vollständig gelesen" → Texte (Datenschutzinformation) ausgeliefert → Unterschrift mit Haken und eigenem Klick,
  je Person nacheinander. Seitenbilder gibt es nur innerhalb der Sitzung, nie mit Token in der Adresse, nie zwischengespeichert."""
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request

import abschluss
import config
import db
import schluessel
import seitenbild
import texte
import unterschrift_bild
from portal_auth import client_ip, kuerze_ip

bp = Blueprint("zugang", __name__, url_prefix="/api/v1/zugang")
COOKIE = "sn_zugang"
MAX_VERSUCHE = 5
SITZUNG_MINUTEN = 60
IP_FENSTER_MINUTEN = 10
IP_LIMIT = 30
LINK_STATI = ("versendet", "versand_unklar")


def _zeit(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _json(daten, status=200):
    return jsonify(daten), status


def _falsch(versuche: int, gesperrt: bool):
    return _json({"error": "angaben_stimmen_nicht", "meldung": "Angaben stimmen nicht.", "versuch": min(versuche, MAX_VERSUCHE), "von": MAX_VERSUCHE,
                  "gesperrt": bool(gesperrt)}, 401)


def _origin_pruefen():
    basis = config.basis_url()
    if basis is None:
        return _json({"error": "nicht_bereit"}, 503)
    t = urlsplit(basis)
    if request.headers.get("Origin") != "%s://%s" % (t.scheme, t.netloc):
        return _json({"error": "origin_nicht_erlaubt"}, 403)
    return None


def geraet_kennung(ua: str) -> str:
    """Nur Browser-Familie und Betriebssystem, nie der volle User-Agent."""
    ua = ua or ""
    if re.search(r"Edg/|EdgA/|EdgiOS/", ua):
        browser = "Edge"
    elif "SamsungBrowser" in ua:
        browser = "Samsung Internet"
    elif re.search(r"Firefox/|FxiOS/", ua):
        browser = "Firefox"
    elif re.search(r"Chrome/|CriOS/", ua):
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Sonstiger"
    if re.search(r"iPhone|iPad|iPod", ua):
        os_ = "iOS"
    elif "Android" in ua:
        os_ = "Android"
    elif "Windows" in ua:
        os_ = "Windows"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        os_ = "macOS"
    elif "Linux" in ua or "X11" in ua:
        os_ = "Linux"
    else:
        os_ = "Sonstiges"
    return "%s/%s" % (browser, os_)


def _audit(c, mandant_ref: str, aktion: str, details: str) -> None:
    """Audit-Eintrag in DERSELBEN Verbindung (eine zweite würde auf die offene Transaktion warten)."""
    c.execute("INSERT INTO audit_log(zeit, mandant_ref, nutzer, aktion, details, ip) VALUES (?,?,?,?,?,?)", (db.jetzt(), mandant_ref, "mitglied", aktion, details, ""))


def _protokoll(c, link: dict, ergebnis: str) -> None:
    c.execute("INSERT INTO zugriff_log(link_id, mandant_ref, zeit, ip, ergebnis) VALUES (?,?,?,?,?)",
              (link["id"], link["mandant_ref"], db.jetzt(), kuerze_ip(client_ip()), ergebnis))


# ------------------------------------------------------------------------------------------------ Code-Prüfung

def _pruefen_intern(token: str, code: str) -> dict:
    """Ergebnis: {'art': 'ok', 'cookie': wert} | {'art': 'falsch', 'versuche': n, 'gesperrt': bool} | {'art': 'ratelimit'}. Alles in einer Transaktion (atomar)."""
    jetzt = _jetzt()
    th = schluessel.token_hash(token)
    iph = schluessel.ip_hmac(client_ip())
    format_ok = bool(re.fullmatch(r"\d{8}", code))
    with db.verbindung() as c:
        alt = _zeit(jetzt - timedelta(hours=24))
        c.execute("DELETE FROM zugang_ip WHERE zeit < ?", (alt,))
        c.execute("DELETE FROM zugang_unbekannt WHERE zeit < ?", (alt,))
        n = c.execute("SELECT COUNT(*) FROM zugang_ip WHERE ip_hmac=? AND zeit>=?", (iph, _zeit(jetzt - timedelta(minutes=IP_FENSTER_MINUTEN)))).fetchone()[0]
        c.execute("INSERT INTO zugang_ip(ip_hmac, zeit) VALUES (?,?)", (iph, _zeit(jetzt)))
        if n >= IP_LIMIT:
            return {"art": "ratelimit"}
        link = c.execute("SELECT l.*, v.status AS vstatus, v.code_hmac AS vcode, v.code_kid AS vkid FROM link l JOIN vorgang v ON v.id=l.vorgang_id WHERE l.token_hash=?", (th,)).fetchone()
        if link is None:
            # Unbekanntes Token: gleiche Zustandsmaschine, gleiche Rechenarbeit, gleiche Antwort.
            schluessel.code_pruefen("0", schluessel.kid(schluessel.aktuell()), "0" * 64, code if format_ok else "00000000")
            z = c.execute("SELECT versuche FROM zugang_unbekannt WHERE token_hash=?", (th,)).fetchone()
            versuche = z["versuche"] if z else 0
            if versuche >= MAX_VERSUCHE:
                return {"art": "falsch", "versuche": MAX_VERSUCHE, "gesperrt": True}
            versuche += 1
            c.execute("INSERT INTO zugang_unbekannt(token_hash, versuche, zeit) VALUES (?,?,?) ON CONFLICT(token_hash) DO UPDATE SET versuche=excluded.versuche, zeit=excluded.zeit",
                      (th, versuche, _zeit(jetzt)))
            return {"art": "falsch", "versuche": versuche, "gesperrt": versuche >= MAX_VERSUCHE}
        link = dict(link)
        if link["verbraucht_am"]:
            tot = "verbraucht"
        elif link["gesperrt"]:
            tot = "gesperrt"
        elif link["laeuft_ab"] <= _zeit(jetzt):
            tot = "abgelaufen"
        elif link["vstatus"] not in LINK_STATI:
            tot = "verbraucht"
        else:
            tot = None
        if tot:
            schluessel.code_pruefen(link["mandant_ref"], link["vkid"] or schluessel.kid(schluessel.aktuell()), link["vcode"] or "0" * 64, code if format_ok else "00000000")
            _protokoll(c, link, tot)
            return {"art": "falsch", "versuche": MAX_VERSUCHE, "gesperrt": True}
        ok = format_ok and schluessel.code_pruefen(link["mandant_ref"], link["vkid"], link["vcode"], code)
        if ok:
            cookie = secrets.token_urlsafe(32)
            c.execute("INSERT INTO sitzung(id_hash, link_id, vorgang_id, mandant_ref, erstellt, laeuft_ab) VALUES (?,?,?,?,?,?)",
                      (schluessel.token_hash(cookie), link["id"], link["vorgang_id"], link["mandant_ref"], _zeit(jetzt), _zeit(jetzt + timedelta(minutes=SITZUNG_MINUTEN))))
            _protokoll(c, link, "ok")
            return {"art": "ok", "cookie": cookie}
        versuche = link["fehlversuche"] + 1
        gesperrt = versuche >= MAX_VERSUCHE
        c.execute("UPDATE link SET fehlversuche=?, gesperrt=? WHERE id=?", (versuche, 1 if gesperrt else 0, link["id"]))
        _protokoll(c, link, "gesperrt" if gesperrt else "falsch")
        if gesperrt:
            db.token_entziehen(c, link_id=link["id"])
            _audit(c, link["mandant_ref"], "link_gesperrt", "vorgang=%d" % link["vorgang_id"])
        return {"art": "falsch", "versuche": versuche, "gesperrt": gesperrt}


@bp.get("/hinweis")
def hinweis():
    """Hinweistext vor der Eingabe des Geburtsdatums (ohne Daten einer Beratungsstelle)."""
    return _json({"kennung": texte.kennung(), "freigegeben": texte.freigegeben(), "zugangshinweis": texte.zugangshinweis()})


@bp.post("/pruefen")
def pruefen():
    fehler = _origin_pruefen()
    if fehler:
        return fehler
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or set(daten) - {"token", "code"}:
        return _json({"error": "eingabe_ungueltig"}, 400)
    token = daten.get("token") if isinstance(daten.get("token"), str) else ""
    code = daten.get("code") if isinstance(daten.get("code"), str) else ""
    try:
        e = _pruefen_intern(token[:256], code[:32])
    except schluessel.HauptschluesselFehlt:
        return _json({"error": "nicht_bereit"}, 503)
    if e["art"] == "ratelimit":
        return _json({"error": "zu_viele_versuche", "meldung": "Zu viele Versuche. Bitte später erneut versuchen."}, 429)
    if e["art"] == "falsch":
        return _falsch(e["versuche"], e["gesperrt"])
    antwort, status = _json({"ok": True, "sitzung_minuten": SITZUNG_MINUTEN})
    antwort.set_cookie(COOKIE, e["cookie"], max_age=SITZUNG_MINUTEN * 60, httponly=True, secure=True, samesite="Strict", path="/api/v1/zugang")
    return antwort, status


# ------------------------------------------------------------------------------------------------ Sitzung und Schritte

def _sitzung():
    wert = request.cookies.get(COOKIE, "")
    if not wert:
        return None
    with db.verbindung() as c:
        s = c.execute("SELECT s.*, l.gesperrt AS l_gesperrt, l.verbraucht_am AS l_verbraucht, l.laeuft_ab AS l_ab, v.status AS vstatus, v.personen AS personen "
                      "FROM sitzung s JOIN link l ON l.id=s.link_id JOIN vorgang v ON v.id=s.vorgang_id WHERE s.id_hash=?", (schluessel.token_hash(wert),)).fetchone()
    if s is None:
        return None
    s = dict(s)
    jetzt = _zeit(_jetzt())
    if s["laeuft_ab"] <= jetzt or s["l_ab"] <= jetzt or s["l_gesperrt"] or s["l_verbraucht"] or s["vstatus"] not in LINK_STATI or s["personen"] not in (1, 2):
        return None
    s["seiten_geliefert"] = json.loads(s["seiten_geliefert"])
    return s


def _ohne_sitzung():
    return _json({"error": "sitzung_ungueltig", "meldung": "Bitte öffnen Sie den Link aus der E-Mail erneut."}, 401)


def _dokument(s: dict):
    """(Rolle, Seitenzahl) des Dokuments, das unterschrieben wird: das zusammengeführte PDF, sonst das einzelne."""
    zeilen = {d["rolle"]: d for d in db.dokumente_liste(s["mandant_ref"], s["vorgang_id"])}
    for rolle in ("M", "B", "A"):
        if rolle in zeilen:
            return rolle, zeilen[rolle]["seiten"]
    return None, 0


def _unterschriebene(c, s: dict) -> list:
    return [z["person"] for z in c.execute("SELECT person FROM unterschrift WHERE vorgang_id=? AND mandant_ref=? ORDER BY person", (s["vorgang_id"], s["mandant_ref"]))]


@bp.get("/status")
def status():
    s = _sitzung()
    if s is None:
        return _ohne_sitzung()
    _, seiten = _dokument(s)
    with db.verbindung() as c:
        fertig = _unterschriebene(c, s)
    schritt = "lesen" if not s["gelesen_am"] else ("texte" if not s["texte_angezeigt_am"] else "unterschreiben")
    return _json({"schritt": schritt, "seiten": seiten, "geliefert": len(s["seiten_geliefert"]), "personen": s["personen"], "unterschrieben": fertig,
                  "naechste_person": len(fertig) + 1 if len(fertig) < s["personen"] else None})


@bp.get("/seite/<int:nr>")
def seite(nr):
    s = _sitzung()
    if s is None:
        return _ohne_sitzung()
    rolle, seiten = _dokument(s)
    if rolle is None or not 1 <= nr <= seiten:
        return _json({"error": "nicht_gefunden"}, 404)
    daten = db.dokument_bytes(s["mandant_ref"], s["vorgang_id"], rolle)
    if daten is None:
        return _json({"error": "nicht_gefunden"}, 404)
    try:
        bild = seitenbild.rendern(daten, nr)
    except seitenbild.Fehler:
        return _json({"error": "seite_nicht_verfuegbar"}, 503)
    with db.verbindung() as c:
        z = c.execute("SELECT seiten_geliefert FROM sitzung WHERE id=?", (s["id"],)).fetchone()
        geliefert = sorted(set(json.loads(z["seiten_geliefert"])) | {nr})
        c.execute("UPDATE sitzung SET seiten_geliefert=? WHERE id=?", (json.dumps(geliefert), s["id"]))
    antwort = request_bild(bild)
    return antwort


def request_bild(bild: bytes):
    from flask import Response
    antwort = Response(bild, mimetype="image/jpeg")
    antwort.headers["Cache-Control"] = "no-store, private"
    antwort.headers["Content-Disposition"] = 'inline; filename="seite.jpg"'
    return antwort


@bp.post("/gelesen")
def gelesen():
    fehler = _origin_pruefen()
    if fehler:
        return fehler
    s = _sitzung()
    if s is None:
        return _ohne_sitzung()
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or daten.get("bestaetigt") is not True or set(daten) - {"bestaetigt"}:
        return _json({"error": "bestaetigung_fehlt", "meldung": "Bitte ausdrücklich bestätigen."}, 400)
    _, seiten = _dokument(s)
    if not set(range(1, seiten + 1)) <= set(s["seiten_geliefert"]):
        return _json({"error": "seiten_fehlen", "meldung": "Bitte zuerst alle Seiten des Dokuments ansehen."}, 409)
    with db.verbindung() as c:
        c.execute("UPDATE sitzung SET gelesen_am=COALESCE(gelesen_am, ?) WHERE id=?", (db.jetzt(), s["id"]))
    return _json({"ok": True})


@bp.get("/texte")
def texte_liefern():
    s = _sitzung()
    if s is None:
        return _ohne_sitzung()
    if not s["gelesen_am"]:
        return _json({"error": "lesen_fehlt", "meldung": "Bitte zuerst das Dokument lesen."}, 409)
    try:
        t = texte.laden(s["mandant_ref"])
    except texte.TexteFehlen:
        return _json({"error": "texte_fehlen", "meldung": "Die Unterschrift ist derzeit nicht möglich. Bitte wenden Sie sich an Ihre Beratungsstelle."}, 503)
    with db.verbindung() as c:
        c.execute("UPDATE sitzung SET texte_angezeigt_am=?, texte_kennung=?, texte_hash=? WHERE id=?", (db.jetzt(), t["kennung"], t["hash"], s["id"]))
    return _json(t)


@bp.post("/unterschrift")
def unterschrift():
    fehler = _origin_pruefen()
    if fehler:
        return fehler
    s = _sitzung()
    if s is None:
        return _ohne_sitzung()
    daten = request.get_json(silent=True)
    erlaubt = {"person", "bild", "ich_unterschreibe", "haken", "text_hash"}
    if not isinstance(daten, dict) or set(daten) - erlaubt or erlaubt - set(daten):
        return _json({"error": "eingabe_ungueltig", "meldung": "Die Eingabe ist ungültig."}, 400)
    if not s["gelesen_am"]:
        return _json({"error": "lesen_fehlt", "meldung": "Bitte zuerst das Dokument lesen."}, 409)
    if not s["texte_angezeigt_am"]:
        return _json({"error": "texte_fehlen", "meldung": "Bitte zuerst die Datenschutzinformation lesen."}, 409)
    if daten["haken"] is not True or daten["ich_unterschreibe"] is not True:
        return _json({"error": "zustimmung_fehlt", "meldung": "Bitte Zustimmung und Unterschrift ausdrücklich bestätigen."}, 400)
    if not isinstance(daten["text_hash"], str) or daten["text_hash"] != s["texte_hash"]:
        return _json({"error": "text_geaendert", "meldung": "Die Texte haben sich geändert. Bitte lesen Sie sie erneut."}, 409)
    person = daten["person"]
    if isinstance(person, bool) or person not in (1, 2) or person > s["personen"]:
        return _json({"error": "person_ungueltig"}, 400)
    try:
        png = unterschrift_bild.pruefen(daten["bild"])
    except unterschrift_bild.Ungueltig as u:
        return _json({"error": u.code, "meldung": "Das Unterschriftsbild ist nicht verwendbar."}, 422)
    ip = client_ip() if config.ip_speicherung() == "voll" else kuerze_ip(client_ip())
    jetzt = db.jetzt()
    with db.verbindung() as c:
        fertig = _unterschriebene(c, s)
        if person in fertig:
            return _json({"error": "schon_unterschrieben"}, 409)
        if person != len(fertig) + 1:
            return _json({"error": "reihenfolge", "meldung": "Bitte der Reihe nach unterschreiben."}, 409)
        c.execute("INSERT INTO unterschrift(vorgang_id, mandant_ref, person, bild, bild_sha256, zeit_texte_angezeigt, zeit_unterschrift, text_kennung, text_hash, haken, klick, ip, geraet) "
                  "VALUES (?,?,?,?,?,?,?,?,?,1,1,?,?)",
                  (s["vorgang_id"], s["mandant_ref"], person, png, hashlib.sha256(png).hexdigest(), s["texte_angezeigt_am"], jetzt, s["texte_kennung"], s["texte_hash"], ip,
                   geraet_kennung(request.headers.get("User-Agent", ""))))
        if person == s["personen"]:
            c.execute("UPDATE vorgang SET status='unterschrieben', unterschrieben_am=? WHERE id=? AND mandant_ref=?", (jetzt, s["vorgang_id"], s["mandant_ref"]))
            c.execute("UPDATE link SET verbraucht_am=? WHERE id=?", (jetzt, s["link_id"]))
            db.token_entziehen(c, vorgang_id=s["vorgang_id"])      # wiederherstellbares Token sofort löschen (nur der Hash bleibt)
            c.execute("DELETE FROM sitzung WHERE link_id=?", (s["link_id"],))
            c.execute("DELETE FROM erinnerung WHERE vorgang_id=? AND mandant_ref=? AND gesendet_am IS NULL", (s["vorgang_id"], s["mandant_ref"]))
            _audit(c, s["mandant_ref"], "unterschrieben", "vorgang=%d" % s["vorgang_id"])
            letzte = True
        else:
            # nächste Person: Haken und Text müssen für sie erneut bestätigt werden
            c.execute("UPDATE sitzung SET texte_angezeigt_am=NULL, texte_kennung=NULL, texte_hash=NULL WHERE id=?", (s["id"],))
            letzte = False
    if letzte:
        # Nach dem Commit: Einbetten und Nachweisprotokoll. Scheitert das, bleibt der Vorgang 'unterschrieben' und der Hintergrund-Job holt es nach.
        try:
            abschluss.ausfuehren(s["mandant_ref"], s["vorgang_id"])
        except Exception:
            logging.getLogger("signnext").error("Abschluss nach Unterschrift fehlgeschlagen (Job holt nach): vorgang=%d", s["vorgang_id"])
        antwort, st = _json({"fertig": True})
        antwort.delete_cookie(COOKIE, path="/api/v1/zugang")
        return antwort, st
    return _json({"fertig": False, "naechste_person": person + 1})


@bp.post("/abmelden")
def abmelden():
    fehler = _origin_pruefen()
    if fehler:
        return fehler
    wert = request.cookies.get(COOKIE, "")
    if wert:
        with db.verbindung() as c:
            c.execute("DELETE FROM sitzung WHERE id_hash=?", (schluessel.token_hash(wert),))
    antwort, st = _json({"ok": True})
    antwort.delete_cookie(COOKIE, path="/api/v1/zugang")
    return antwort, st
