"""Prüfansicht: was die Beratungsstelle vor dem Versand sieht, und die Angaben, die sie selbst ergänzt.

Name, Geburtsdatum und Mailadresse aus den PDFs werden bei JEDEM Aufruf frisch aus den abgelegten Dateien gelesen und nie gespeichert. Gespeichert werden nur
Angaben, die die Beratungsstelle selbst macht oder ändert: die Mailadresse verschlüsselt, das Geburtsdatum nur als HMAC des Codes ttmmjj (nie Klartext),
die Personenzahl als Zahl; je Angabe die Herkunft (pdf | manuell | abweichend)."""
import db
import mailer
import pdf_eingang
import schluessel


class Fehler(Exception):
    def __init__(self, code: str, status: int = 422, feld: str | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.feld = feld


def pdf_werte(mandant_ref: str, vorgang_id: int) -> dict:
    """{'A': Ergebnis|None, 'B': Ergebnis|None} aus den abgelegten Originalen (nicht aus dem zusammengeführten PDF)."""
    ergebnis = {}
    for rolle in ("A", "B"):
        daten = db.dokument_bytes(mandant_ref, vorgang_id, rolle)
        ergebnis[rolle] = pdf_eingang.auswerten(daten) if daten is not None else None
    return ergebnis


def _pdf_felder(pdf: dict) -> dict:
    a, b = pdf["A"], pdf["B"]
    wa = a["werte"] if a else {}
    wb = b["werte"] if b else {}
    quelle = wa if (wa.get("name") or wa.get("vorname")) else wb
    name = " ".join(x for x in (quelle.get("vorname"), quelle.get("name")) if x) or None
    return {"name": name, "datum": wa.get("geburtsdatum"), "mail": wb.get("mail"), "personen": b["personen"] if b else None}


def _herkunft(pdf_wert, manuell_gesetzt: bool, gespeicherte_herkunft: str | None) -> str:
    if manuell_gesetzt:
        return gespeicherte_herkunft or "manuell"
    return "pdf" if pdf_wert is not None else "fehlt"


def effektiv(mandant_ref: str, v: dict, pdf: dict) -> dict:
    """Die Angaben, mit denen versendet würde. ``v`` = vorgang_intern. Enthält Klartext-Werte: nie speichern, nie protokollieren."""
    f = _pdf_felder(pdf)
    mail = None
    if v.get("email_enc"):
        mail = schluessel.entschluesseln(mandant_ref, "mitglied-mail", v["email_enc"])
    elif f["mail"]:
        mail = f["mail"]
    code_hmac = code_kid = None
    datum = None
    if v.get("code_hmac"):
        code_hmac, code_kid = v["code_hmac"], v["code_kid"]
    elif f["datum"]:
        code = schluessel.code_aus_datum(f["datum"])
        if code:
            code_hmac, code_kid = schluessel.code_hmac(mandant_ref, code)
            datum = f["datum"]
    personen = v.get("personen")
    return {
        "name": f["name"], "email": mail, "email_herkunft": _herkunft(f["mail"], bool(v.get("email_enc")), v.get("email_herkunft")),
        "datum_pdf": f["datum"], "datum_herkunft": _herkunft(datum if datum else None, bool(v.get("code_hmac")), v.get("datum_herkunft")),
        "datum_gesetzt": code_hmac is not None, "code_hmac": code_hmac, "code_kid": code_kid,
        "personen": personen, "personen_herkunft": (v.get("personen_herkunft") or "pdf") if personen else "fehlt",
        "pdf_mail": f["mail"], "pdf_personen": f["personen"],
    }


def fehlend(e: dict) -> list:
    out = []
    if not e["email"]:
        out.append("email")
    if not e["datum_gesetzt"]:
        out.append("geburtsdatum")
    if e["personen"] not in (1, 2):
        out.append("personen")
    return out


def ansicht(mandant_ref: str, vorgang_id: int) -> dict:
    """Antwort der Prüfansicht (enthält Personendaten für die berechtigte Beratungsstelle; wird nicht gespeichert)."""
    v = db.vorgang_intern(mandant_ref, vorgang_id)
    if v is None:
        raise Fehler("nicht_gefunden", 404)
    e = effektiv(mandant_ref, v, pdf_werte(mandant_ref, vorgang_id))
    abgleich = db.vorgang_lesen(mandant_ref, vorgang_id)["abgleich"]
    return {
        "vorgang_id": v["id"], "status": v["status"], "abgleich": abgleich,
        "name": {"wert": e["name"], "herkunft": "pdf" if e["name"] else "fehlt"},
        # Ein manuell eingegebenes Datum ist nur als HMAC gespeichert und kann nicht angezeigt werden (gesetzt = true).
        "geburtsdatum": {"wert": e["datum_pdf"] if e["datum_herkunft"] == "pdf" else None, "herkunft": e["datum_herkunft"], "gesetzt": e["datum_gesetzt"],
                        "pdf_wert": e["datum_pdf"]},
        "email": {"wert": e["email"], "herkunft": e["email_herkunft"], "pdf_wert": e["pdf_mail"]},
        "personen": {"wert": e["personen"], "herkunft": e["personen_herkunft"], "pdf_wert": e["pdf_personen"]},
        "fehlend": fehlend(e), "versandbereit_pdf": v["status"] == "bereit" and not fehlend(e), "erinnerungen_aus": bool(v.get("erinnerungen_aus")),
    }


def angaben_setzen(mandant_ref: str, vorgang_id: int, eingabe: dict) -> dict:
    """Die Beratungsstelle trägt Mailadresse, Geburtsdatum (TT.MM.JJJJ) und/oder Personenzahl ein. ``null`` nimmt die manuelle Angabe zurück (es gilt wieder das PDF).
    Nur im Zustand 'bereit' oder 'bestaetigung_noetig'. Wirft Fehler."""
    v = db.vorgang_intern(mandant_ref, vorgang_id)
    if v is None:
        raise Fehler("nicht_gefunden", 404)
    if v["status"] not in ("bereit", "bestaetigung_noetig"):
        raise Fehler("zustand_passt_nicht", 409)
    if not isinstance(eingabe, dict) or not eingabe:
        raise Fehler("eingabe_ungueltig", 400)
    if set(eingabe) - {"email", "geburtsdatum", "personen"}:
        raise Fehler("eingabe_ungueltig", 400)
    f = _pdf_felder(pdf_werte(mandant_ref, vorgang_id))
    felder = {}
    if "email" in eingabe:
        wert = eingabe["email"]
        if wert is None:
            felder.update(email_enc=None, email_herkunft=None)
        else:
            mail = wert.strip().lower() if isinstance(wert, str) else ""
            if not mailer.adresse_gueltig(mail):
                raise Fehler("email_ungueltig", 422, "email")
            if f["mail"] == mail:
                felder.update(email_enc=None, email_herkunft=None)
            else:
                felder.update(email_enc=schluessel.verschluesseln(mandant_ref, "mitglied-mail", mail), email_herkunft="manuell" if f["mail"] is None else "abweichend")
    if "geburtsdatum" in eingabe:
        wert = eingabe["geburtsdatum"]
        if wert is None:
            felder.update(code_hmac=None, code_kid=None, datum_herkunft=None)
        else:
            code = schluessel.code_aus_datum(wert if isinstance(wert, str) else "")
            if code is None:
                raise Fehler("geburtsdatum_ungueltig", 422, "geburtsdatum")
            pdf_code = schluessel.code_aus_datum(f["datum"]) if f["datum"] else None
            if pdf_code == code:
                felder.update(code_hmac=None, code_kid=None, datum_herkunft=None)
            else:
                h, kid = schluessel.code_hmac(mandant_ref, code)
                felder.update(code_hmac=h, code_kid=kid, datum_herkunft="manuell" if pdf_code is None else "abweichend")
    if "personen" in eingabe:
        wert = eingabe["personen"]
        if wert is None:
            felder.update(personen=f["personen"], personen_herkunft=None)
        else:
            if wert not in (1, 2) or isinstance(wert, bool):
                raise Fehler("personen_ungueltig", 422, "personen")
            if f["personen"] == wert:
                felder.update(personen=wert, personen_herkunft=None)
            else:
                felder.update(personen=wert, personen_herkunft="manuell" if f["personen"] is None else "abweichend")
    db.vorgang_aendern(mandant_ref, vorgang_id, **felder)
    return {"gesetzt": sorted(eingabe)}
