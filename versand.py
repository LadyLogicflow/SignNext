"""Versand des Zugangslinks an das Mitglied, vom Postfach der Beratungsstelle, nur nach ausdrücklicher Bestätigung (nie automatisch).

Ablauf: Zustand 'bereit' → 'versenden' (atomar, nur ein Aufruf gewinnt) → Mail → 'versendet'. Scheitert der Versand eindeutig vor der Übergabe an den Mailserver,
geht der Vorgang zurück auf 'bereit' (Link verworfen, neuer Versuch gefahrlos). Bricht die Verbindung nach dem Beginn der Datenübertragung ab, steht der Vorgang auf
'versand_unklar' und wird nie automatisch wiederholt. Der Link (Token, 256 Bit) steht nur in der Mail; gespeichert wird nur sein SHA-256."""
from datetime import datetime, timedelta, timezone

import config
import db
import mail_zugang
import mailer
import pruefung
import schluessel

LINK_TAGE = 7
BETREFF = "Ihre Beratungsstelle: Dokument zur Unterschrift bereit"
TEXT = (
    "Guten Tag,\n\n"
    "Ihre Beratungsstelle hat ein Dokument für Sie zur Unterschrift bereitgestellt.\n\n"
    "Bitte öffnen Sie es über diesen persönlichen Link (7 Tage gültig, nur für Sie bestimmt, bitte nicht weiterleiten):\n"
    "{link}\n\n"
    "Zum Öffnen benötigen Sie Ihr Geburtsdatum als sechs Ziffern (Tag, Monat, zweistelliges Jahr, ohne Punkte).\n\n"
    "Wenn Sie nicht unterschreiben möchten oder Fragen haben, wenden Sie sich bitte an Ihre Beratungsstelle.\n"
)
FEHLER_STATUS = {"tls": 502, "verbindung": 502, "anmeldung": 502, "absender_abgelehnt": 502, "empfaenger_abgelehnt": 422, "abgelehnt": 502, "konfiguration": 409}


