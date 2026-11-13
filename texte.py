"""Texte der Unterschriftsseite (Datenschutzinformation, Unterschriftssatz, Haken, Ausstieg, Hinweis zur Eingabe des Geburtsdatums).

Die Texte liegen als Dateien in ``texte/`` und werden nur eingesetzt, nicht im Code formuliert. Variablen ({verantwortlicher}, {ip_behandlung}, {aufbewahrung_tage},
{datenschutz_link}) kommen aus der tatsächlichen Einstellung. Die Seite liefert die Texte vollständig aus (nicht nur einen Link), mit Kennung und SHA-256 des
fertig gefüllten Textes; beides steht im Nachweis jeder Unterschrift. Solange ``texte/KENNUNG`` mit ``entwurf`` beginnt, sind die Texte nicht freigegeben."""
import hashlib
import json
from pathlib import Path

import config
import db

ORDNER = Path(__file__).resolve().parent / "texte"
NAMEN = ("datenschutz", "unterschriftssatz", "haken", "ausstieg", "zugangshinweis")


class TexteFehlen(Exception):
    """Die Beratungsstelle hat die Angaben für die Datenschutzinformation noch nicht eingetragen."""


def kennung() -> str:
    return (ORDNER / "KENNUNG").read_text(encoding="utf-8").strip()


def freigegeben() -> bool:
    return not kennung().startswith("entwurf")


def _roh(name: str) -> str:
    return (ORDNER / (name + ".txt")).read_text(encoding="utf-8").strip()


def werte_fuer(mandant_ref: str) -> dict:
    """Variablen aus der tatsächlichen Einstellung. Wirft TexteFehlen, wenn die Beratungsstelle ihre Angaben noch nicht eingetragen hat."""
    e = db.stelle_einstellung_lesen(mandant_ref)
    if not e:
        raise TexteFehlen()
    return {"verantwortlicher": e["verantwortlicher"], "datenschutz_link": e["datenschutz_link"],
            "ip_behandlung": "gekürzt" if config.ip_speicherung() == "gekuerzt" else "ungekürzt", "aufbewahrung_tage": str(config.aufbewahrung_tage())}


def laden(mandant_ref: str) -> dict:
    """{'kennung', 'freigegeben', 'hash', 'texte': {...}} mit gefüllten Variablen. Der Hash gilt für genau diese Texte."""
    w = werte_fuer(mandant_ref)
    texte = {n: _roh(n).format(**w) for n in NAMEN}
    kanonisch = json.dumps({"kennung": kennung(), "texte": texte}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"kennung": kennung(), "freigegeben": freigegeben(), "hash": hashlib.sha256(kanonisch.encode("utf-8")).hexdigest(), "texte": texte}


def zugangshinweis() -> str:
    """Hinweis vor der Eingabe des Geburtsdatums (enthält keine Variablen)."""
    return _roh("zugangshinweis")
