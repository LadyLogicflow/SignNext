"""Handy-Übergabe der Unterschrift per QR (v0.7.0, catrin #19322/#19332): Der Computer des Mitglieds zeigt einen QR-Code, das Handy öffnet /h, das Mitglied zeichnet dort,
das Bild erscheint am Computer und wird dort wie bisher mit Haken und Klick bestätigt und abgeschickt.

Sicherheit und Datenschutz (alles entscheidet der Server):
- Token: 256 Bit zufällig, nur sein SHA-256 steht in der Datenbank, im URL-Fragment (nie in Server-Logs). Besitz des Tokens = Recht, GENAU dieses Zeichenfeld zu füllen.
- Bindung: Vorgang + Person + Sitzung des Computers (Cookie sn_zugang). Nur der Computer mit dieser Sitzung sieht Status und holt das Bild ab; der Geburtsdatum-Code bleibt am Computer.
- Bild: wie bei der normalen Unterschrift geprüft und neu kodiert (unterschrift_bild), nur VERSCHLÜSSELT in der Tabelle handoff, Lebensdauer 10 Minuten, EINMAL abholbar und
  danach sofort gelöscht; nie in Logs oder im Audit (dort nur „erzeugt/gezeichnet/abgeholt/abgelaufen" mit Vorgangsnummer).
- Begrenzung: höchstens 10 neue Übergaben je Vorgang und Stunde, höchstens 30 Handy-Anfragen je Adresse in 10 Minuten; ein aktiver Eintrag je Vorgang + Person (neu ersetzt alt).
- Unbekannte, abgelaufene, benutzte und gesperrte Token antworten gleich (404 link_ungueltig)."""
import base64
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import config
import db
import qr
import schluessel
import unterschrift_bild
from portal_auth import client_ip

TTL_SEKUNDEN = 600
ERZEUGEN_JE_STUNDE = 10
HANDY_LIMIT = 30
HANDY_FENSTER_SEKUNDEN = 600
ZWECK = "handoff-bild"
LINK_STATI = ("versendet", "versand_unklar")

bp = Blueprint("handoff", __name__, url_prefix="/api/v1/zugang/handoff")
bp_handy = Blueprint("handy", __name__, url_prefix="/api/v1/handy")

_ip_zeiten: dict = {}
_ip_sperre = threading.Lock()


