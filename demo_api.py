"""Demo-Schnittstelle für die Beraterseiten (nur wenn SIGNNEXT_DEMO=1; sonst 404). Nur erfundene Daten, nie in der Produktion.

GET  /api/v1/demo                     Zustand der Demo (Versandmodus, erlaubte Testadressen, Mail-Zugang geprüft)
POST /api/v1/demo/vorgang             Demo-Vorgang aus erfundenen A/B-PDFs, mit Link, QR und Geburtsdatum
GET  /api/v1/demo/beispiel/<name>     erfundene Beispiel-PDFs zum Herunterladen und Hochladen
POST /api/v1/demo/testmail {an}       Testmail über den Mail-Zugang, nur an eine Adresse aus SIGNNEXT_VERSAND_TESTADRESSEN
"""
from functools import wraps

from flask import Blueprint, Response, g, jsonify, request

import config
import db
import demo
import demo_daten
import mail_zugang
import mailer
import qr
import schluessel
from portal_auth import anmeldung_pflicht, audit

bp = Blueprint("demo_api", __name__, url_prefix="/api/v1/demo")

BEISPIELE = {
    "anschreiben": ("Anschreiben-B-Demo.pdf", lambda: demo_daten.b_pdf(personen=2)),
    "anschreiben-einzel": ("Anschreiben-B-Demo-Einzelperson.pdf", lambda: demo_daten.b_pdf(personen=1)),
    "protokoll-ehepaar": ("Steuerprotokoll-A-Demo-Ehepaar.pdf", lambda: demo_daten.a_pdf(ehegatte=True)),
    "protokoll-einzel": ("Steuerprotokoll-A-Demo-Einzelperson.pdf", lambda: demo_daten.a_pdf(ehegatte=False)),
}


def nur_demo(f):
    @wraps(f)
    def innen(*a, **kw):
        if not config.demo_aktiv():
            return jsonify({"error": "nicht_gefunden"}), 404
        return f(*a, **kw)
    return innen


@bp.get("")
@nur_demo
@anmeldung_pflicht()
def zustand():
    z = mail_zugang.sicht(g.nutzer["mandant_ref"])
    return jsonify({"demo": True, "versand": config.versand_modus(), "testadressen": sorted(config.versand_testadressen()), "mail_zugang_geprueft": bool(z.get("geprueft_am")),
                    "beispiele": sorted(BEISPIELE)})


@bp.post("/vorgang")
@nur_demo
@anmeldung_pflicht()
def vorgang_anlegen():
    try:
        v = demo.vorgang_erzeugen(g.nutzer["mandant_ref"])
    except demo.DemoFehler as f:
        return jsonify({"error": str(f), "meldung": "Der Demo-Vorgang konnte nicht angelegt werden."}), 409
    audit("demo_vorgang_angelegt", "id=%d" % v["vorgang_id"])
    v["qr_svg"] = qr.svg(v["link"])
    return jsonify(v)


@bp.get("/beispiel/<name>")
@nur_demo
@anmeldung_pflicht()
def beispiel(name):
    eintrag = BEISPIELE.get(name)
    if eintrag is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    datei, erzeuger = eintrag
    return Response(erzeuger(), mimetype="application/pdf", headers={"Content-Disposition": 'attachment; filename="%s"' % datei})


@bp.post("/testmail")
@nur_demo
@anmeldung_pflicht()
def testmail():
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict) or set(daten) != {"an"} or not isinstance(daten["an"], str):
        return jsonify({"error": "eingabe_ungueltig", "meldung": "Bitte {an: Adresse} senden."}), 400
    an = daten["an"].strip().lower()
    if an not in config.versand_testadressen():
        return jsonify({"error": "adresse_nicht_erlaubt", "meldung": "Diese Adresse steht nicht in der Liste der erlaubten Testadressen."}), 400
    try:
        z = mail_zugang.fuer_dienst(g.nutzer["mandant_ref"], "demo_testmail", g.nutzer["email"])
    except schluessel.HauptschluesselFehlt:
        return jsonify({"error": "nicht_bereit", "meldung": "Der Dienst ist nicht vollständig eingerichtet."}), 503
    except schluessel.Entschluesselung:
        return jsonify({"error": "mail_zugang_nicht_lesbar", "meldung": "Der Mail-Zugang ist nicht lesbar. Bitte neu eintragen."}), 503
    if z is None:
        return jsonify({"error": "mail_zugang_fehlt", "meldung": "Bitte zuerst den Mail-Zugang eintragen."}), 409
    nachricht = mailer.nachricht_bauen(z["absender_adresse"], z["absender_name"], an, "SignNext Demo: Testmail",
                                       "Guten Tag,\n\ndas ist eine Testmail der SignNext-Demo (erfundene Daten). Sie müssen nichts weiter tun.\n")
    info: dict = {}
    try:
        mailer.senden(z, [an], nachricht, info)
    except mailer.MailFehler as f:
        audit("demo_testmail", "ok=False fehler=%s" % f.code)
        return jsonify({"ok": False, "error": "mail_" + f.code, "meldung": "Die Testmail konnte nicht gesendet werden (%s)." % f.code}), 502
    finally:
        z["passwort"] = None
    audit("demo_testmail", "ok=True")
    return jsonify({"ok": True, "ergebnis": "gesendet"})
