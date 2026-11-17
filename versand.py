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
import tarif

LINK_TAGE = config.LINK_TAGE
BETREFF = "Ihre Beratungsstelle: Dokument zur Unterschrift bereit"
TEXT = (
    "Guten Tag,\n\n"
    "Ihre Beratungsstelle hat ein Dokument für Sie zur Unterschrift bereitgestellt.\n\n"
    "Bitte öffnen Sie es über diesen persönlichen Link (7 Tage gültig, nur für Sie bestimmt, bitte nicht weiterleiten):\n"
    "{link}\n\n"
    "Zum Öffnen benötigen Sie das Geburtsdatum der Person, für die das Dokument erstellt wurde, bei Ehepaaren und eingetragenen Lebenspartnerschaften "
    "der zuerst genannten Person (sechs Ziffern: Tag, Monat, zweistelliges Jahr, ohne Punkte).\n\n"
    "Haben Sie diese Nachricht nicht erwartet, öffnen Sie den Link bitte nicht und rufen Sie Ihre Beratungsstelle an.\n\n"
    "Wenn Sie nicht unterschreiben möchten oder Fragen haben, wenden Sie sich bitte an Ihre Beratungsstelle.\n"
)
FEHLER_STATUS = {"tls_zertifikat": 502, "tls_nicht_angeboten": 502, "tls_fehler": 502, "verbindung": 502, "anmeldung": 502, "absender_abgelehnt": 502, "empfaenger_abgelehnt": 422, "abgelehnt": 502, "konfiguration": 409}


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


def _zurueck(mandant_ref: str, vorgang_id: int, token_hash: str, code: str, ziel: str = "bereit", zaehlung_zurueck: bool = False) -> None:
    """Eindeutiger Fehler vor der Zustellung: Link verwerfen, Zustand zurück auf ``ziel`` (neuer Versuch gefahrlos). War es der erste Versand dieses Vorgangs,
    wird auch die Zählung zurückgenommen (``zaehlung_zurueck``); bei unklarem Versand bleibt sie."""
    with db.verbindung() as c:
        c.execute("DELETE FROM link WHERE token_hash=? AND mandant_ref=?", (token_hash, mandant_ref))
    _versand_eintragen(mandant_ref, vorgang_id, "fehlgeschlagen", code)
    zusatz = {"gezaehlt_am": None, "zaehlmonat": None} if zaehlung_zurueck else {}
    db.vorgang_status_wechseln(mandant_ref, vorgang_id, "versenden", ziel, versand_fehler=code, **zusatz)


def versenden(mandant_ref: str, vorgang_id: int, nutzer: str) -> dict:
    """Erstversand des Zugangslinks. Wirft Fehler. Liefert nie den Link oder das Token."""
    return _senden(mandant_ref, vorgang_id, nutzer, neu=False)


def neuer_link(mandant_ref: str, vorgang_id: int, nutzer: str) -> dict:
    """Neuer Zugangslink durch die Beratungsstelle (z. B. nach Sperre oder Ablauf): alle alten Links werden ungültig. Nur solange nicht unterschrieben wurde."""
    return _senden(mandant_ref, vorgang_id, nutzer, neu=True)


def versenden_demo_ohne_mail(mandant_ref: str, vorgang_id: int, nutzer: str) -> dict:
    """Nur Demo (catrin #19330): wie ``versenden``, aber ohne Mailserver; der Link steht in der Antwort (``demo_link``) und wird am Bildschirm gezeigt (Fallback, wenn die Mail scheitert)."""
    if not config.demo_aktiv():
        raise Fehler("nicht_gefunden", 404)
    return _senden(mandant_ref, vorgang_id, nutzer, neu=False, demo_ohne_mail=True)


