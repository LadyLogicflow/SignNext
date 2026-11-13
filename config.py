"""Konfiguration von SignNext. Baustein 1 braucht kein Geheimnis: Anmeldung läuft über SortNext (/api/v1/portal/me)."""
import os
from pathlib import Path


def version() -> str:
    try:
        return (Path(__file__).with_name("VERSION")).read_text(encoding="utf-8").strip()
    except OSError:
        return "unbekannt"


def daten_verzeichnis() -> Path:
    return Path(os.environ.get("SIGNNEXT_DATEN", "/data"))


def sortnext_basis_url() -> str:
    return os.environ.get("SORTNEXT_BASE_URL", "https://sortnext.bst-next.de").rstrip("/")


def proxy_anzahl() -> int:
    """Wie vielen vorgeschalteten Proxys (Coolify/Traefik) für X-Forwarded-For vertraut wird. 0 = Adresse der Verbindung."""
    try:
        return max(0, int(os.environ.get("SIGNNEXT_PROXIES", "1")))
    except ValueError:
        return 1


HAUPTSCHLUESSEL_MIN_LAENGE = 32
VERSAND_JE_STUNDE = 30


def hauptschluessel() -> str | None:
    """Hauptschlüssel für Mail-Zugangsdaten, Mailadressen und den Code-HMAC. Es gibt bewusst keinen Ersatzwert (fail closed).
    Betriebshinweis: Der Schlüssel darf nicht im selben Backup liegen wie die Datenbank und steht nie im Repo."""
    wert = os.environ.get("SIGNNEXT_HAUPTSCHLUESSEL", "")
    return wert if len(wert) >= HAUPTSCHLUESSEL_MIN_LAENGE else None


def hauptschluessel_alt() -> str | None:
    """Vorheriger Hauptschlüssel, nur während eines Schlüsselwechsels gesetzt (Entschlüsseln und Code-Prüfung alter Daten)."""
    wert = os.environ.get("SIGNNEXT_HAUPTSCHLUESSEL_ALT", "")
    return wert if len(wert) >= HAUPTSCHLUESSEL_MIN_LAENGE else None


def basis_url() -> str | None:
    """Öffentliche Adresse von SignNext für die Links in Mails. Kein Standardwert; ohne gültige https-Adresse wird nichts versendet."""
    wert = os.environ.get("SIGNNEXT_BASIS_URL", "").strip().rstrip("/")
    return wert if wert.startswith("https://") and len(wert) > 10 else None


def stelle_rolle_variante() -> str:
    """'an': Rolle je Beratungsstelle aus /portal/me (Feld stelle_rolle, sobald SortNext es liefert). 'aus' (Voreinstellung): Rolle des Nutzers."""
    return "an" if os.environ.get("SIGNNEXT_STELLE_ROLLE", "") == "an" else "aus"
