"""Schlüssel aus dem Hauptschlüssel (HKDF) je Zweck UND je Beratungsstelle.

Verschlüsselt werden das SMTP-Passwort und die Mailadresse des Mitglieds (wiederherstellbar, AES-256-GCM mit zufälligem 96-Bit-Nonce je Wert;
Beratungsstelle und Zweck sind als zusätzliche authentifizierte Daten gebunden, ein Wert lässt sich also nicht in eine andere Beratungsstelle oder ein anderes Feld kopieren). Das Geburtsdatum wird nie
gespeichert, nur als HMAC des Codes ttmmjj. Link-Token werden nur als SHA-256 gespeichert.
Jeder Wert trägt die Kennung (kid) des Schlüssels, mit dem er erzeugt wurde; so bleiben alte Werte während eines Schlüsselwechsels
(SIGNNEXT_HAUPTSCHLUESSEL_ALT) lesbar und prüfbar. Fehlt der Hauptschlüssel, gibt es nichts (fail closed, kein Ersatzschlüssel)."""
import base64
import hashlib
import hmac
import re
import secrets
from datetime import date, datetime

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import config

REFERENZ = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class HauptschluesselFehlt(Exception):
    pass


class Entschluesselung(Exception):
    """Wert lässt sich nicht entschlüsseln (falscher Schlüssel, Beratungsstelle oder beschädigt). Enthält nie den Wert."""


def kid(roh: str) -> str:
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:8]


def aktuell() -> str:
    roh = config.hauptschluessel()
    if roh is None:
        raise HauptschluesselFehlt("SIGNNEXT_HAUPTSCHLUESSEL fehlt oder ist zu kurz")
    return roh


def _roh_zu_kid(kennung: str) -> str:
    for roh in (config.hauptschluessel(), config.hauptschluessel_alt()):
        if roh is not None and kid(roh) == kennung:
            return roh
    raise Entschluesselung("schluessel_unbekannt")


def _ableiten(roh: str, zweck: str, mandant_ref: str) -> bytes:
    if not REFERENZ.match(mandant_ref or ""):
        raise ValueError("mandant_ref ungueltig")
    info = ("signnext-%s-v1-%s" % (zweck, mandant_ref)).encode("utf-8")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(roh.encode("utf-8"))


def _aad(mandant_ref: str, zweck: str) -> bytes:
    return ("signnext-%s-v2-%s" % (zweck, mandant_ref)).encode("utf-8")


def verschluesseln(mandant_ref: str, zweck: str, klartext: str) -> str:
    roh = aktuell()
    nonce = secrets.token_bytes(12)
    chiffrat = AESGCM(_ableiten(roh, zweck, mandant_ref)).encrypt(nonce, klartext.encode("utf-8"), _aad(mandant_ref, zweck))
    return "v2:%s:%s" % (kid(roh), base64.urlsafe_b64encode(nonce + chiffrat).decode("ascii"))


def entschluesseln(mandant_ref: str, zweck: str, text: str) -> str:
    try:
        version, kennung, rest = (text or "").split(":", 2)
        if version != "v2":
            raise Entschluesselung("format")
        roh = _roh_zu_kid(kennung)
        daten = base64.urlsafe_b64decode(rest.encode("ascii"))
        if len(daten) < 12 + 16:
            raise Entschluesselung("nicht_lesbar")
        return AESGCM(_ableiten(roh, zweck, mandant_ref)).decrypt(daten[:12], daten[12:], _aad(mandant_ref, zweck)).decode("utf-8")
    except (ValueError, InvalidTag, UnicodeError):
        raise Entschluesselung("nicht_lesbar")


def umschluesseln(mandant_ref: str, zweck: str, text: str) -> str:
    """Schlüsselwechsel: mit dem Schlüssel des Wertes lesen, mit dem aktuellen neu verschlüsseln."""
    return verschluesseln(mandant_ref, zweck, entschluesseln(mandant_ref, zweck, text))


# --- Geburtsdatum: nur der Code ttmmjj, nur als HMAC ---

def code_aus_datum(text: str) -> str | None:
    """'TT.MM.JJJJ' → 'ttmmjj' (zweistelliges Jahr). Ungültige, zukünftige oder unplausible Daten → None."""
    if not isinstance(text, str) or not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text.strip()):
        return None
    try:
        d = datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None
    if d.year < 1900 or d > date.today():
        return None
    return "%02d%02d%02d" % (d.day, d.month, d.year % 100)


def code_hmac(mandant_ref: str, code: str) -> tuple[str, str]:
    """(HMAC hex, kid). Der Schlüssel gilt nur für diese Beratungsstelle."""
    roh = aktuell()
    h = hmac.new(_ableiten(roh, "code", mandant_ref), code.encode("ascii"), hashlib.sha256).hexdigest()
    return h, kid(roh)


def code_pruefen(mandant_ref: str, kennung: str, erwartet_hex: str, code: str) -> bool:
    """Konstantzeitiger Vergleich mit dem Schlüssel, der den Wert erzeugt hat (aktuell oder alt)."""
    try:
        roh = _roh_zu_kid(kennung)
    except Entschluesselung:
        return False
    h = hmac.new(_ableiten(roh, "code", mandant_ref), (code or "").encode("ascii", "ignore"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(h, erwartet_hex or "")


# --- Link-Token: 256 Bit, nur der Hash wird gespeichert ---

def neues_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8", "ignore")).hexdigest()
