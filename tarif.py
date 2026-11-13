"""Zählung und Tarife (Baustein 6). SignNext rechnet nichts ab und stellt keine Rechnung; der Preis ist nur ein einstellbarer Wert des Betreibers, der über die Sperre entscheidet.

- Gezählt wird ein Vorgang genau einmal, im selben atomaren Schritt, der den Erstversand beansprucht (``beanspruchen``), im Kalendermonat (deutsche Zeit) des Versands.
  Neuer Link, Erinnerungen, Abschluss und Download zählen nie. Scheitert der erste Versand eindeutig, wird die Zählung zurückgenommen (``freigeben``); bei unklarem Versand bleibt sie.
- Tarif je Beratungsstelle: ohne Eintrag in ``stelle_tarif`` gilt ``plus`` für die Referenzen aus SIGNNEXT_TARIF_PLUS_REFERENZEN (Standard: Pilot 282874), sonst ``basis``.
  Limit und Preis je Tarif stehen in der Tabelle ``tarif`` und werden nur vom Betreiber per manage.py geändert.
- Sperre (nur Erstversand eines NEUEN Vorgangs): Limit erreicht UND (SIGNNEXT_SPERRE=an ODER (auto UND Preis > 0)). Bei Preis 0 wird nur gezählt."""
from datetime import datetime, timezone

import berlinzeit
import config
import db


def _jetzt_text(jetzt: datetime | None) -> str:
    return (jetzt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def zuordnung(c, mandant_ref: str) -> str:
    z = c.execute("SELECT tarif FROM stelle_tarif WHERE mandant_ref=?", (mandant_ref,)).fetchone()
    if z and z["tarif"] in config.TARIF_NAMEN:
        return z["tarif"]
    return "plus" if mandant_ref in config.tarif_plus_referenzen() else "basis"


def _tarif_zeile(c, name: str) -> tuple:
    z = c.execute("SELECT limit_monat, preis_cent FROM tarif WHERE name=?", (name,)).fetchone()
    if z is None:
        limit, preis = config.TARIF_VORGABE[name]
        return limit, preis
    return z["limit_monat"], z["preis_cent"]


def sperre_aktiv(limit, preis_cent: int) -> bool:
    modus = config.sperre_modus()
    if limit is None or modus == "aus":
        return False
    return modus == "an" or preis_cent > 0


def genutzt(c, mandant_ref: str, monat: str) -> int:
    return c.execute("SELECT COUNT(*) FROM vorgang WHERE mandant_ref=? AND zaehlmonat=?", (mandant_ref, monat)).fetchone()[0]


def info(mandant_ref: str, jetzt: datetime | None = None) -> dict:
    """Für die Beratungsstelle (GET /api/v1/tarif). Der Preis steht bewusst nicht darin."""
    monat = berlinzeit.monat(_jetzt_text(jetzt))
    with db.verbindung() as c:
        name = zuordnung(c, mandant_ref)
        limit, preis = _tarif_zeile(c, name)
        n = genutzt(c, mandant_ref, monat)
    aktiv = sperre_aktiv(limit, preis)
    return {"tarif": name, "limit": limit, "genutzt": n, "noch_frei": None if limit is None else max(0, limit - n), "zeitraum": monat,
            "gesperrt": bool(aktiv and limit is not None and n >= limit), "sperre_aktiv": aktiv}


def beanspruchen(mandant_ref: str, vorgang_id: int, ausgang: str, jetzt: datetime | None = None) -> str:
    """Atomar: Zustand ``ausgang`` → 'versenden', beim ersten Versand zusätzlich zählen und das Kontingent prüfen.
    Liefert 'ok' | 'zustand' (ein anderer Aufruf war schneller oder der Zustand passt nicht) | 'kontingent' (nichts wurde verändert)."""
    zeit = _jetzt_text(jetzt)
    with db.verbindung() as c:
        v = c.execute("SELECT status, gezaehlt_am FROM vorgang WHERE id=? AND mandant_ref=?", (int(vorgang_id), mandant_ref)).fetchone()
        if v is None or v["status"] != ausgang:
            return "zustand"
        if v["gezaehlt_am"] is None:
            monat = berlinzeit.monat(zeit)
            limit, preis = _tarif_zeile(c, zuordnung(c, mandant_ref))
            if sperre_aktiv(limit, preis) and genutzt(c, mandant_ref, monat) >= limit:
                return "kontingent"
        monat = berlinzeit.monat(zeit)
        ok = c.execute("UPDATE vorgang SET status='versenden', versenden_seit=?, gezaehlt_am=COALESCE(gezaehlt_am, ?), zaehlmonat=COALESCE(zaehlmonat, ?) "
                       "WHERE id=? AND mandant_ref=? AND status=?", (zeit, zeit, monat, int(vorgang_id), mandant_ref, ausgang)).rowcount == 1
    return "ok" if ok else "zustand"


# --- Betreiber (manage.py): jede Änderung steht im Audit (Akteur 'manage'), nichts davon über die Schnittstelle der Beratungsstellen ---

def liste() -> list:
    with db.verbindung() as c:
        tarife = [dict(z) for z in c.execute("SELECT name, limit_monat, preis_cent FROM tarif ORDER BY name")]
        zugeordnet = [dict(z) for z in c.execute("SELECT mandant_ref, tarif FROM stelle_tarif ORDER BY mandant_ref")]
    return [{"tarife": tarife, "zuordnung": zugeordnet, "plus_referenzen": sorted(config.tarif_plus_referenzen()), "sperre": config.sperre_modus()}]


def preis_setzen(name: str, preis_cent: int, akteur: str = "manage") -> None:
    if name not in config.TARIF_NAMEN or isinstance(preis_cent, bool) or not isinstance(preis_cent, int) or not 0 <= preis_cent <= 1_000_000:
        raise ValueError("preis")
    with db.verbindung() as c:
        c.execute("UPDATE tarif SET preis_cent=? WHERE name=?", (preis_cent, name))
        db.audit_in(c, None, akteur, "tarif_preis", "tarif=%s preis_cent=%d" % (name, preis_cent))


def limit_setzen(name: str, limit, akteur: str = "manage") -> None:
    """``limit`` = ganze Zahl 1..100000 oder None (unbegrenzt)."""
    if name not in config.TARIF_NAMEN or (limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000)):
        raise ValueError("limit")
    with db.verbindung() as c:
        c.execute("UPDATE tarif SET limit_monat=? WHERE name=?", (limit, name))
        db.audit_in(c, None, akteur, "tarif_limit", "tarif=%s limit=%s" % (name, "unbegrenzt" if limit is None else limit))


