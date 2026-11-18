"""SQLite (WAL), eine gesperrte Verbindung je Aufruf.

Mandantenschlüssel ist überall ``mandant_ref`` = ``externe_referenz`` aus dem Portal (Produkt signnext). Jede Abfrage auf Mandantendaten filtert danach."""
import json
import re
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
    name TEXT,
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
    versand_fehler TEXT,
    versenden_seit TEXT,
    unterschrieben_am TEXT,
    abschluss_seit TEXT,
    abschluss_versuche INTEGER NOT NULL DEFAULT 0,
    abgeschlossen_am TEXT,
    aufbewahrung_tage INTEGER,
    aufbewahrung_bis TEXT,
    erinnerungen_aus INTEGER NOT NULL DEFAULT 0,
    link_sperre_gemeldet INTEGER,
    bst_info_am TEXT,
    mitglied_mail_am TEXT,
    mitglied_mail_versuche INTEGER NOT NULL DEFAULT 0,
    vorwarnung_am TEXT,
    geloescht_am TEXT,
    geloescht_grund TEXT,
    gezaehlt_am TEXT,
    zaehlmonat TEXT
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
    gesperrt INTEGER NOT NULL DEFAULT 0,
    token_enc TEXT
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
    geaendert_von TEXT NOT NULL,
    aufbewahrung_tage INTEGER,
    mitglied_download_link INTEGER NOT NULL DEFAULT 1
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
    texte_hash TEXT,
    dok_stand TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sitzung_vorgang ON sitzung(mandant_ref, vorgang_id);
-- Handy-Übergabe der Unterschrift (handoff.py): kurzlebig (10 Minuten), das Bild liegt nur verschlüsselt hier und wird nach der Abholung sofort gelöscht.
CREATE TABLE IF NOT EXISTS handoff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    sitzung_id INTEGER NOT NULL,
    vorgang_id INTEGER NOT NULL,
    mandant_ref TEXT NOT NULL,
    person INTEGER NOT NULL,
    dokument_rolle TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'wartet',
    erstellt TEXT NOT NULL,
    laeuft_ab TEXT NOT NULL,
    bild_enc TEXT
);
CREATE INDEX IF NOT EXISTS idx_handoff_vorgang ON handoff(mandant_ref, vorgang_id, person);
CREATE TABLE IF NOT EXISTS handoff_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vorgang_id INTEGER NOT NULL,
    zeit TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_handoff_log ON handoff_log(vorgang_id, zeit);
-- Je Unterschrift: nur das Bild (keine biometrischen Merkmale), Zeitpunkte, Nachweis des angezeigten Textes, IP (laut Einstellung), grobe Gerätekennung.
CREATE TABLE IF NOT EXISTS unterschrift (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vorgang_id INTEGER NOT NULL REFERENCES vorgang(id),
    mandant_ref TEXT NOT NULL,
    person INTEGER NOT NULL,
    dokument_rolle TEXT NOT NULL DEFAULT 'M',
    haken_uebernommen INTEGER NOT NULL DEFAULT 0,
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
    UNIQUE(vorgang_id, dokument_rolle, person)
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
-- Erinnerungen an das Mitglied: eine Zeile je Erinnerung (48/72/96/120/144/156 Stunden nach dem Versand). Der Anspruch (versuche, letzter_versuch) verhindert Doppelversand.
-- ergebnis: NULL (offen) | gesendet | fehlgeschlagen | unklar | uebersprungen | entfallen
CREATE TABLE IF NOT EXISTS erinnerung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vorgang_id INTEGER NOT NULL REFERENCES vorgang(id),
    mandant_ref TEXT NOT NULL,
    nr INTEGER NOT NULL,
    faellig_am TEXT NOT NULL,
    versuche INTEGER NOT NULL DEFAULT 0,
    letzter_versuch TEXT,
    gesendet_am TEXT,
    ergebnis TEXT,
    UNIQUE(vorgang_id, nr)
);
CREATE INDEX IF NOT EXISTS idx_erinnerung_faellig ON erinnerung(mandant_ref, faellig_am);
-- Download-Link für das Mitglied: nur der SHA-256 des Tokens, 7 Tage, höchstens 3 Abrufe, Abrufe werden protokolliert (Zeit, gekürzte IP, Zähler).
CREATE TABLE IF NOT EXISTS download_link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vorgang_id INTEGER NOT NULL REFERENCES vorgang(id),
    mandant_ref TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    erstellt TEXT NOT NULL,
    laeuft_ab TEXT NOT NULL,
    abrufe INTEGER NOT NULL DEFAULT 0,
    max_abrufe INTEGER NOT NULL DEFAULT 3,
    gesperrt INTEGER NOT NULL DEFAULT 0,
    fehlversuche INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_download_vorgang ON download_link(mandant_ref, vorgang_id);
