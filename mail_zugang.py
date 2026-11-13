"""Mail-Zugang je Beratungsstelle (Server, Port, Benutzer, Passwort, Absender, Signatur).

Das Passwort wird verschlüsselt gespeichert (schluessel.py) und ist nur schreibbar: Es erscheint nie in einer Antwort, einem Log, einem Fehlertext oder im Audit.
Jeder Lesezugriff des Dienstes auf das Passwort (Versand, Test) wird protokolliert (Zweck, ohne Wert)."""
import re

import db
import mailer
import schluessel

ZWECK = "mailpasswort"
MAX_SIGNATUR = 1000
STEUERZEICHEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Ungueltig(Exception):
    """Eingabefehler mit Anzeigetext (ohne Zugangsdaten)."""

    def __init__(self, text: str, feld: str | None = None):
        super().__init__(text)
        self.text = text
        self.feld = feld


def _text(wert, feld: str, maxlaenge: int, pflicht: bool = True) -> str:
    if wert is None and not pflicht:
        return ""
    if not isinstance(wert, str):
        raise Ungueltig("Bitte einen Text angeben.", feld)
    wert = wert.strip()
    if pflicht and not wert:
        raise Ungueltig("Bitte ausfüllen.", feld)
    if len(wert) > maxlaenge or STEUERZEICHEN.search(wert):
        raise Ungueltig("Der Wert ist zu lang oder enthält ungültige Zeichen.", feld)
    return wert


def _einzeilig(wert: str, feld: str) -> str:
    if "\n" in wert or "\r" in wert:
        raise Ungueltig("Bitte nur eine Zeile.", feld)
    return wert


def sicht(mandant_ref: str) -> dict:
    """Was die Oberfläche sehen darf: alles außer dem Passwort."""
    with db.verbindung() as c:
        z = c.execute("SELECT host, port, benutzer, absender_adresse, absender_name, signatur, bcc_an_absender, geprueft_am, geaendert_am "
                      "FROM mail_zugang WHERE mandant_ref=?", (mandant_ref,)).fetchone()
    if not z:
        return {"konfiguriert": False}
    d = dict(z)
    d["bcc_an_absender"] = bool(d["bcc_an_absender"])
    d.update({"konfiguriert": True, "passwort_gesetzt": True})
    return d


