"""SQLite (WAL), eine gesperrte Verbindung je Aufruf.

Mandantenschlüssel ist überall ``mandant_ref`` = ``externe_referenz`` aus dem Portal (Produkt signnext). Jede Abfrage auf Mandantendaten filtert danach."""
import json
import re
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config

_sperre = threading.RLock()
_pfad: str | None = None

SCHEMA = """
-- Ein Vorgang = ein Unterschriftsauftrag einer Beratungsstelle. Baustein 1 legt nur das Gerüst an, die Inhalte kommen mit Baustein 2 bis 5.
CREATE TABLE IF NOT EXISTS vorgang (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mandant_ref TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'neu',
    erstellt TEXT NOT NULL,
    personen INTEGER,
    abgleich TEXT,
    bestaetigt_von TEXT,
    bestaetigt_am TEXT,
    email_enc TEXT,
    email_herkunft TEXT,
    code_hmac TEXT,
    code_kid TEXT,
    datum_herkunft TEXT,
    personen_herkunft TEXT,
    versendet_am TEXT,
    versand_fehler TEXT
);
CREATE INDEX IF NOT EXISTS idx_vorgang_mandant ON vorgang(mandant_ref, id);
-- Rolle: A = Protokoll, B = Anschreiben, M = zusammengeführtes PDF. Keine Personendaten, nur Hash, Größe, Seiten, Stellen (JSON) und der Dateiname.
CREATE TABLE IF NOT EXISTS dokument (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vorgang_id INTEGER NOT NULL REFERENCES vorgang(id),
    mandant_ref TEXT NOT NULL,
    rolle TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    groesse INTEGER NOT NULL,
    seiten INTEGER NOT NULL,
    datei TEXT NOT NULL,
    stellen TEXT NOT NULL DEFAULT '[]',
    erstellt TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dokument_vorgang ON dokument(mandant_ref, vorgang_id);
-- Mail-Zugang der Beratungsstelle. Das Passwort steht nur verschlüsselt hier (AES-256-GCM, schluessel.py) und wird nie ausgegeben.
CREATE TABLE IF NOT EXISTS mail_zugang (
    mandant_ref TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    benutzer TEXT NOT NULL,
    passwort_enc TEXT NOT NULL,
    absender_adresse TEXT NOT NULL,
    absender_name TEXT NOT NULL DEFAULT '',
    signatur TEXT NOT NULL DEFAULT '',
    bcc_an_absender INTEGER NOT NULL DEFAULT 0,
    geprueft_am TEXT,
    geaendert_am TEXT NOT NULL,
    geaendert_von TEXT NOT NULL
);
-- Zugangslink des Mitglieds: nur der SHA-256 des Tokens (256 Bit) wird gespeichert. Gültigkeit 7 Tage, einmalig; die Prüfung folgt mit Baustein 4.
CREATE TABLE IF NOT EXISTS link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vorgang_id INTEGER NOT NULL REFERENCES vorgang(id),
    mandant_ref TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    erstellt TEXT NOT NULL,
    laeuft_ab TEXT NOT NULL,
    verbraucht_am TEXT,
    fehlversuche INTEGER NOT NULL DEFAULT 0,
    gesperrt INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_link_vorgang ON link(mandant_ref, vorgang_id);
-- Eine Zeile je Versandversuch (Limit, Nachweis). ergebnis: gesendet | fehlgeschlagen | unklar. Keine Adressen, keine Inhalte.
CREATE TABLE IF NOT EXISTS versand (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vorgang_id INTEGER NOT NULL REFERENCES vorgang(id),
    mandant_ref TEXT NOT NULL,
    zeit TEXT NOT NULL,
    ergebnis TEXT NOT NULL,
    code TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_versand_zeit ON versand(mandant_ref, zeit);
-- Angaben der Beratungsstelle für die Datenschutzinformation auf der Unterschriftsseite (Verantwortliche Stelle, Link zu ihrer Datenschutzerklärung).
CREATE TABLE IF NOT EXISTS stelle_einstellung (
    mandant_ref TEXT PRIMARY KEY,
    verantwortlicher TEXT NOT NULL,
    datenschutz_link TEXT NOT NULL,
    geaendert_am TEXT NOT NULL,
    geaendert_von TEXT NOT NULL
);
-- Sitzung des Mitglieds nach erfolgreicher Code-Prüfung: nur der Hash des Cookie-Werts wird gespeichert. Schritte werden serverseitig erzwungen.
CREATE TABLE IF NOT EXISTS sitzung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_hash TEXT NOT NULL UNIQUE,
    link_id INTEGER NOT NULL REFERENCES link(id),
    vorgang_id INTEGER NOT NULL REFERENCES vorgang(id),
    mandant_ref TEXT NOT NULL,
    erstellt TEXT NOT NULL,
    laeuft_ab TEXT NOT NULL,
    seiten_geliefert TEXT NOT NULL DEFAULT '[]',
    gelesen_am TEXT,
    texte_angezeigt_am TEXT,
    texte_kennung TEXT,
    texte_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_sitzung_vorgang ON sitzung(mandant_ref, vorgang_id);
-- Je Unterschrift: nur das Bild (keine biometrischen Merkmale), Zeitpunkte, Nachweis des angezeigten Textes, IP (laut Einstellung), grobe Gerätekennung.
CREATE TABLE IF NOT EXISTS unterschrift (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vorgang_id INTEGER NOT NULL REFERENCES vorgang(id),
    mandant_ref TEXT NOT NULL,
    person INTEGER NOT NULL,
    bild BLOB NOT NULL,
    bild_sha256 TEXT NOT NULL,
    zeit_texte_angezeigt TEXT NOT NULL,
    zeit_unterschrift TEXT NOT NULL,
    text_kennung TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    haken INTEGER NOT NULL,
    klick INTEGER NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    geraet TEXT NOT NULL DEFAULT '',
    UNIQUE(vorgang_id, person)
);
-- Aufrufprotokoll je Link (Zeit, gekürzte IP, Ergebnis).
CREATE TABLE IF NOT EXISTS zugriff_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id INTEGER NOT NULL,
    mandant_ref TEXT NOT NULL,
    zeit TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    ergebnis TEXT NOT NULL
);
-- Kurzlebig (24 h): Fehlversuche für vorgelegte Token, die es nicht gibt (nur der SHA-256 des Tokens), und Prüfversuche je IP (nur HMAC der Adresse).
CREATE TABLE IF NOT EXISTS zugang_unbekannt (
    token_hash TEXT PRIMARY KEY,
    versuche INTEGER NOT NULL,
    zeit TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS zugang_ip (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_hmac TEXT NOT NULL,
    zeit TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zugang_ip ON zugang_ip(ip_hmac, zeit);
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
        # Datenbanken aus 0.1.0 haben die neuen Spalten von vorgang noch nicht.
        vorhanden = {z["name"] for z in c.execute("PRAGMA table_info(vorgang)")}
        for spalte, typ in (("personen", "INTEGER"), ("abgleich", "TEXT"), ("bestaetigt_von", "TEXT"), ("bestaetigt_am", "TEXT"), ("email_enc", "TEXT"),
                            ("email_herkunft", "TEXT"), ("code_hmac", "TEXT"), ("code_kid", "TEXT"), ("datum_herkunft", "TEXT"), ("personen_herkunft", "TEXT"),
                            ("versendet_am", "TEXT"), ("versand_fehler", "TEXT")):
            if spalte not in vorhanden:
                c.execute("ALTER TABLE vorgang ADD COLUMN %s %s" % (spalte, typ))


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


# --- Vorgänge und Dokumente: immer mit Mandantenfilter ---

REFERENZ = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
VORGANG_FELDER = ("status", "personen", "abgleich", "bestaetigt_von", "bestaetigt_am", "email_enc", "email_herkunft", "code_hmac", "code_kid", "datum_herkunft",
                  "personen_herkunft", "versendet_am", "versand_fehler")
# Felder, die nach außen gehen dürfen (nie email_enc, code_hmac, code_kid)
VORGANG_OEFFENTLICH = ("id", "status", "erstellt", "personen", "abgleich", "bestaetigt_von", "bestaetigt_am", "email_herkunft", "datum_herkunft", "personen_herkunft",
                       "versendet_am", "versand_fehler")


def dokumente_verzeichnis(mandant_ref: str) -> Path:
    if not REFERENZ.match(mandant_ref or ""):
        raise ValueError("mandant_ref ungueltig")
    return config.daten_verzeichnis() / "dokumente" / mandant_ref


def vorgang_anlegen(mandant_ref: str, status: str = "neu") -> int:
    with verbindung() as c:
        return c.execute("INSERT INTO vorgang(mandant_ref, status, erstellt) VALUES (?,?,?)", (mandant_ref, status, jetzt())).lastrowid


def vorgang_aendern(mandant_ref: str, vorgang_id: int, **felder) -> bool:
    if not felder or any(k not in VORGANG_FELDER for k in felder):
        raise ValueError("unbekanntes Feld")
    sets = ", ".join("%s=?" % k for k in felder)
    with verbindung() as c:
        return c.execute("UPDATE vorgang SET %s WHERE id=? AND mandant_ref=?" % sets,
                         (*felder.values(), int(vorgang_id), mandant_ref)).rowcount == 1


def vorgang_lesen(mandant_ref: str, vorgang_id: int) -> dict | None:
    with verbindung() as c:
        z = c.execute("SELECT %s FROM vorgang WHERE id=? AND mandant_ref=?" % ", ".join(VORGANG_OEFFENTLICH), (int(vorgang_id), mandant_ref)).fetchone()
    if not z:
        return None
    d = dict(z)
    d["abgleich"] = json.loads(d["abgleich"]) if d["abgleich"] else None
    return d


def vorgang_intern(mandant_ref: str, vorgang_id: int) -> dict | None:
    """Alle Spalten inklusive verschlüsselter Werte. Nur für Versand und Prüfansicht, nie direkt ausgeben."""
    with verbindung() as c:
        z = c.execute("SELECT * FROM vorgang WHERE id=? AND mandant_ref=?", (int(vorgang_id), mandant_ref)).fetchone()
    return dict(z) if z else None


def vorgang_status_wechseln(mandant_ref: str, vorgang_id: int, von: str, nach: str, **felder) -> bool:
    """Atomarer Zustandswechsel (nur wenn der Vorgang noch im Zustand ``von`` ist). True, wenn dieser Aufruf gewechselt hat."""
    if any(k not in VORGANG_FELDER for k in felder):
        raise ValueError("unbekanntes Feld")
    sets = ", ".join(["status=?"] + ["%s=?" % k for k in felder])
    with verbindung() as c:
        return c.execute("UPDATE vorgang SET %s WHERE id=? AND mandant_ref=? AND status=?" % sets,
                         (nach, *felder.values(), int(vorgang_id), mandant_ref, von)).rowcount == 1


def vorgaenge_liste(mandant_ref: str) -> list:
    with verbindung() as c:
        zeilen = c.execute("SELECT id, status, erstellt FROM vorgang WHERE mandant_ref=? ORDER BY id DESC LIMIT 500",
                           (mandant_ref,)).fetchall()
    return [dict(z) for z in zeilen]


def dokument_speichern(mandant_ref: str, vorgang_id: int, rolle: str, daten: bytes, sha256: str, seiten: int, stellen: list) -> int:
    """Legt die Datei unter einem Zufallsnamen ab und trägt sie ein. Schlägt der Eintrag fehl, wird die Datei wieder entfernt."""
    verz = dokumente_verzeichnis(mandant_ref) / str(int(vorgang_id))
    verz.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + ".pdf"
    pfad = verz / name
    pfad.write_bytes(daten)
    try:
        with verbindung() as c:
            return c.execute("INSERT INTO dokument(vorgang_id, mandant_ref, rolle, sha256, groesse, seiten, datei, stellen, erstellt) VALUES (?,?,?,?,?,?,?,?,?)",
                             (int(vorgang_id), mandant_ref, rolle, sha256, len(daten), int(seiten), name, json.dumps(stellen), jetzt())).lastrowid
    except Exception:
        pfad.unlink(missing_ok=True)
        raise


def dokumente_liste(mandant_ref: str, vorgang_id: int) -> list:
    with verbindung() as c:
        zeilen = c.execute("SELECT id, rolle, sha256, groesse, seiten, stellen, erstellt FROM dokument WHERE mandant_ref=? AND vorgang_id=? ORDER BY id",
                           (mandant_ref, int(vorgang_id))).fetchall()
    out = []
    for z in zeilen:
        d = dict(z)
        d["stellen"] = json.loads(d["stellen"])
        out.append(d)
    return out


def dokument_bytes(mandant_ref: str, vorgang_id: int, rolle: str) -> bytes | None:
    """Inhalt des (letzten) Dokuments der Rolle; nur für den eigenen Mandanten."""
    with verbindung() as c:
        z = c.execute("SELECT datei FROM dokument WHERE mandant_ref=? AND vorgang_id=? AND rolle=? ORDER BY id DESC LIMIT 1",
                      (mandant_ref, int(vorgang_id), rolle)).fetchone()
    if not z:
        return None
    pfad = dokumente_verzeichnis(mandant_ref) / str(int(vorgang_id)) / z["datei"]
    return pfad.read_bytes() if pfad.is_file() else None


MANDANTENTABELLEN = ("sitzung", "unterschrift", "zugriff_log", "dokument", "link", "versand", "mail_zugang", "stelle_einstellung", "vorgang", "audit_log")


def mandant_loeschen(mandant_ref: str) -> dict:
    """Entfernt alle Daten einer Beratungsstelle (Vertragsende, Organisationslöschung): Dateien, dann Tabellenzeilen. Liefert die Zahlen für das Löschprotokoll.
    Spätere Bausteine ergänzen hier ihre Tabellen; der Test prüft, dass keine Tabelle mit mandant_ref übrig bleibt."""
    verz = dokumente_verzeichnis(mandant_ref)
    dateien = sum(1 for p in verz.rglob("*") if p.is_file()) if verz.is_dir() else 0
    if verz.is_dir():
        shutil.rmtree(verz)
    with verbindung() as c:
        zahlen = {}
        for tabelle in MANDANTENTABELLEN:
            zahlen[tabelle] = c.execute("DELETE FROM %s WHERE mandant_ref=?" % tabelle, (mandant_ref,)).rowcount
    zahlen["dateien"] = dateien
    return zahlen


def link_status(mandant_ref: str, vorgang_id: int) -> str | None:
    """'aktiv' | 'gesperrt' | 'abgelaufen' | 'verbraucht' | None (noch kein Link). Für den Hinweis an die Beratungsstelle."""
    with verbindung() as c:
        zeilen = c.execute("SELECT gesperrt, verbraucht_am, laeuft_ab FROM link WHERE mandant_ref=? AND vorgang_id=? ORDER BY id DESC", (mandant_ref, int(vorgang_id))).fetchall()
    if not zeilen:
        return None
    jetzt = jetzt_text()
    z = zeilen[0]
    if z["verbraucht_am"]:
        return "verbraucht"
    if z["gesperrt"]:
        return "gesperrt"
    return "abgelaufen" if z["laeuft_ab"] <= jetzt else "aktiv"


def jetzt_text() -> str:
    return jetzt()


def stelle_einstellung_lesen(mandant_ref: str) -> dict | None:
    with verbindung() as c:
        z = c.execute("SELECT verantwortlicher, datenschutz_link, geaendert_am FROM stelle_einstellung WHERE mandant_ref=?", (mandant_ref,)).fetchone()
    return dict(z) if z else None