CREATE TABLE IF NOT EXISTS download_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id INTEGER NOT NULL,
    vorgang_id INTEGER NOT NULL,
    mandant_ref TEXT NOT NULL,
    zeit TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    zaehler INTEGER NOT NULL,
    bst_benachrichtigt_am TEXT
);
-- Tarife (Betreiber, per manage.py): Limit je Kalendermonat (NULL = unbegrenzt) und Preis in Cent (nur ein Wert, SignNext rechnet nichts ab).
CREATE TABLE IF NOT EXISTS tarif (
    name TEXT PRIMARY KEY,
    limit_monat INTEGER,
    preis_cent INTEGER NOT NULL DEFAULT 0
);
-- Zuordnung einer Beratungsstelle zu einem Tarif (ohne Zeile: Standard laut config.tarif_plus_referenzen, sonst basis).
CREATE TABLE IF NOT EXISTS stelle_tarif (
    mandant_ref TEXT PRIMARY KEY,
    tarif TEXT NOT NULL,
    geaendert_am TEXT NOT NULL,
    geaendert_von TEXT NOT NULL
);
-- Löschprotokoll: NUR Zahlen. Keine Namen, keine Dateinamen, keine Mitgliedsdaten. mandant_ref und vorgang_id nur zur Zuordnung. Bleibt auch nach Vertragsende
-- (Ausnahme von mandant_loeschen, siehe AUSNAHMEN) und wird nach ``loeschen_nach`` (Standard 3 Jahre) selbst gelöscht.
CREATE TABLE IF NOT EXISTS loeschprotokoll (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zeit TEXT NOT NULL,
    art TEXT NOT NULL,
    mandant_ref TEXT NOT NULL,
    vorgang_id INTEGER,
    zahlen TEXT NOT NULL,
    loeschen_nach TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loeschprotokoll_frist ON loeschprotokoll(loeschen_nach);
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
        # v0.7.0 Einzel-Signatur: Unterschriften gelten je Dokument (UNIQUE ändert sich, SQLite braucht dafür den Umbau der Tabelle). Alte Zeilen gehören zum zusammengeführten Dokument 'M'.
        alt = bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='unterschrift'").fetchone()) and \
            "dokument_rolle" not in {z["name"] for z in c.execute("PRAGMA table_info(unterschrift)")}
        if alt:
            c.execute("ALTER TABLE unterschrift RENAME TO unterschrift_alt")
        c.executescript(SCHEMA)
        if alt:
            c.execute("INSERT INTO unterschrift(id, vorgang_id, mandant_ref, person, dokument_rolle, bild, bild_sha256, zeit_texte_angezeigt, zeit_unterschrift, text_kennung, text_hash, haken, klick, ip, geraet) "
                      "SELECT id, vorgang_id, mandant_ref, person, 'M', bild, bild_sha256, zeit_texte_angezeigt, zeit_unterschrift, text_kennung, text_hash, haken, klick, ip, geraet FROM unterschrift_alt")
            c.execute("DROP TABLE unterschrift_alt")
        c.execute("PRAGMA journal_mode=WAL")
        # Datenbanken aus 0.1.0 haben die neuen Spalten von vorgang noch nicht.
        vorhanden = {z["name"] for z in c.execute("PRAGMA table_info(vorgang)")}
        for spalte, typ in (("personen", "INTEGER"), ("abgleich", "TEXT"), ("bestaetigt_von", "TEXT"), ("bestaetigt_am", "TEXT"), ("email_enc", "TEXT"),
                            ("email_herkunft", "TEXT"), ("code_hmac", "TEXT"), ("code_kid", "TEXT"), ("datum_herkunft", "TEXT"), ("personen_herkunft", "TEXT"),
                            ("versendet_am", "TEXT"), ("versand_fehler", "TEXT"), ("versenden_seit", "TEXT"), ("unterschrieben_am", "TEXT"), ("abschluss_seit", "TEXT"),
                            ("abschluss_versuche", "INTEGER NOT NULL DEFAULT 0"), ("abgeschlossen_am", "TEXT"), ("aufbewahrung_tage", "INTEGER"), ("aufbewahrung_bis", "TEXT"),
                            ("erinnerungen_aus", "INTEGER NOT NULL DEFAULT 0"), ("link_sperre_gemeldet", "INTEGER"), ("bst_info_am", "TEXT"), ("mitglied_mail_am", "TEXT"),
                            ("mitglied_mail_versuche", "INTEGER NOT NULL DEFAULT 0"), ("vorwarnung_am", "TEXT"), ("geloescht_am", "TEXT"), ("geloescht_grund", "TEXT"),
                            ("gezaehlt_am", "TEXT"), ("zaehlmonat", "TEXT"), ("name", "TEXT")):
            if spalte not in vorhanden:
                c.execute("ALTER TABLE vorgang ADD COLUMN %s %s" % (spalte, typ))
        for tabelle, spalte, typ in (("link", "token_enc", "TEXT"), ("stelle_einstellung", "aufbewahrung_tage", "INTEGER"),
                                     ("stelle_einstellung", "mitglied_download_link", "INTEGER NOT NULL DEFAULT 1"),
                                     ("download_link", "fehlversuche", "INTEGER NOT NULL DEFAULT 0"),
                                     ("sitzung", "dok_stand", "TEXT NOT NULL DEFAULT '{}'"), ("handoff", "dokument_rolle", "TEXT NOT NULL DEFAULT ''")):
            if spalte not in {z["name"] for z in c.execute("PRAGMA table_info(%s)" % tabelle)}:
                c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tabelle, spalte, typ))
        c.execute("CREATE INDEX IF NOT EXISTS idx_vorgang_zaehlung ON vorgang(mandant_ref, zaehlmonat)")      # nach den Spalten (alte Datenbanken)
        for name, (limit, preis) in config.TARIF_VORGABE.items():
            c.execute("INSERT OR IGNORE INTO tarif(name, limit_monat, preis_cent) VALUES (?,?,?)", (name, limit, preis))
        # Vorgänge aus früheren Fassungen: Was schon versendet wurde, zählt (im Monat des Versands).
        import berlinzeit
        for z in c.execute("SELECT id, versendet_am FROM vorgang WHERE versendet_am IS NOT NULL AND gezaehlt_am IS NULL").fetchall():
            c.execute("UPDATE vorgang SET gezaehlt_am=?, zaehlmonat=? WHERE id=?", (z["versendet_am"], berlinzeit.monat(z["versendet_am"]), z["id"]))


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