def zuordnen(mandant_ref: str, name: str, akteur: str = "manage") -> None:
    if name not in config.TARIF_NAMEN or not db.REFERENZ.match(mandant_ref or ""):
        raise ValueError("zuordnung")
    with db.verbindung() as c:
        c.execute("INSERT INTO stelle_tarif(mandant_ref, tarif, geaendert_am, geaendert_von) VALUES (?,?,?,?) "
                  "ON CONFLICT(mandant_ref) DO UPDATE SET tarif=excluded.tarif, geaendert_am=excluded.geaendert_am, geaendert_von=excluded.geaendert_von", (mandant_ref, name, db.jetzt(), akteur))
        db.audit_in(c, mandant_ref, akteur, "tarif_zugeordnet", "tarif=%s" % name)


def zaehlung(mandant_ref: str | None = None, monat: str | None = None) -> list:
    """Zahlen für die manuelle Abrechnung: [{mandant_ref, monat, anzahl, tarif}], nur Zahlen."""
    if monat is not None and not (len(monat) == 7 and monat[4] == "-" and monat[:4].isdigit() and monat[5:].isdigit() and 1 <= int(monat[5:]) <= 12):
        raise ValueError("monat")
    sql = "SELECT mandant_ref, zaehlmonat AS monat, COUNT(*) AS anzahl FROM vorgang WHERE zaehlmonat IS NOT NULL"
    par: list = []
    if mandant_ref:
        sql += " AND mandant_ref=?"
        par.append(mandant_ref)
    if monat:
        sql += " AND zaehlmonat=?"
        par.append(monat)
    sql += " GROUP BY mandant_ref, zaehlmonat ORDER BY zaehlmonat, mandant_ref"
    with db.verbindung() as c:
        zeilen = [dict(z) for z in c.execute(sql, par)]
        for z in zeilen:
            z["tarif"] = zuordnung(c, z["mandant_ref"])
    return zeilen
