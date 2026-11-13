"""Deutsche Zeit (MEZ/MESZ) ohne tzdata-Abhängigkeit: Anzeige im Nachweisprotokoll und Monatsgrenze der Zählung.

Sommerzeit von letztem Sonntag im März bis letztem Sonntag im Oktober, jeweils um 01:00 UTC."""
from datetime import datetime, timedelta, timezone


def _letzter_sonntag(jahr: int, monat: int) -> datetime:
    d = datetime(jahr, monat, 31, 1, 0, 0, tzinfo=timezone.utc)      # Wechsel jeweils um 01:00 UTC
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _lokal(dt: datetime) -> tuple[datetime, bool]:
    sommer = _letzter_sonntag(dt.year, 3) <= dt < _letzter_sonntag(dt.year, 10)
    return dt + timedelta(hours=2 if sommer else 1), sommer


def _utc(zeit_utc: str) -> datetime:
    return datetime.strptime(zeit_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def berlin(zeit_utc: str) -> str:
    """'2026-11-13T14:05:00Z' → '13.11.2026 15:05:00 MEZ'."""
    lokal, sommer = _lokal(_utc(zeit_utc))
    return lokal.strftime("%d.%m.%Y %H:%M:%S") + (" MESZ" if sommer else " MEZ")


def monat(zeit_utc: str) -> str:
    """Kalendermonat in deutscher Zeit als 'JJJJ-MM' (z. B. '2026-12-31T23:30:00Z' → '2027-01')."""
    lokal, _ = _lokal(_utc(zeit_utc))
    return lokal.strftime("%Y-%m")