def audit_in(c, mandant_ref: str | None, nutzer: str, aktion: str, details: str = "", ip: str = "") -> None:
    """Wie ``audit``, aber in der Transaktion des Aufrufers."""
    c.execute("INSERT INTO audit_log(zeit, mandant_ref, nutzer, aktion, details, ip) VALUES (?,?,?,?,?,?)", (jetzt(), mandant_ref, nutzer, aktion, details, ip))


# --- Vorgänge und Dokumente: immer mit Mandantenfilter ---

REFERENZ = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
VORGANG_FELDER = ("status", "personen", "abgleich", "bestaetigt_von", "bestaetigt_am", "email_enc", "email_herkunft", "code_hmac", "code_kid", "datum_herkunft",
                  "personen_herkunft", "versendet_am", "versand_fehler", "versenden_seit", "unterschrieben_am", "abschluss_seit", "abschluss_versuche", "abgeschlossen_am",
                  "aufbewahrung_tage", "aufbewahrung_bis", "erinnerungen_aus", "link_sperre_gemeldet", "bst_info_am", "mitglied_mail_am", "mitglied_mail_versuche",
                  "vorwarnung_am", "geloescht_am", "geloescht_grund", "gezaehlt_am", "zaehlmonat", "name")
# Felder, die nach außen gehen dürfen (nie email_enc, code_hmac, code_kid)
VORGANG_OEFFENTLICH = ("id", "status", "erstellt", "name", "personen", "abgleich", "bestaetigt_von", "bestaetigt_am", "email_herkunft", "datum_herkunft", "personen_herkunft",
                       "versendet_am", "versand_fehler", "unterschrieben_am", "abgeschlossen_am", "aufbewahrung_bis", "erinnerungen_aus", "geloescht_am", "geloescht_grund")


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


