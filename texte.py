"""Texte der Unterschriftsseite (Datenschutzinformation, Unterschriftssatz, Haken, Ausstieg, Hinweis zur Eingabe des Geburtsdatums).

Die Texte liegen als Dateien in ``texte/`` und werden nur eingesetzt, nicht im Code formuliert. Variablen ({verantwortlicher}, {ip_behandlung}, {aufbewahrung_tage},
{datenschutz_link}) kommen aus der tatsächlichen Einstellung. Die Seite liefert die Texte vollständig aus (nicht nur einen Link), mit Kennung und SHA-256 des
fertig gefüllten Textes; beides steht im Nachweis jeder Unterschrift. Solange ``texte/FREIGABE`` nicht ``erteilt`` lautet (Freigabe durch catrin), sind die Texte nicht freigegeben ("Freigabe offen"); das steht dann auch in der Kennung und
im Nachweis. Der Wortlaut stammt von Irony und ist durch SHA-256 des Template-Texts (UTF-8, ein Absatz, ohne Umbruch am Ende, Variablen als {name}) festgelegt (siehe Tests)."""
import hashlib
import json
from pathlib import Path

import config
import db

ORDNER = Path(__file__).resolve().parent / "texte"
NAMEN = ("datenschutz", "unterschriftssatz", "haken", "ausstieg", "zugangshinweis")


class TexteFehlen(Exception):
    """Die Beratungsstelle hat die Angaben für die Datenschutzinformation noch nicht eingetragen."""


FREIGABE_OFFEN = "Freigabe offen"


def freigegeben() -> bool:
    return (ORDNER / "FREIGABE").read_text(encoding="utf-8").strip() == "erteilt"


def kennung() -> str:
    basis = (ORDNER / "KENNUNG").read_text(encoding="utf-8").strip()
    return basis if freigegeben() else "%s (%s)" % (basis, FREIGABE_OFFEN)


def _roh(name: str) -> str:
    return (ORDNER / (name + ".txt")).read_text(encoding="utf-8").strip()


def werte_fuer(mandant_ref: str) -> dict:
    """Variablen aus der tatsächlichen Einstellung. Wirft TexteFehlen, wenn die Beratungsstelle ihre Angaben noch nicht eingetragen hat."""
    e = db.stelle_einstellung_lesen(mandant_ref)
    if not e:
        raise TexteFehlen()
    return {"verantwortlicher": e["verantwortlicher"], "datenschutz_link": e["datenschutz_link"],
            "ip_behandlung": "gekürzt gespeichert" if config.ip_speicherung() == "gekuerzt" else "ungekürzt gespeichert", "aufbewahrung_tage": str(e["aufbewahrung_tage"] or config.aufbewahrung_standard())}


def laden(mandant_ref: str) -> dict:
    """{'kennung', 'freigegeben', 'hash', 'texte': {...}} mit gefüllten Variablen. Der Hash gilt für genau diese Texte."""
    w = werte_fuer(mandant_ref)
    # Fehlt der Link zur Datenschutzerklärung der Beratungsstelle, gilt die Fassung ohne Link (nie ein leerer Link).
    link_ok = bool((w["datenschutz_link"] or "").strip())
    quelle = {"datenschutz": "datenschutz_mit_link" if link_ok else "datenschutz_ohne_link"}
    texte = {n: _roh(quelle.get(n, n)).format(**w) for n in NAMEN}
    kanonisch = json.dumps({"kennung": kennung(), "texte": texte}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"kennung": kennung(), "freigegeben": freigegeben(), "hash": hashlib.sha256(kanonisch.encode("utf-8")).hexdigest(), "texte": texte}


def zugangshinweis() -> str:
    """Hinweis vor der Eingabe des Geburtsdatums (enthält keine Variablen)."""
    return _roh("zugangshinweis")
