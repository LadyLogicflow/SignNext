"""Versand der automatischen Mails (Erinnerungen, Bestätigung, Hinweise an die Beratungsstelle) über das Postfach der Beratungsstelle.

Gleiche Regeln wie beim Erstversand (versand.py): Schalter SIGNNEXT_VERSAND (aus = nichts wird gesendet und nichts verbraucht, testadressen = nur freigegebene Adressen),
geprüfter Mail-Zugang, Stundenlimit je Beratungsstelle, TLS-Pflicht im Mailer. Ergebnis immer eines von:
  'gesendet' | 'fehlgeschlagen' (eindeutig nicht zugestellt, Wiederholung gefahrlos) | 'unklar' (nie wiederholen) | 'uebersprungen' (Schalter/Konfiguration, nichts verbraucht) | 'zurueckgestellt' (Limit)."""
from datetime import datetime, timedelta, timezone

import config
import db
import mail_zugang
import mailer
import schluessel


def _zeit(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _eintragen(mandant_ref: str, vorgang_id: int, ergebnis: str, code: str, message_id: str = "") -> None:
    with db.verbindung() as c:
        c.execute("INSERT INTO versand(vorgang_id, mandant_ref, zeit, ergebnis, code, message_id) VALUES (?,?,?,?,?,?)", (int(vorgang_id), mandant_ref, db.jetzt(), ergebnis, code, message_id))


def mitglied_adresse(mandant_ref: str, vorgang: dict) -> str | None:
    """Adresse des Mitglieds, nur für den Versand entschlüsselt. None, wenn keine hinterlegt oder nicht lesbar."""
    if not vorgang.get("email_enc"):
        return None
    try:
        return schluessel.entschluesseln(mandant_ref, "mitglied-mail", vorgang["email_enc"])
    except schluessel.Entschluesselung:
        return None


def senden(mandant_ref: str, vorgang_id: int, art: str, betreff: str, text: str, an: str | None = None) -> str:
    """Sendet eine Mail. ``an=None`` bedeutet: an das eigene Postfach der Beratungsstelle (Hinweise). ``art`` landet als Code in der Versandtabelle (erinnerung, info, ...)."""
    modus = config.versand_modus()
    if modus == "aus" or config.hauptschluessel() is None:
        return "uebersprungen"
    zsicht = mail_zugang.sicht(mandant_ref)
    if not zsicht["konfiguriert"] or not zsicht.get("geprueft_am"):
        return "uebersprungen"
    seit = _zeit(datetime.now(timezone.utc) - timedelta(hours=1))
    with db.verbindung() as c:
        n = c.execute("SELECT COUNT(*) FROM versand WHERE mandant_ref=? AND zeit>=?", (mandant_ref, seit)).fetchone()[0]
    if n >= config.VERSAND_JE_STUNDE:
        return "zurueckgestellt"
    try:
        zugang = mail_zugang.fuer_dienst(mandant_ref, "versand", "system")
    except schluessel.Entschluesselung:
        _eintragen(mandant_ref, vorgang_id, "fehlgeschlagen", art)
        return "fehlgeschlagen"
    if zugang is None:
        return "uebersprungen"
    try:
        empfaenger_adresse = an or zugang["absender_adresse"]
        if modus == "testadressen" and empfaenger_adresse not in config.versand_testadressen():
            return "uebersprungen"
        nachricht = mailer.nachricht_bauen(zugang["absender_adresse"], zugang["absender_name"], empfaenger_adresse, betreff,
                                           text + ("\n-- \n%s\n" % zugang["signatur"] if zugang.get("signatur") else ""))
        try:
            message_id = mailer.senden(zugang, [empfaenger_adresse], nachricht)
        except mailer.MailFehler as f:
            if f.definitiv:
                _eintragen(mandant_ref, vorgang_id, "fehlgeschlagen", art)
                return "fehlgeschlagen"
            _eintragen(mandant_ref, vorgang_id, "unklar", art)
            return "unklar"
        except Exception:
            _eintragen(mandant_ref, vorgang_id, "unklar", art)
            return "unklar"
        _eintragen(mandant_ref, vorgang_id, "gesendet", art, message_id)
        return "gesendet"
    finally:
        zugang["passwort"] = None