def vorgaenge_liste(mandant_ref: str, ohne_geloeschte: bool = False) -> list:
    with verbindung() as c:
        zeilen = c.execute("SELECT v.id, v.status, v.erstellt, v.name, v.personen, "
                           "(SELECT l.gesperrt FROM link l WHERE l.vorgang_id=v.id AND l.mandant_ref=v.mandant_ref ORDER BY l.id DESC LIMIT 1) AS l_gesperrt, "
                           "(SELECT l.verbraucht_am FROM link l WHERE l.vorgang_id=v.id AND l.mandant_ref=v.mandant_ref ORDER BY l.id DESC LIMIT 1) AS l_verbraucht, "
                           "(SELECT l.laeuft_ab FROM link l WHERE l.vorgang_id=v.id AND l.mandant_ref=v.mandant_ref ORDER BY l.id DESC LIMIT 1) AS l_ab "
                           "FROM vorgang v WHERE v.mandant_ref=? AND (?=0 OR v.status<>'geloescht') ORDER BY v.id DESC LIMIT 500", (mandant_ref, int(bool(ohne_geloeschte)))).fetchall()
    jetzt = jetzt_text()
    liste = []
    for z in zeilen:
        d = {k: z[k] for k in ("id", "status", "erstellt", "name", "personen")}
        d["link_laeuft_ab"] = z["l_ab"]
        d["link_status"] = None if z["l_ab"] is None else _link_status_aus({"verbraucht_am": z["l_verbraucht"], "gesperrt": z["l_gesperrt"], "laeuft_ab": z["l_ab"]}, jetzt)
        liste.append(d)
    return liste


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


MANDANTENTABELLEN = ("download_log", "download_link", "erinnerung", "sitzung", "unterschrift", "zugriff_log", "dokument", "link", "versand", "mail_zugang",
                     "stelle_einstellung", "stelle_tarif", "audit_log", "handoff")
# Tabellen mit mandant_ref, die bei der Löschung einer Beratungsstelle BLEIBEN: das Löschprotokoll (nur Zahlen, Nachweis der Löschung). Wer das ändern will (Irony prüft),
# setzt LOESCHPROTOKOLL_BLEIBT auf False: dann wird es wie alle anderen Tabellen mitgelöscht, ohne weitere Änderung.
LOESCHPROTOKOLL_BLEIBT = True
AUSNAHMEN = ("loeschprotokoll", "vorgang")      # vorgang: Zähleinträge (ohne Inhalt) bleiben, siehe ZAEHLEINTRAG_BLEIBT
ZAEHLEINTRAG_BLEIBT = True
ZAEHLEINTRAG_JAHRE = 3                         # gelöscht, wenn 3 Jahre nach Ende des Kalenderjahres des Versands vergangen sind (jobs.py)
LOESCHPROTOKOLL_JAHRE = 3


