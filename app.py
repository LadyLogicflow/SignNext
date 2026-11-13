"""SignNext: Unterschriften für Beratungsstellen (BST Next). Baustein 1: Portal-Anschluss."""
import logging
import os

from flask import Flask, Request, jsonify

import api
import config
import db
import zugang


def _health_daten() -> tuple[dict, bool]:
    """Keine personenbezogenen Daten, nur Zustände."""
    daten = {"app": "signnext", "version": config.version()}
    gesund = True
    try:
        with db.verbindung() as c:
            c.execute("SELECT 1").fetchone()
        daten["datenbank"] = "ok"
    except Exception:
        logging.exception("Health: Datenbank nicht erreichbar")
        daten["datenbank"] = "fehler"
        gesund = False
    daten["status"] = "ok" if gesund else "fehler"
    return daten, gesund


KLEIN = 300000
UPLOAD_MAX = 2 * 15 * 1024 * 1024 + 1024 * 1024


class _Anfrage(Request):
    """Nur der Upload (zwei PDFs) darf groß sein, alle anderen Routen nehmen höchstens 300 kB."""

    @property
    def max_content_length(self):
        return UPLOAD_MAX if self.endpoint == "api.vorgang_anlegen" else KLEIN


def create_app(db_pfad: str | None = None) -> Flask:
    app = Flask(__name__)
    app.request_class = _Anfrage
    if db_pfad is None:
        verz = config.daten_verzeichnis()
        verz.mkdir(parents=True, exist_ok=True)
        db_pfad = str(verz / "signnext.db")
    db.init(db_pfad)
    app.register_blueprint(api.bp)
    app.register_blueprint(zugang.bp)

    @app.errorhandler(413)
    def _zu_gross(_):
        return jsonify({"error": "zu_gross"}), 413

    @app.after_request
    def _kopf(antwort):
        antwort.headers["Cache-Control"] = "no-store"
        antwort.headers["X-Content-Type-Options"] = "nosniff"
        antwort.headers["Referrer-Policy"] = "no-referrer"
        return antwort

    @app.get("/health")
    def health():
        daten, gesund = _health_daten()
        return jsonify(daten), (200 if gesund else 503)

    @app.get("/api/version")
    def api_version():
        return jsonify({"app": "signnext", "version": config.version()})

    return app


def _wsgi():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    return create_app()
