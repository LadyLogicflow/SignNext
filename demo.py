"""Demo-Daten für Testinstanzen (``manage.py demo``): eine Demo-Beratungsstelle mit einem versendeten Vorgang aus erfundenen PDFs. KEINE Mail, nur der Link zum Ausprobieren.

Nur mit SIGNNEXT_DEMO=1 (nie in der Produktion setzen). Der Link und das Geburtsdatum werden auf der Konsole ausgegeben; alles ist erfunden. Die Demo-Vorgänge laufen wie
echte durch den normalen Ablauf (Code-Prüfung, Lesen, Unterschrift, Abschluss, Download) und werden von den Hintergrund-Jobs wie echte gelöscht."""
import os
from datetime import datetime, timedelta, timezone

import config
import db
import demo_daten
import pdf_eingang
import pruefung
import schluessel

VORLAGE_ANGABEN = ("Demo-Beratungsstelle (Testdaten), Musterweg 1, 12345 Musterstadt, demo@example.org", "https://example.org/datenschutz")


class DemoFehler(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _pruefen(mandant_ref: str) -> None:
    if os.environ.get("SIGNNEXT_DEMO", "") != "1":
        raise DemoFehler("demo_nicht_erlaubt")
    if config.hauptschluessel() is None:
        raise DemoFehler("hauptschluessel_fehlt")
    if not db.REFERENZ.match(mandant_ref or ""):
        raise DemoFehler("mandant_ungueltig")


def _link(pfad: str, token: str) -> str:
    basis = config.basis_url()
    return "%s%s#%s" % (basis or "", pfad, token)


def vorgang_erzeugen(mandant_ref: str = "demo") -> dict:
    """Legt einen versendeten Demo-Vorgang an. Liefert {vorgang_id, link, geburtsdatum, code}."""
    _pruefen(mandant_ref)
    with db.verbindung() as c:
        c.execute("INSERT OR IGNORE INTO stelle_einstellung(mandant_ref, verantwortlicher, datenschutz_link, geaendert_am, geaendert_von) VALUES (?,?,?,?,?)",
                  (mandant_ref, VORLAGE_ANGABEN[0], VORLAGE_ANGABEN[1], db.jetzt(), "demo"))
    ergebnis = pdf_eingang.anlegen(mandant_ref, [demo_daten.b_pdf(), demo_daten.a_pdf()])
    vid = ergebnis["vorgang_id"]
    if ergebnis["status"] != "bereit":
        raise DemoFehler("demo_vorgang_nicht_bereit")
    v = db.vorgang_intern(mandant_ref, vid)
    e = pruefung.effektiv(mandant_ref, v, pruefung.pdf_werte(mandant_ref, vid))
    if pruefung.fehlend(e):
        raise DemoFehler("demo_angaben_fehlen")
    token, token_hash = schluessel.neues_token()
    jetzt = datetime.now(timezone.utc)
    laeuft_ab = jetzt.replace(microsecond=0) + timedelta(days=config.LINK_TAGE)
    with db.verbindung() as c:
        c.execute("INSERT INTO link(vorgang_id, mandant_ref, token_hash, erstellt, laeuft_ab, token_enc) VALUES (?,?,?,?,?,?)",
                  (vid, mandant_ref, token_hash, jetzt.strftime("%Y-%m-%dT%H:%M:%SZ"), laeuft_ab.strftime("%Y-%m-%dT%H:%M:%SZ"), schluessel.verschluesseln(mandant_ref, "link-token", token)))
        db.erinnerungen_planen(c, mandant_ref, vid, jetzt)
    db.vorgang_status_wechseln(mandant_ref, vid, "bereit", "versendet", versendet_am=db.jetzt(), versand_fehler=None,
                               email_enc=schluessel.verschluesseln(mandant_ref, "mitglied-mail", e["email"]), email_herkunft=e["email_herkunft"],
                               code_hmac=e["code_hmac"], code_kid=e["code_kid"], datum_herkunft=e["datum_herkunft"], personen=e["personen"], personen_herkunft=e["personen_herkunft"])
    db.audit(mandant_ref, "demo", "demo_vorgang", "vorgang=%d" % vid)
    return {"vorgang_id": vid, "link": _link("/u", token), "geburtsdatum": demo_daten.DOB, "code": demo_daten.CODE, "personen": e["personen"]}


def download_link_erzeugen(vorgang_id: int, mandant_ref: str = "demo") -> dict:
    """Für einen abgeschlossenen Demo-Vorgang einen Download-Link ausgeben (sonst kommt er per Mail; die gibt es in der Testinstanz nicht)."""
    _pruefen(mandant_ref)
    v = db.vorgang_intern(mandant_ref, vorgang_id)
    if v is None or v["status"] != "abgeschlossen":
        raise DemoFehler("vorgang_nicht_abgeschlossen")
    import download
    with db.verbindung() as c:
        token = download.link_erzeugen(c, mandant_ref, vorgang_id)
        db.audit_in(c, mandant_ref, "demo", "demo_download_link", "vorgang=%d" % int(vorgang_id))
    return {"vorgang_id": int(vorgang_id), "link": _link("/d", token), "geburtsdatum": demo_daten.DOB, "code": demo_daten.CODE}