def mandanten_tabellen_zum_loeschen() -> tuple:
    return MANDANTENTABELLEN + (() if LOESCHPROTOKOLL_BLEIBT else ("loeschprotokoll",))


def loeschprotokoll_schreiben(c, art: str, mandant_ref: str, vorgang_id: int | None, zahlen: dict, jetzt_dt: datetime | None = None) -> None:
    """Nur Zahlen (ganze Zahlen) und Art. Verweigert alles andere, damit keine Namen oder Dateinamen hineinrutschen."""
    if not all(isinstance(k, str) and re.fullmatch(r"[a-z_]{1,30}", k) and isinstance(v, int) and not isinstance(v, bool) for k, v in zahlen.items()):
        raise ValueError("Löschprotokoll nimmt nur Zählerwerte")
    zeit = jetzt_dt or datetime.now(timezone.utc)
    try:
        frist = zeit.replace(year=zeit.year + LOESCHPROTOKOLL_JAHRE)
    except ValueError:                                                      # 29. Februar
        frist = zeit.replace(year=zeit.year + LOESCHPROTOKOLL_JAHRE, day=28)
    c.execute("INSERT INTO loeschprotokoll(zeit, art, mandant_ref, vorgang_id, zahlen, loeschen_nach) VALUES (?,?,?,?,?,?)",
              (zeit.strftime("%Y-%m-%dT%H:%M:%SZ"), art, mandant_ref, vorgang_id, json.dumps(zahlen, sort_keys=True), frist.strftime("%Y-%m-%dT%H:%M:%SZ")))


def mandant_loeschen(mandant_ref: str) -> dict:
    """Entfernt alle Daten einer Beratungsstelle (Vertragsende, Organisationslöschung): Dateien, dann Tabellenzeilen. Liefert die Zahlen für das Löschprotokoll
    und schreibt einen Eintrag ins Löschprotokoll (nur Zahlen). Spätere Bausteine ergänzen hier ihre Tabellen; der Test prüft, dass keine Tabelle mit mandant_ref übrig bleibt."""
    verz = dokumente_verzeichnis(mandant_ref)
    dateien = sum(1 for p in verz.rglob("*") if p.is_file()) if verz.is_dir() else 0
    if verz.is_dir():
        shutil.rmtree(verz)
    with verbindung() as c:
        zahlen = {}
        for tabelle in mandanten_tabellen_zum_loeschen():
            zahlen[tabelle] = c.execute("DELETE FROM %s WHERE mandant_ref=?" % tabelle, (mandant_ref,)).rowcount
        # Vorgänge: Was nie versendet wurde, geht ganz. Was gezählt wurde, bleibt als Zähleintrag ohne Inhalt (ID, Zeitpunkte, Monat, Status, Organisation) bis zur Frist.
        if ZAEHLEINTRAG_BLEIBT:
            zahlen["vorgang"] = c.execute("DELETE FROM vorgang WHERE mandant_ref=? AND gezaehlt_am IS NULL", (mandant_ref,)).rowcount
            zahlen["zaehleintraege"] = c.execute("UPDATE vorgang SET status='geloescht', geloescht_am=COALESCE(geloescht_am, ?), geloescht_grund=COALESCE(geloescht_grund, 'vertragsende'), "
                                                 "name=NULL, email_enc=NULL, code_hmac=NULL, code_kid=NULL, abgleich=NULL, bestaetigt_von=NULL, email_herkunft=NULL, datum_herkunft=NULL, "
                                                 "personen_herkunft=NULL, versand_fehler=NULL WHERE mandant_ref=? AND gezaehlt_am IS NOT NULL", (jetzt(), mandant_ref)).rowcount
        else:
            zahlen["vorgang"] = c.execute("DELETE FROM vorgang WHERE mandant_ref=?", (mandant_ref,)).rowcount
            zahlen["zaehleintraege"] = 0
    zahlen["dateien"] = dateien
    with verbindung() as c:
        loeschprotokoll_schreiben(c, "vertragsende", mandant_ref, None, {k: v for k, v in zahlen.items() if k != "loeschprotokoll"})
    return zahlen