def _zeit(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _json(daten, status=200):
    return jsonify(daten), status


def _ungueltig():
    return _json({"error": "link_ungueltig", "meldung": "Dieser Link gilt nicht mehr. Bitte am Computer einen neuen QR-Code anzeigen."}, 404)


def _audit(mandant_ref: str, aktion: str, vorgang_id: int) -> None:
    db.audit(mandant_ref, "", aktion, "vorgang=%d" % vorgang_id, "")


def aufraeumen(jetzt: datetime | None = None) -> int:
    """Löscht abgelaufene Übergaben samt Bild (Job und bei jedem Aufruf)."""
    jetzt = jetzt or _jetzt()
    with db.verbindung() as c:
        n = c.execute("DELETE FROM handoff WHERE laeuft_ab <= ?", (_zeit(jetzt),)).rowcount
        c.execute("DELETE FROM handoff_log WHERE zeit < ?", (_zeit(jetzt - timedelta(hours=2)),))
    return n


def _ip_begrenzt() -> bool:
    """True, wenn diese Adresse zu viele Handy-Anfragen gestellt hat (im Arbeitsspeicher, nur der HMAC der Adresse als Schlüssel)."""
    schluessel_ip = schluessel.ip_hmac(client_ip())
    jetzt = time.time()
    with _ip_sperre:
        zeiten = [t for t in _ip_zeiten.get(schluessel_ip, []) if jetzt - t < HANDY_FENSTER_SEKUNDEN]
        zeiten.append(jetzt)
        _ip_zeiten[schluessel_ip] = zeiten
        if len(_ip_zeiten) > 5000:                                   # Speicher begrenzen: alte Einträge fallen heraus
            for k in [k for k, v in _ip_zeiten.items() if not v or jetzt - v[-1] > HANDY_FENSTER_SEKUNDEN]:
                _ip_zeiten.pop(k, None)
        return len(zeiten) > HANDY_LIMIT


# ----- Hilfen -----

def _sitzung_zu(sitzung_id: int):
    """Die Computer-Sitzung, solange sie und der Link gültig sind (gleiche Regeln wie zugang._sitzung), sonst None."""
    with db.verbindung() as c:
        s = c.execute("SELECT s.*, l.gesperrt AS l_gesperrt, l.verbraucht_am AS l_verbraucht, l.laeuft_ab AS l_ab, v.status AS vstatus, v.personen AS personen "
                      "FROM sitzung s JOIN link l ON l.id=s.link_id JOIN vorgang v ON v.id=s.vorgang_id WHERE s.id=?", (int(sitzung_id),)).fetchone()
    if s is None:
        return None
    s = dict(s)
    jetzt = _zeit(_jetzt())
    if s["laeuft_ab"] <= jetzt or s["l_ab"] <= jetzt or s["l_gesperrt"] or s["l_verbraucht"] or s["vstatus"] not in LINK_STATI or s["personen"] not in (1, 2):
        return None
    return s


def _naechste_person(s: dict) -> int | None:
    with db.verbindung() as c:
        fertig = [z["person"] for z in c.execute("SELECT person FROM unterschrift WHERE vorgang_id=? AND mandant_ref=? ORDER BY person", (s["vorgang_id"], s["mandant_ref"]))]
    return len(fertig) + 1 if len(fertig) < s["personen"] else None


def _zeile_per_id(handoff_id, sitzung_id: int):
    if not isinstance(handoff_id, str) or not 8 <= len(handoff_id) <= 64:
        return None
    with db.verbindung() as c:
        z = c.execute("SELECT * FROM handoff WHERE handoff_id=? AND sitzung_id=?", (handoff_id, int(sitzung_id))).fetchone()
    return dict(z) if z else None


def _rest(z: dict) -> int:
    return max(0, int((datetime.strptime(z["laeuft_ab"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - _jetzt()).total_seconds()))


# ----- Computer (Cookie sn_zugang) -----

def _computer(f):
    from functools import wraps
    import zugang

    @wraps(f)
    def innen(*a, **kw):
        if request.method != "GET":
            fehler = zugang._origin_pruefen()
            if fehler:
                return fehler
        s = zugang._sitzung()
        if s is None:
            return zugang._ohne_sitzung()
        aufraeumen()
        return f(s, *a, **kw)
    return innen


@bp.post("")
@_computer
def erzeugen(s):
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or set(daten) != {"person"}:
        return _json({"error": "eingabe_ungueltig", "meldung": "Die Eingabe ist ungültig."}, 400)
    person = daten["person"]
    if isinstance(person, bool) or person not in (1, 2) or person > s["personen"]:
        return _json({"error": "person_ungueltig"}, 400)
    if not s["gelesen_am"] or not s["texte_angezeigt_am"] or person != _naechste_person(s):
        return _json({"error": "schritt_falsch", "meldung": "Die Übergabe ist erst im Schritt Unterschrift und nur für die nächste Person möglich."}, 409)
    basis = config.basis_url()
    if basis is None or config.hauptschluessel() is None:
        return _json({"error": "nicht_bereit"}, 503)
    jetzt = _jetzt()
    with db.verbindung() as c:
        n = c.execute("SELECT COUNT(*) FROM handoff_log WHERE vorgang_id=? AND zeit>=?", (s["vorgang_id"], _zeit(jetzt - timedelta(hours=1)))).fetchone()[0]
    if n >= ERZEUGEN_JE_STUNDE:
        return _json({"error": "zu_viele_anfragen", "meldung": "Zu viele Übergaben. Bitte später erneut versuchen."}, 429)
    token, token_hash = schluessel.neues_token()
    handoff_id = secrets.token_hex(12)
    with db.verbindung() as c:
        c.execute("DELETE FROM handoff WHERE vorgang_id=? AND mandant_ref=? AND person=?", (s["vorgang_id"], s["mandant_ref"], person))        # ein aktiver Eintrag je Person
        c.execute("INSERT INTO handoff(handoff_id, token_hash, sitzung_id, vorgang_id, mandant_ref, person, status, erstellt, laeuft_ab) VALUES (?,?,?,?,?,?, 'wartet', ?, ?)",
                  (handoff_id, token_hash, s["id"], s["vorgang_id"], s["mandant_ref"], person, _zeit(jetzt), _zeit(jetzt + timedelta(seconds=TTL_SEKUNDEN))))
        c.execute("INSERT INTO handoff_log(vorgang_id, zeit) VALUES (?,?)", (s["vorgang_id"], _zeit(jetzt)))
    _audit(s["mandant_ref"], "handoff_erzeugt", s["vorgang_id"])
    url = "%s/h#%s" % (basis, token)
    return _json({"handoff_id": handoff_id, "url": url, "qr_svg": qr.svg(url), "sekunden": TTL_SEKUNDEN})


@bp.get("/<handoff_id>")
@_computer
def status(s, handoff_id):
    z = _zeile_per_id(handoff_id, s["id"])
    if z is None:
        return _json({"status": "abgelaufen", "sekunden_rest": 0})
    return _json({"status": z["status"], "sekunden_rest": _rest(z)})


@bp.post("/<handoff_id>/abholen")
@_computer
def abholen(s, handoff_id):
    z = _zeile_per_id(handoff_id, s["id"])
    if z is None:
        return _json({"error": "schon_abgeholt", "meldung": "Das Bild wurde schon abgeholt oder ist abgelaufen."}, 410)
    if z["status"] != "gezeichnet" or z["bild_enc"] is None:
        return _json({"error": "noch_nicht_gezeichnet", "meldung": "Am Handy wurde noch nicht unterschrieben."}, 409)
    try:
        bild = schluessel.entschluesseln(z["mandant_ref"], ZWECK, z["bild_enc"])
    except schluessel.Entschluesselung:
        with db.verbindung() as c:
            c.execute("DELETE FROM handoff WHERE id=?", (z["id"],))
        return _json({"error": "schon_abgeholt", "meldung": "Das Bild wurde schon abgeholt oder ist abgelaufen."}, 410)
    with db.verbindung() as c:                                       # EINMAL: sofort löschen, auch wenn die Antwort den Computer nicht erreicht
        c.execute("DELETE FROM handoff WHERE id=?", (z["id"],))
    _audit(z["mandant_ref"], "handoff_abgeholt", z["vorgang_id"])
    return _json({"bild": bild})


@bp.delete("/<handoff_id>")
@_computer
def abbrechen(s, handoff_id):
    with db.verbindung() as c:
        c.execute("DELETE FROM handoff WHERE handoff_id=? AND sitzung_id=?", (handoff_id if isinstance(handoff_id, str) else "", s["id"]))
    return _json({"ok": True})


# ----- Handy (nur Token, kein Cookie) -----

def _handy_zeile(token):
    """Die gültige Übergabe zu diesem Token (Handy) samt gültiger Computer-Sitzung, sonst None. Unbekannt/abgelaufen/gesperrt sind nicht zu unterscheiden."""
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        return None
    with db.verbindung() as c:
        z = c.execute("SELECT * FROM handoff WHERE token_hash=? AND laeuft_ab>?", (schluessel.token_hash(token), _zeit(_jetzt()))).fetchone()
    if z is None:
        return None
    z = dict(z)
    s = _sitzung_zu(z["sitzung_id"])
    if s is None or _naechste_person(s) != z["person"]:
        return None
    z["personen"] = s["personen"]
    return z


def _handy_vorspann():
    import zugang
    fehler = zugang._origin_pruefen()
    if fehler:
        return fehler
    if _ip_begrenzt():
        return _json({"error": "zu_viele_versuche", "meldung": "Zu viele Versuche. Bitte später erneut versuchen."}, 429)
    aufraeumen()
    return None


@bp_handy.post("/info")
def handy_info():
    fehler = _handy_vorspann()
    if fehler:
        return fehler
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or set(daten) != {"token"}:
        return _ungueltig()
    z = _handy_zeile(daten["token"])
    if z is None:
        return _ungueltig()
    if z["status"] == "gezeichnet":
        return _json({"error": "schon_gezeichnet", "meldung": "Hier wurde schon unterschrieben. Bitte am Computer weitermachen."}, 409)
    if z["status"] == "wartet":
        with db.verbindung() as c:
            c.execute("UPDATE handoff SET status='verbunden' WHERE id=? AND status='wartet'", (z["id"],))
    return _json({"person": z["person"], "personen": z["personen"], "status": "verbunden"})


@bp_handy.post("/unterschrift")
def handy_unterschrift():
    fehler = _handy_vorspann()
    if fehler:
        return fehler
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or set(daten) != {"token", "bild"}:
        return _ungueltig()
    z = _handy_zeile(daten["token"])
    if z is None:
        return _ungueltig()
    if z["status"] == "gezeichnet":
        return _json({"error": "schon_gezeichnet", "meldung": "Hier wurde schon unterschrieben. Bitte am Computer weitermachen."}, 409)
    try:
        png = unterschrift_bild.pruefen(daten["bild"])
    except unterschrift_bild.Ungueltig as u:
        return _json({"error": u.code if u.code in ("bild_leer", "bild_zu_gross", "bild_format") else "bild_ungueltig", "meldung": "Das Unterschriftsbild ist nicht verwendbar."}, 422)
    bild = unterschrift_bild.PRAEFIX + base64.b64encode(png).decode("ascii")
    with db.verbindung() as c:
        ok = c.execute("UPDATE handoff SET status='gezeichnet', bild_enc=? WHERE id=? AND status IN ('wartet','verbunden')",
                       (schluessel.verschluesseln(z["mandant_ref"], ZWECK, bild), z["id"])).rowcount
    if not ok:
        return _json({"error": "schon_gezeichnet", "meldung": "Hier wurde schon unterschrieben. Bitte am Computer weitermachen."}, 409)
    _audit(z["mandant_ref"], "handoff_gezeichnet", z["vorgang_id"])
    return _json({"ok": True})
