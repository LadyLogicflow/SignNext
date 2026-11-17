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
REFERENZ = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")

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
    stellen = daten.get("stellen") if isinstance(daten.get("stellen"), list) else []
    return {"ok": True, "nutzer": {"email": email, "rolle": rolle, "mandant_ref": ref, "stellen_anzahl": len(stellen), "stelle_rolle": _stelle_rolle(stellen, ref)}}


def _stelle_rolle(stellen: list, ref: str):
    """Rolle des Nutzers in der Beratungsstelle, die signnext mit dieser Referenz liefert (Feld ``stelle_rolle``, sobald SortNext es liefert); sonst None."""
    for st in stellen:
        if not isinstance(st, dict):
            continue
        for p in st.get("produkte") or []:
            if isinstance(p, dict) and p.get("produkt") == PRODUKT and isinstance(p.get("externe_referenz"), str) and p["externe_referenz"].strip() == ref:
                r = st.get("stelle_rolle")
                return r if r in ("admin", "mitarbeiter") else None
    return None


def admin_pruefen(nutzer: dict) -> tuple[bool, str | None]:
    """Darf dieser Nutzer in SEINER Beratungsstelle Admin-Dinge tun (z. B. Mail-Zugang ändern)? (erlaubt, Grund bei Ablehnung).
    Variante 'aus' (Voreinstellung): Rolle des Nutzers aus SortNext; hat der Nutzer mehr als eine Beratungsstelle, wird abgelehnt (fail closed), weil die
    Rolle dann nicht eindeutig einer Stelle zugeordnet werden kann. Variante 'an': nur die Rolle je Stelle zählt."""
    if config.stelle_rolle_variante() == "an":
        return (True, None) if nutzer.get("stelle_rolle") == "admin" else (False, "rolle_nicht_erlaubt")
    if nutzer["rolle"] != "admin":
        return False, "rolle_nicht_erlaubt"
    if nutzer.get("stellen_anzahl", 0) > 1:
        return False, "mehrere_beratungsstellen"
    return True, None


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


# ----- Offen-Modus der Demo (nur Pi-Testinstanz, SIGNNEXT_DEMO_ANMELDUNG=offen) -----
# Beraterseiten brauchen dort kein Portal-Cookie, werden aber nur beantwortet, wenn ALLE Bedingungen erfüllt sind (sonst 403, fail closed):
# (a) die Client-Adresse (n-ter Eintrag von rechts in X-Forwarded-For hinter genau einem Proxy, nie ein Wert des Clients) liegt in einem privaten Netz, Tailscale oder Loopback
#     (config.demo_netze; das Docker-Netz 172.16/12 zählt nicht), (b) der Host-Kopf ist eine der erlaubten Adressen (stoppt DNS-Rebinding), (c) bei ändernden Aufrufen passt
#     ein vorhandener Origin zum Host, (d) der Zugriff kam über den vertrauenswürdigen Proxy (X-Forwarded-For vorhanden).
DEMO_NUTZER = {"email": "demo-modus", "rolle": "admin", "mandant_ref": "demo", "demo": True}


def _adresse_im_netz(adresse: str) -> bool:
    import ipaddress
    try:
        ip = ipaddress.ip_address((adresse or "").strip())
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in netz for netz in config.demo_netze())


def _host_name(host: str) -> str:
    host = (host or "").strip().lower()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.split(":", 1)[0]


def offen_pruefen() -> str | None:
    """None = Zugriff erlaubt, sonst ein kurzer Grund (nur Log/Audit, nie für den Aufrufer)."""
    if not request.headers.get("X-Forwarded-For", "").strip():
        return "kein_proxy"
    if not _adresse_im_netz(client_ip()):
        return "adresse_nicht_erlaubt"
    if _host_name(request.headers.get("Host", "")) not in config.demo_hosts():
        return "host_unbekannt"
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        herkunft = request.headers.get("Origin")
        if herkunft is not None:
            from urllib.parse import urlsplit
            if herkunft == "null" or urlsplit(herkunft).netloc.lower() != request.headers.get("Host", "").strip().lower():
                return "origin_fremd"
    return None


def anmeldung_pflicht(rolle: str | None = None):
    """Decorator. Ohne Cookie 401; ohne Produktzugriff/Rolle 403; SortNext nicht erreichbar 503. ``rolle='admin'`` verlangt die Rolle Admin.
    Setzt g.nutzer = {email, rolle, mandant_ref}; ``mandant_ref`` ist der einzige Mandantenschlüssel."""
    def deko(f):
        @wraps(f)
        def innen(*a, **kw):
            if config.demo_anmeldung_offen():                  # Demo: feste Beratungsstelle, kein Portal (siehe oben); ohne Erlaubnis 403
                grund = offen_pruefen()
                if grund:
                    db.audit(None, "", "demo_verweigert", grund, kuerze_ip(client_ip()))
                    return jsonify({"error": "forbidden", "grund": "demo_nicht_erlaubt"}), 403
                g.nutzer = dict(DEMO_NUTZER)
                return f(*a, **kw)
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
            if rolle:
                ok, grund = admin_pruefen(e["nutzer"]) if rolle == "admin" else (e["nutzer"]["rolle"] == rolle, "rolle_nicht_erlaubt")
                if not ok:
                    g.nutzer = e["nutzer"]
                    audit("zugriff_verweigert", "rolle_" + rolle + "_noetig:" + str(grund))
                    return jsonify({"error": "forbidden", "grund": grund}), 403
            g.nutzer = e["nutzer"]
            return f(*a, **kw)
        return innen
    return deko
