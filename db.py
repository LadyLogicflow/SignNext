"""SQLite (WAL), eine gesperrte Verbindung je Aufruf.

Mandantenschlüssel ist überall ``mandant_ref`` = ``externe_referenz`` aus dem Portal (Produkt signnext). Jede Abfrage auf Mandantendaten filtert danach."""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_sperre = threading.RLock()
_pfad: str | None = None

SCHEMA = """
-- Ein Vorgang = ein Unterschriftsauftrag einer Beratungsstelle. Baustein 1 legt nur das Gerüst an, die Inhalte kommen mit Baustein 2 bis 5.
CREATE TABLE IF NOT EXISTS vorgang (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mandant_ref TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'neu',
    erstellt TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vorgang_mandant ON vorgang(mandant_ref, id);
-- Nutzer = E-Mail der Portal-Sitzung, ip gekürzt (IPv4 /24, IPv6 /48). Keine Dokumentinhalte.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zeit TEXT NOT NULL,
    mandant_ref TEXT,
    nutzer TEXT NOT NULL,
    aktion TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_mandant ON audit_log(mandant_ref, id);
"""


def jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init(pfad: str) -> None:
    global _pfad
    _pfad = pfad
    with verbindung() as c:
        c.executescript(SCHEMA)
        c.execute("PRAGMA journal_mode=WAL")


@contextmanager
def verbindung():
    if _pfad is None:
        raise RuntimeError("Datenbank nicht initialisiert")
    with _sperre:
        c = sqlite3.connect(_pfad, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def audit(mandant_ref: str | None, nutzer: str, aktion: str, details: str = "", ip: str = "") -> None:
    """Schreibt einen Audit-Eintrag. Wirft bei Fehler (der Aufrufer bricht dann ab)."""
    with verbindung() as c:
        c.execute("INSERT INTO audit_log(zeit, mandant_ref, nutzer, aktion, details, ip) VALUES (?,?,?,?,?,?)",
                  (jetzt(), mandant_ref, nutzer, aktion, details, ip))


# --- Vorgänge: immer mit Mandantenfilter ---

def vorgang_lesen(mandant_ref: str, vorgang_id: int) -> dict | None:
    with verbindung() as c:
        z = c.execute("SELECT id, status, erstellt FROM vorgang WHERE id=? AND mandant_ref=?",
                      (int(vorgang_id), mandant_ref)).fetchone()
    return dict(z) if z else None


def vorgaenge_liste(mandant_ref: str) -> list:
    with verbindung() as c:
        zeilen = c.execute("SELECT id, status, erstellt FROM vorgang WHERE mandant_ref=? ORDER BY id DESC LIMIT 500",
                           (mandant_ref,)).fetchall()
    return [dict(z) for z in zeilen]


MANDANTENTABELLEN = ("vorgang", "audit_log")


def mandant_loeschen(mandant_ref: str) -> dict:
    """Entfernt alle Daten einer Beratungsstelle (Vertragsende, Organisationslöschung). Liefert die Zahlen für das Löschprotokoll.
    Spätere Bausteine ergänzen hier ihre Tabellen; der Test prüft, dass keine Tabelle mit mandant_ref übrig bleibt."""
    with verbindung() as c:
        zahlen = {}
        for tabelle in MANDANTENTABELLEN:
            zahlen[tabelle] = c.execute("DELETE FROM %s WHERE mandant_ref=?" % tabelle, (mandant_ref,)).rowcount
    return zahlen
