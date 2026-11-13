"""Schnittstelle für Beratungsstellen (Portal-Cookie). Jede Route arbeitet nur mit dem Mandanten aus der Sitzung (g.nutzer['mandant_ref'])."""
from flask import Blueprint, g, jsonify

import db
from portal_auth import anmeldung_pflicht, audit

bp = Blueprint("api", __name__, url_prefix="/api/v1")


@bp.get("/me")
@anmeldung_pflicht()
def me():
    return jsonify({"email": g.nutzer["email"], "rolle": g.nutzer["rolle"], "produkt": "signnext"})


@bp.get("/vorgaenge")
@anmeldung_pflicht()
def vorgaenge():
    return jsonify({"vorgaenge": db.vorgaenge_liste(g.nutzer["mandant_ref"])})


@bp.get("/vorgaenge/<int:vorgang_id>")
@anmeldung_pflicht()
def vorgang(vorgang_id):
    v = db.vorgang_lesen(g.nutzer["mandant_ref"], vorgang_id)
    if v is None:
        return jsonify({"error": "nicht_gefunden"}), 404
    audit("vorgang_gelesen", "id=%d" % vorgang_id)
    return jsonify(v)