def setzen(mandant_ref: str, eingabe: dict, nutzer: str) -> dict:
    """Legt den Zugang an oder ändert ihn. Fehlt ``passwort`` bei einer Änderung, bleibt das alte erhalten. Jede Änderung hebt die Prüfung auf."""
    if not isinstance(eingabe, dict):
        raise Ungueltig("Objekt erwartet.")
    bekannt = {"host", "port", "benutzer", "passwort", "absender_adresse", "absender_name", "signatur", "bcc_an_absender"}
    fremd = set(eingabe) - bekannt
    if fremd:
        raise Ungueltig("Unbekannte Felder: %s" % ", ".join(sorted(fremd)))
    with db.verbindung() as c:
        alt = c.execute("SELECT * FROM mail_zugang WHERE mandant_ref=?", (mandant_ref,)).fetchone()
    alt = dict(alt) if alt else None

    def nimm(feld, standard=None):
        return eingabe[feld] if feld in eingabe else (alt[feld] if alt and feld in alt else standard)

    try:
        host, port, _ = mailer.ziel_pruefen(nimm("host"), nimm("port"))
    except ValueError as e:
        raise Ungueltig(str(e), "host")
    benutzer = _einzeilig(_text(nimm("benutzer"), "benutzer", 254), "benutzer")
    absender = _einzeilig(_text(nimm("absender_adresse"), "absender_adresse", 254), "absender_adresse").lower()
    if not mailer.adresse_gueltig(absender):
        raise Ungueltig("Die Absenderadresse ist ungültig.", "absender_adresse")
    name = _einzeilig(_text(nimm("absender_name", ""), "absender_name", 100, pflicht=False), "absender_name")
    if re.search(r"[<>\"\\]", name):
        raise Ungueltig("Der Absendername enthält ungültige Zeichen.", "absender_name")
    signatur = _text(nimm("signatur", ""), "signatur", MAX_SIGNATUR, pflicht=False)
    bcc = nimm("bcc_an_absender", False)
    if bcc not in (True, False, 0, 1):
        raise Ungueltig("bcc_an_absender: true oder false.", "bcc_an_absender")
    if "passwort" in eingabe:
        pw = eingabe["passwort"]
        if not isinstance(pw, str) or not (1 <= len(pw) <= 256) or "\n" in pw or "\r" in pw:
            raise Ungueltig("Das Passwort ist ungültig.", "passwort")
        pw_enc = schluessel.verschluesseln(mandant_ref, ZWECK, pw)
    elif alt:
        pw_enc = alt["passwort_enc"]
    else:
        raise Ungueltig("Bitte das Passwort angeben.", "passwort")
    with db.verbindung() as c:
        c.execute("INSERT INTO mail_zugang(mandant_ref, host, port, benutzer, passwort_enc, absender_adresse, absender_name, signatur, bcc_an_absender, geprueft_am, "
                  "geaendert_am, geaendert_von) VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?) "
                  "ON CONFLICT(mandant_ref) DO UPDATE SET host=excluded.host, port=excluded.port, benutzer=excluded.benutzer, passwort_enc=excluded.passwort_enc, "
                  "absender_adresse=excluded.absender_adresse, absender_name=excluded.absender_name, signatur=excluded.signatur, bcc_an_absender=excluded.bcc_an_absender, "
                  "geprueft_am=NULL, geaendert_am=excluded.geaendert_am, geaendert_von=excluded.geaendert_von",
                  (mandant_ref, host, port, benutzer, pw_enc, absender, name, signatur, int(bool(bcc)), db.jetzt(), nutzer))
    return sicht(mandant_ref)


def loeschen(mandant_ref: str) -> bool:
    with db.verbindung() as c:
        return c.execute("DELETE FROM mail_zugang WHERE mandant_ref=?", (mandant_ref,)).rowcount == 1


def fuer_dienst(mandant_ref: str, zweck: str, nutzer: str) -> dict | None:
    """Zugang MIT Klartext-Passwort für Versand oder Test. Der Zugriff wird protokolliert (Zweck, ohne Wert). Nie an eine Antwort weiterreichen."""
    with db.verbindung() as c:
        z = c.execute("SELECT * FROM mail_zugang WHERE mandant_ref=?", (mandant_ref,)).fetchone()
    if not z:
        return None
    z = dict(z)
    z["passwort"] = schluessel.entschluesseln(mandant_ref, ZWECK, z["passwort_enc"])
    db.audit(mandant_ref, nutzer, "mailzugang_gelesen", "zweck=%s" % zweck, "")
    return z


def pruefen(mandant_ref: str, nutzer: str) -> dict:
    """Testmail nur an die Absenderadresse der Beratungsstelle selbst. Bei Erfolg wird der Zugang als geprüft vermerkt."""
    z = fuer_dienst(mandant_ref, "test", nutzer)
    if z is None:
        return {"ok": False, "fehler": "nicht_konfiguriert"}
    m = mailer.nachricht_bauen(z["absender_adresse"], z["absender_name"], z["absender_adresse"], "SignNext: Test Ihres Mail-Zugangs",
                               "Guten Tag,\n\ndiese Nachricht bestätigt, dass SignNext über Ihr Postfach Mails versenden kann.\nSie müssen nichts weiter tun.\n")
    try:
        mailer.senden(z, [z["absender_adresse"]], m)
    except mailer.MailFehler as f:
        return {"ok": False, "fehler": f.code}
    finally:
        z["passwort"] = None
    with db.verbindung() as c:
        c.execute("UPDATE mail_zugang SET geprueft_am=? WHERE mandant_ref=?", (db.jetzt(), mandant_ref))
    return {"ok": True}
