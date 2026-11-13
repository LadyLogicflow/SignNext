"""Liefert die Oberfläche (Ordner ``ui/``) aus: nur GET/HEAD, feste Seitenliste, kein Dateizugriff nach Benutzereingabe.

Seiten (alle ohne Anmeldung ausgeliefert; die Daten kommen erst über die API und brauchen dort Cookie bzw. Zugangslink):
  /u              Unterschriftsseite des Mitglieds (Link aus der Mail: /u#<Token>)
  /d              Download-Seite des Mitglieds (Link aus der Mail: /d#<Token>)
  /pruefung       Prüfansicht der Beratungsstelle (?id=<Vorgang>)
  /einstellungen  Einstellungen der Beratungsstelle
  /vorgaenge      Vorgangsliste der Beratungsstelle ( / leitet hierher)
Dazu die Dateien ``*.js`` und ``style.css`` aus ``ui/`` unter ``/<Dateiname>`` (die Seiten binden sie relativ ein).

Sicherheitsheader auf jeder Seite: strenge Content-Security-Policy (Skripte nur von der eigenen Adresse, keine Inline-Skripte, keine fremden Quellen, keine Einbettung
durch andere Seiten), nicht für Suchmaschinen, nie zwischenspeichern (zusätzlich zu den globalen Headern in app.py)."""
import re
from pathlib import Path

from flask import Blueprint, Response, abort, redirect, send_from_directory

bp = Blueprint("ui", __name__)
ORDNER = Path(__file__).resolve().parent / "ui"
SEITEN = {"/u": "unterschrift.html", "/d": "download.html", "/pruefung": "pruefung.html", "/einstellungen": "einstellungen.html", "/vorgaenge": "vorgaenge.html"}
ASSET = re.compile(r"^[a-z][a-z0-9_-]{0,40}\.(js|css)$")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-src 'self'; "
       "form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'")


def _kopf(antwort: Response, seite: bool) -> Response:
    antwort.headers["Cache-Control"] = "no-store"
    antwort.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    antwort.headers["Referrer-Policy"] = "no-referrer"
    if seite:
        antwort.headers["Content-Security-Policy"] = CSP
        antwort.headers["X-Frame-Options"] = "DENY"
        antwort.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    return antwort


def _seite(datei: str) -> Response:
    pfad = ORDNER / datei
    if not pfad.is_file():
        abort(404)
    return _kopf(send_from_directory(ORDNER, datei, mimetype="text/html", max_age=0), True)


def _asset_erlaubt(name: str) -> bool:
    return bool(ASSET.fullmatch(name)) and (ORDNER / name).is_file()


for _pfad, _datei in SEITEN.items():
    bp.add_url_rule(_pfad, endpoint="seite_" + _datei.split(".")[0], view_func=(lambda d=_datei: _seite(d)), methods=["GET"])


@bp.get("/")
def start():
    return redirect("/vorgaenge", 302)


@bp.get("/<name>")
def asset(name):
    if not _asset_erlaubt(name):
        abort(404)
    mime = "text/javascript" if name.endswith(".js") else "text/css"
    return _kopf(send_from_directory(ORDNER, name, mimetype=mime, max_age=0), False)
