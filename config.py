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


def versand_modus() -> str:
    """'aus' (Standard, fail closed): es wird nichts versendet. 'testadressen': nur an Adressen aus SIGNNEXT_VERSAND_TESTADRESSEN. 'an': Echtversand.
    'an' setzt nur der Betreiber, nach der Freigabe durch catrin und nach dem Produktionsbeleg (echte Testmail mit geprüfter Verbindung)."""
    wert = os.environ.get("SIGNNEXT_VERSAND", "")
    return wert if wert in ("testadressen", "an") else "aus"


def versand_testadressen() -> set:
    return {a.strip().lower() for a in os.environ.get("SIGNNEXT_VERSAND_TESTADRESSEN", "").split(",") if a.strip()}


def aufbewahrung_tage() -> int:
    """Wie lange unterschriebenes Dokument und Protokoll nach der Unterschrift aufbewahrt werden (Standard 90). Steht so in der Datenschutzinformation."""
    try:
        return max(1, int(os.environ.get("SIGNNEXT_AUFBEWAHRUNG_TAGE", "90")))
    except ValueError:
        return 90


def ip_speicherung() -> str:
    """'voll' (Standard, laut Anforderung: IP nur im Nachweisprotokoll) oder 'gekuerzt'. Steht so in der Datenschutzinformation."""
    return "gekuerzt" if os.environ.get("SIGNNEXT_IP_SPEICHERUNG", "") == "gekuerzt" else "voll"


# --- Baustein 5: Fristen und Takte (alle Zeiten in Stunden/Tagen nach dem jeweiligen Ereignis) ---
ERINNERUNG_STUNDEN = (48, 72, 96, 120, 144, 156)         # nach dem Versand des Links
VORWARNUNG_STUNDEN = 24                                  # vor dem Ablauf des Links (Löschung nicht unterschriebener Vorgänge)
LINK_TAGE = 7
NICHT_VERSENDET_TAGE = 7                                 # hochgeladen, nie versendet: nach dieser Zeit gelöscht (Vorwarnung 24 Stunden vorher)
AUFBEWAHRUNG_MIN_TAGE = 30
AUFBEWAHRUNG_MAX_TAGE = 365
VORWARNUNG_AUFBEWAHRUNG_TAGE = 14                        # Erinnerung an die Beratungsstelle vor der Löschung des unterschriebenen Dokuments
STALE_MINUTEN = 10                                       # so lange darf ein Vorgang in 'versenden' oder 'abschluss_laeuft' stehen
ABSCHLUSS_VERSUCHE_MAX = 5
MITGLIED_MAIL_VERSUCHE_MAX = 3
ERINNERUNG_VERSUCHE_MAX = 3
ERINNERUNG_ABSTAND_MINUTEN = 5
DOWNLOAD_TAGE = 7
DOWNLOAD_MAX_ABRUFE = 3
JOB_INTERVALL_SEKUNDEN = 60


def aufbewahrung_standard() -> int:
    """Voreinstellung, solange die Beratungsstelle keine eigene Frist gesetzt hat (SIGNNEXT_AUFBEWAHRUNG_TAGE, Standard 90)."""
    return min(AUFBEWAHRUNG_MAX_TAGE, max(AUFBEWAHRUNG_MIN_TAGE, aufbewahrung_tage()))


def betreiber_kontakt() -> str:
    """Kontaktweg des Betreibers für den Hinweis bei einem unerwarteten Abruf (SIGNNEXT_BETREIBER_KONTAKT, ohne Zeilenumbrüche). Leer = ohne Angabe."""
    return " ".join(os.environ.get("SIGNNEXT_BETREIBER_KONTAKT", "").split())[:200]


# --- Baustein 6: Zählung und Tarif (SignNext rechnet nichts ab und stellt keine Rechnung; Preise sind nur ein einstellbarer Wert) ---
TARIF_NAMEN = ("basis", "plus")
TARIF_VORGABE = {"basis": (15, 0), "plus": (None, 0)}          # (Limit je Kalendermonat, Preis in Cent)


def tarif_plus_referenzen() -> frozenset:
    """Beratungsstellen, die von Anfang an im Tarif plus sind (SIGNNEXT_TARIF_PLUS_REFERENZEN, kommagetrennt; Standard: der Pilot 282874; leer = keine)."""
    roh = os.environ.get("SIGNNEXT_TARIF_PLUS_REFERENZEN", "282874")
    return frozenset(r for r in (x.strip() for x in roh.split(",")) if r and all(ch.isalnum() or ch in "_-" for ch in r) and len(r) <= 64)


def sperre_modus() -> str:
    """SIGNNEXT_SPERRE: 'auto' (Standard: Sperre am Limit nur, wenn der Preis des Tarifs größer 0 ist), 'an' (immer am Limit), 'aus' (nie, nur zählen)."""
    wert = os.environ.get("SIGNNEXT_SPERRE", "auto")
    return wert if wert in ("auto", "an", "aus") else "auto"


def upgrade_hinweis() -> str:
    """Optionaler kurzer Zusatz zur Meldung bei erreichtem Kontingent (SIGNNEXT_UPGRADE_HINWEIS, ohne Zeilenumbrüche). Leer = ohne."""
    return " ".join(os.environ.get("SIGNNEXT_UPGRADE_HINWEIS", "").split())[:200]