class Fehler(Exception):
    def __init__(self, code: str, status: int = 409, einzelheiten: list | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.einzelheiten = einzelheiten


def _zeit(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _versand_eintragen(mandant_ref: str, vorgang_id: int, ergebnis: str, code: str = "", message_id: str = "") -> None:
    with db.verbindung() as c:
        c.execute("INSERT INTO versand(vorgang_id, mandant_ref, zeit, ergebnis, code, message_id) VALUES (?,?,?,?,?,?)",
                  (int(vorgang_id), mandant_ref, db.jetzt(), ergebnis, code, message_id))


def _zurueck_auf_bereit(mandant_ref: str, vorgang_id: int, token_hash: str, code: str) -> None:
    with db.verbindung() as c:
        c.execute("DELETE FROM link WHERE token_hash=? AND mandant_ref=?", (token_hash, mandant_ref))
    _versand_eintragen(mandant_ref, vorgang_id, "fehlgeschlagen", code)
    db.vorgang_status_wechseln(mandant_ref, vorgang_id, "versenden", "bereit", versand_fehler=code)


def versenden(mandant_ref: str, vorgang_id: int, nutzer: str) -> dict:
    """Wirft Fehler. Liefert nie den Link oder das Token."""
    v = db.vorgang_intern(mandant_ref, vorgang_id)
    if v is None:
        raise Fehler("nicht_gefunden", 404)
    if v["status"] in ("versendet", "versenden", "versand_unklar"):
        raise Fehler("schon_versendet" if v["status"] == "versendet" else "versand_laeuft_oder_unklar")
    if v["status"] != "bereit":
        raise Fehler("zustand_passt_nicht")
    basis = config.basis_url()
    if basis is None:
        raise Fehler("basis_url_fehlt", 503)
    if config.hauptschluessel() is None:
        raise Fehler("nicht_bereit", 503)
    if not mail_zugang.sicht(mandant_ref)["konfiguriert"]:
        raise Fehler("mail_zugang_fehlt")
    seit = _zeit(datetime.now(timezone.utc) - timedelta(hours=1))
    with db.verbindung() as c:
        n = c.execute("SELECT COUNT(*) FROM versand WHERE mandant_ref=? AND zeit>=?", (mandant_ref, seit)).fetchone()[0]
    if n >= config.VERSAND_JE_STUNDE:
        raise Fehler("zu_viele_versaende", 429)
    e = pruefung.effektiv(mandant_ref, v, pruefung.pdf_werte(mandant_ref, vorgang_id))
    fehlt = pruefung.fehlend(e)
    if fehlt:
        raise Fehler("angaben_fehlen", 409, fehlt)
    if not db.vorgang_status_wechseln(mandant_ref, vorgang_id, "bereit", "versenden"):
        raise Fehler("versand_laeuft_oder_unklar")
    token, token_hash = schluessel.neues_token()
    jetzt = datetime.now(timezone.utc)
    laeuft_ab = jetzt + timedelta(days=LINK_TAGE)
    with db.verbindung() as c:
        c.execute("INSERT INTO link(vorgang_id, mandant_ref, token_hash, erstellt, laeuft_ab) VALUES (?,?,?,?,?)",
                  (int(vorgang_id), mandant_ref, token_hash, _zeit(jetzt), _zeit(laeuft_ab)))
    try:
        zugang = mail_zugang.fuer_dienst(mandant_ref, "versand", nutzer)
    except schluessel.Entschluesselung:
        _zurueck_auf_bereit(mandant_ref, vorgang_id, token_hash, "mail_zugang_nicht_lesbar")
        raise Fehler("mail_zugang_nicht_lesbar", 503)
    except Exception:
        _zurueck_auf_bereit(mandant_ref, vorgang_id, token_hash, "intern")
        raise
    text = TEXT.format(link="%s/u/%s" % (basis, token))
    if zugang["signatur"]:
        text += "\n-- \n%s\n" % zugang["signatur"]
    nachricht = mailer.nachricht_bauen(zugang["absender_adresse"], zugang["absender_name"], e["email"], BETREFF, text)
    empfaenger = [e["email"]] + ([zugang["absender_adresse"]] if zugang["bcc_an_absender"] else [])
    try:
        message_id = mailer.senden(zugang, empfaenger, nachricht)
    except mailer.MailFehler as f:
        if f.definitiv:
            _zurueck_auf_bereit(mandant_ref, vorgang_id, token_hash, f.code)
            raise Fehler("mail_" + f.code, FEHLER_STATUS.get(f.code, 502))
        _versand_eintragen(mandant_ref, vorgang_id, "unklar", "unklar")
        db.vorgang_status_wechseln(mandant_ref, vorgang_id, "versenden", "versand_unklar", versand_fehler="unklar")
        raise Fehler("versand_unklar", 502)
    except Exception:
        _versand_eintragen(mandant_ref, vorgang_id, "unklar", "intern")
        db.vorgang_status_wechseln(mandant_ref, vorgang_id, "versenden", "versand_unklar", versand_fehler="unklar")
        raise Fehler("versand_unklar", 502)
    finally:
        zugang["passwort"] = None
    _versand_eintragen(mandant_ref, vorgang_id, "gesendet", "", message_id)
    versendet_am = db.jetzt()
    db.vorgang_status_wechseln(
        mandant_ref, vorgang_id, "versenden", "versendet", versendet_am=versendet_am, versand_fehler=None,
        email_enc=schluessel.verschluesseln(mandant_ref, "mitglied-mail", e["email"]), email_herkunft=e["email_herkunft"],
        code_hmac=e["code_hmac"], code_kid=e["code_kid"], datum_herkunft=e["datum_herkunft"], personen=e["personen"], personen_herkunft=e["personen_herkunft"])
    return {"vorgang_id": int(vorgang_id), "status": "versendet", "versendet_am": versendet_am, "link_laeuft_ab": _zeit(laeuft_ab)}