def _senden(mandant_ref: str, vorgang_id: int, nutzer: str, neu: bool, demo_ohne_mail: bool = False) -> dict:
    v = db.vorgang_intern(mandant_ref, vorgang_id)
    if v is None:
        raise Fehler("nicht_gefunden", 404)
    if v["status"] == "versenden":
        raise Fehler("versand_laeuft_oder_unklar")
    if neu:
        if v["status"] == "unterschrieben":
            raise Fehler("schon_unterschrieben")
        if v["status"] not in ("versendet", "versand_unklar"):
            raise Fehler("zustand_passt_nicht")
    else:
        if v["status"] in ("versendet", "versand_unklar"):
            raise Fehler("schon_versendet" if v["status"] == "versendet" else "versand_laeuft_oder_unklar")
        if v["status"] != "bereit":
            raise Fehler("zustand_passt_nicht")
    ausgang = v["status"]
    if config.versand_modus() == "aus":
        raise Fehler("versand_gesperrt", 503)
    basis = config.basis_url()
    if basis is None:
        raise Fehler("basis_url_fehlt", 503)
    if config.hauptschluessel() is None:
        raise Fehler("nicht_bereit", 503)
    zsicht = mail_zugang.sicht(mandant_ref)
    if not demo_ohne_mail:
        if not zsicht["konfiguriert"]:
            raise Fehler("mail_zugang_fehlt")
        if not zsicht.get("geprueft_am"):
            raise Fehler("mail_zugang_ungeprueft")
    if db.stelle_einstellung_lesen(mandant_ref) is None:
        raise Fehler("stelleneinstellungen_fehlen")
    seit = _zeit(datetime.now(timezone.utc) - timedelta(hours=1))
    with db.verbindung() as c:
        n = c.execute("SELECT COUNT(*) FROM versand WHERE mandant_ref=? AND zeit>=?", (mandant_ref, seit)).fetchone()[0]
    if n >= config.VERSAND_JE_STUNDE:
        raise Fehler("zu_viele_versaende", 429)
    e = pruefung.effektiv(mandant_ref, v, pruefung.pdf_werte(mandant_ref, vorgang_id))
    fehlt = pruefung.fehlend(e)
    if fehlt:
        raise Fehler("angaben_fehlen", 409, fehlt)
    if not demo_ohne_mail and config.versand_modus() == "testadressen" and e["email"] not in config.versand_testadressen():
        raise Fehler("empfaenger_nicht_freigegeben")
    erst = v["gezaehlt_am"] is None                                      # erster Versand dieses Vorgangs: wird gezählt, ggf. am Kontingent gesperrt
    anspruch = tarif.beanspruchen(mandant_ref, vorgang_id, ausgang)
    if anspruch == "kontingent":
        raise Fehler("kontingent_erreicht", 409)
    if anspruch != "ok":
        raise Fehler("versand_laeuft_oder_unklar")
    token, token_hash = schluessel.neues_token()
    jetzt = datetime.now(timezone.utc)
    laeuft_ab = jetzt + timedelta(days=LINK_TAGE)
    with db.verbindung() as c:
        # token_enc: wiederherstellbares Token nur für die Erinnerungen; wird bei Unterschrift, Sperre, Ablauf und Storno sofort gelöscht (db.token_entziehen).
        c.execute("INSERT INTO link(vorgang_id, mandant_ref, token_hash, erstellt, laeuft_ab, token_enc) VALUES (?,?,?,?,?,?)",
                  (int(vorgang_id), mandant_ref, token_hash, _zeit(jetzt), _zeit(laeuft_ab), schluessel.verschluesseln(mandant_ref, "link-token", token)))
    if demo_ohne_mail:                                           # nur Demo: kein Mailserver, der Link wird am Bildschirm gezeigt
        message_id = "demo-ohne-mail"
    else:
        try:
            zugang = mail_zugang.fuer_dienst(mandant_ref, "versand", nutzer)
        except schluessel.Entschluesselung:
            _zurueck(mandant_ref, vorgang_id, token_hash, "mail_zugang_nicht_lesbar", ausgang, erst)
            raise Fehler("mail_zugang_nicht_lesbar", 503)
        except Exception:
            _zurueck(mandant_ref, vorgang_id, token_hash, "intern", ausgang, erst)
            raise
        # Der Link steht nach dem # (Fragment): Der Browser schickt ihn nie an den Server, er landet in keinem Zugriffsprotokoll von SignNext oder des Proxys.
        text = TEXT.format(link="%s/u#%s" % (basis, token))
        if zugang["signatur"]:
            text += "\n-- \n%s\n" % zugang["signatur"]
        nachricht = mailer.nachricht_bauen(zugang["absender_adresse"], zugang["absender_name"], e["email"], BETREFF, text)
        empfaenger = [e["email"]] + ([zugang["absender_adresse"]] if zugang["bcc_an_absender"] else [])
        try:
            message_id = mailer.senden(zugang, empfaenger, nachricht)
        except mailer.MailFehler as f:
            if f.definitiv:
                _zurueck(mandant_ref, vorgang_id, token_hash, f.code, ausgang, erst)
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
    _versand_eintragen(mandant_ref, vorgang_id, "gesendet", "neuer_link" if neu else "", message_id)
    versendet_am = db.jetzt()
    if neu:
        with db.verbindung() as c:
            c.execute("UPDATE link SET gesperrt=1, token_enc=NULL WHERE vorgang_id=? AND mandant_ref=? AND token_hash<>?", (int(vorgang_id), mandant_ref, token_hash))
        db.vorgang_status_wechseln(mandant_ref, vorgang_id, "versenden", "versendet", versendet_am=versendet_am, versand_fehler=None, vorwarnung_am=None)
    else:
        db.vorgang_status_wechseln(
            mandant_ref, vorgang_id, "versenden", "versendet", versendet_am=versendet_am, versand_fehler=None,
            email_enc=schluessel.verschluesseln(mandant_ref, "mitglied-mail", e["email"]), email_herkunft=e["email_herkunft"],
            code_hmac=e["code_hmac"], code_kid=e["code_kid"], datum_herkunft=e["datum_herkunft"], personen=e["personen"], personen_herkunft=e["personen_herkunft"])
    with db.verbindung() as c:
        db.erinnerungen_planen(c, mandant_ref, vorgang_id, jetzt)
    antwort = {"vorgang_id": int(vorgang_id), "status": "versendet", "versendet_am": versendet_am, "link_laeuft_ab": _zeit(laeuft_ab), "neuer_link": neu}
    if config.demo_aktiv():                                      # nur Demo: Link für die Anzeige am Bildschirm (sonst nie in einer Antwort)
        antwort["demo_link"] = "%s/u#%s" % (basis, token)
    return antwort