def erinnerungen_planen(c, mandant_ref: str, vorgang_id: int, ab_dt) -> None:
    """Legt die sechs Erinnerungen (48/72/96/120/144/156 h nach ``ab_dt``) neu an. Alte Zeilen werden verworfen (neuer Link = neue Zählung)."""
    c.execute("DELETE FROM erinnerung WHERE vorgang_id=? AND mandant_ref=?", (int(vorgang_id), mandant_ref))
    for nr, stunden in enumerate(config.ERINNERUNG_STUNDEN, 1):
        faellig = (ab_dt + timedelta(hours=stunden)).strftime("%Y-%m-%dT%H:%M:%SZ")
        c.execute("INSERT INTO erinnerung(vorgang_id, mandant_ref, nr, faellig_am, versuche) VALUES (?,?,?,?,0)", (int(vorgang_id), mandant_ref, nr, faellig))


def token_entziehen(c, vorgang_id: int | None = None, link_id: int | None = None) -> int:
    """Löscht das wiederherstellbare Token (token_enc) eines Links oder aller Links eines Vorgangs. Der Hash bleibt (für die Sperrprüfung). Liefert die Zahl der Zeilen."""
    if link_id is not None:
        return c.execute("UPDATE link SET token_enc=NULL WHERE id=? AND token_enc IS NOT NULL", (int(link_id),)).rowcount
    return c.execute("UPDATE link SET token_enc=NULL WHERE vorgang_id=? AND token_enc IS NOT NULL", (int(vorgang_id),)).rowcount


def link_status(mandant_ref: str, vorgang_id: int) -> str | None:
    """'aktiv' | 'gesperrt' | 'abgelaufen' | 'verbraucht' | None (noch kein Link). Für den Hinweis an die Beratungsstelle."""
    with verbindung() as c:
        zeilen = c.execute("SELECT gesperrt, verbraucht_am, laeuft_ab FROM link WHERE mandant_ref=? AND vorgang_id=? ORDER BY id DESC", (mandant_ref, int(vorgang_id))).fetchall()
    if not zeilen:
        return None
    return _link_status_aus(zeilen[0], jetzt_text())


def link_laeuft_ab(mandant_ref: str, vorgang_id: int) -> str | None:
    """Ablaufzeitpunkt des neuesten Links (UTC-Text) oder None, wenn es noch keinen gibt."""
    with verbindung() as c:
        z = c.execute("SELECT laeuft_ab FROM link WHERE mandant_ref=? AND vorgang_id=? ORDER BY id DESC LIMIT 1", (mandant_ref, int(vorgang_id))).fetchone()
    return z["laeuft_ab"] if z else None


def _link_status_aus(z, jetzt: str) -> str:
    if z["verbraucht_am"]:
        return "verbraucht"
    if z["gesperrt"]:
        return "gesperrt"
    return "abgelaufen" if z["laeuft_ab"] <= jetzt else "aktiv"


def jetzt_text() -> str:
    return jetzt()


def stelle_einstellung_lesen(mandant_ref: str) -> dict | None:
    with verbindung() as c:
        z = c.execute("SELECT verantwortlicher, datenschutz_link, geaendert_am, aufbewahrung_tage, mitglied_download_link FROM stelle_einstellung WHERE mandant_ref=?", (mandant_ref,)).fetchone()
    return dict(z) if z else None
