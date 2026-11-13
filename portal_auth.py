"""Anmeldung über das zentrale Portal-Cookie (bst_session, Domain .bst-next.de).

SignNext prüft das Cookie nicht selbst, sondern fragt SortNext (/api/v1/portal/me). Das Ergebnis wird 30 Sekunden zwischengespeichert; eine Sperre
oder Rücknahme der Freischaltung wirkt damit nach höchstens 30 Sekunden. Fail closed: Ist SortNext nicht erreichbar oder die Antwort nicht eindeutig,
gibt es keinen Zugriff (503 bzw. 403), nie ein 'erlaubt'.

Mandantenschlüssel ist ``externe_referenz`` des Produkts ``signnext`` aus der Antwort von SortNext, nie ein Wert vom Client."""
import hashlib
import re
import threading
import time
from functools import wraps

import requests
from flask import g, jsonify, request

import config
import db

COOKIE = "bst_session"
PRODUKT = "signnext"
CACHE_SEKUNDEN = 30
# Rolle des Nutzers laut SortNext → Rolle in SignNext. Alles andere (super_admin, unbekannt) bekommt keinen Zugriff auf Mandantendaten.
ROLLEN = {"bst_admin": "admin", "bst_user": "mitarbeiter", "mitarbeiter": "mitarbeiter"}
REFERENZ = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_cache: dict[str, tuple[float, dict]] = {}
_cache_sperre = threading.Lock()


def _schluessel(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bei_sortnext_fragen(token: str) -> tuple[str, dict | None]:
    """('ok', daten) | ('abgelehnt', None) | ('nicht_erreichbar', None)."""
    try:
        r = requests.get(config.sortnext_basis_url() + "/api/v1/portal/me", cookies={COOKIE: token}, timeout=5)
    except requests.RequestException:
        return "nicht_erreichbar", None
    if r.status_code >= 500:
        return "nicht_erreichbar", None
    if r.status_code != 200:
        return "abgelehnt", None
    try:
        daten = r.json()
    except ValueError:
        return "nicht_erreichbar", None
    if not isinstance(daten, dict) or not isinstance(daten.get("email"), str) or not daten["email"]:
        return "nicht_erreichbar", None
    return "ok", daten


def auswerten(daten: dict) -> dict:
    """Macht aus der Antwort von SortNext eine Entscheidung: {'ok': True, 'nutzer': {...}} oder {'ok': False, 'grund': code}."""
    email = daten["email"].strip().lower()
    if daten.get("onboarding_incomplete"):
        return {"ok": False, "grund": "onboarding_unvollstaendig", "email": email}
    rolle = ROLLEN.get(daten.get("rolle")) if isinstance(daten.get("rolle"), str) else None
    if rolle is None:
        return {"ok": False, "grund": "rolle_nicht_erlaubt", "email": email}
    produkte = daten.get("produkte")
    if not isinstance(produkte, list):
        return {"ok": False, "grund": "kein_produktzugriff", "email": email}
    referenzen = set()
    for p in produkte:
        if isinstance(p, dict) and p.get("produkt") == PRODUKT:
            ref = p.get("externe_referenz")
            referenzen.add(ref.strip() if isinstance(ref, str) else None)
    if not referenzen:
        return {"ok": False, "grund": "kein_produktzugriff", "email": email}
    if len(referenzen) > 1:
        return {"ok": False, "grund": "mehrere_beratungsstellen", "email": email}
    ref = referenzen.pop()
    if ref is None or not REFERENZ.match(ref):
        return {"ok": False, "grund": "referenz_ungueltig", "email": email}
    return {"ok": True, "nutzer": {"email": email, "rolle": rolle, "mandant_ref": ref}}


def ermitteln(token: str) -> tuple[str, dict | None]:
    """('ok', entscheidung) | ('abgelehnt', None) | ('nicht_erreichbar', None). Nur eindeutige Antworten werden zwischengespeichert, nie ein Ausfall."""
    k = _schluessel(token)
    jetzt = time.monotonic()
    with _cache_sperre:
        treffer = _cache.get(k)
        if treffer and jetzt - treffer[0] < CACHE_SEKUNDEN:
            return "ok", treffer[1]
    status, daten = _bei_sortnext_fragen(token)
    if status != "ok":
        return status, None
    entscheidung = auswerten(daten)
    with _cache_sperre:
        if len(_cache) > 500:
            _cache.clear()
        _cache[k] = (jetzt, entscheidung)
    return "ok", entscheidung


def cache_leeren() -> None:
    with _cache_sperre:
        _cache.clear()


def kuerze_ip(ip: str) -> str:
    """IPv4 auf /24, IPv6 auf /48 kürzen (Audit-Log); Unbekanntes wird leer."""
    ip = (ip or "").strip()
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
        return ".".join(ip.split(".")[:3]) + ".0"
    if ":" in ip and re.fullmatch(r"[0-9A-Fa-f:.]{2,45}", ip):
        teile = ip.split("::")[0].split(":")[:3]
        return ":".join(t for t in teile if t) + "::"
    return ""


def client_ip() -> str:
    """Hinter n vertrauenswürdigen Proxys zählt der n-te Eintrag von rechts in X-Forwarded-For (ein vom Client vorangestellter Wert wird ignoriert)."""
    n = config.proxy_anzahl()
    teile = [t.strip() for t in request.headers.get("X-Forwarded-For", "").split(",") if t.strip()]
    if n and len(teile) >= n:
        return teile[-n][:64]
    return (request.remote_addr or "")[:64]


def audit(aktion: str, details: str = "") -> None:
    """Audit-Eintrag für den angemeldeten Nutzer (nach @anmeldung_pflicht)."""
    db.audit(g.nutzer["mandant_ref"], g.nutzer["email"], aktion, details, kuerze_ip(client_ip()))


def anmeldung_pflicht(rolle: str | None = None):
    """Decorator. Ohne Cookie 401; ohne Produktzugriff/Rolle 403; SortNext nicht erreichbar 503. ``rolle='admin'`` verlangt die Rolle Admin.
    Setzt g.nutzer = {email, rolle, mandant_ref}; ``mandant_ref`` ist der einzige Mandantenschlüssel."""
    def deko(f):
        @wraps(f)
        def innen(*a, **kw):
            token = request.cookies.get(COOKIE, "")
            if not token:
                return jsonify({"error": "not authenticated"}), 401
            status, e = ermitteln(token)
            if status == "nicht_erreichbar":
                return jsonify({"error": "anmeldung_nicht_verfuegbar"}), 503
            if status == "abgelehnt":
                return jsonify({"error": "not authenticated"}), 401
            if not e["ok"]:
                db.audit(None, e["email"], "zugriff_verweigert", e["grund"], kuerze_ip(client_ip()))
                return jsonify({"error": "forbidden", "grund": e["grund"]}), 403
            if rolle and e["nutzer"]["rolle"] != rolle:
                g.nutzer = e["nutzer"]
                audit("zugriff_verweigert", "rolle_" + rolle + "_noetig")
                return jsonify({"error": "forbidden", "grund": "rolle_nicht_erlaubt"}), 403
            g.nutzer = e["nutzer"]
            return f(*a, **kw)
        return innen
    return deko
