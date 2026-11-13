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
