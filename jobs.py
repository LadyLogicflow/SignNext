"""Hintergrund-Jobs: Erinnerungen, Abschluss-Nachholung, Hinweise an die Beratungsstelle, Vorwarnungen, Löschungen, Aufräumen.

``lauf(jetzt)`` ist ein Durchlauf (wiederholbar, jeder Schritt für sich abgesichert: ein Fehler stoppt die anderen Schritte nicht). ``starten()`` startet einen Thread, der
jede Minute einen Durchlauf macht; bei mehreren Prozessen arbeitet nur der, der die Sperrdatei hält (flock, fällt mit dem Prozess weg). Abschalten: SIGNNEXT_JOBS=0.
Es gibt keine Mail ohne Schalter: Bei SIGNNEXT_VERSAND=aus wird nichts gesendet und nichts als erledigt markiert."""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import abschluss
import berlinzeit
import config
import db
import download
import loeschen
import mailtexte
import mailversand
import schluessel

LOG = logging.getLogger("signnext")
_STUFE_LETZTE = len(config.ERINNERUNG_STUNDEN)


def _z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _schritt(name: str, zaehler: dict, f, *a) -> None:
    try:
        zaehler[name] = f(*a)
    except Exception as e:                                       # ein Schritt darf die anderen nicht stoppen; nie Inhalte loggen
        LOG.error("Job-Schritt fehlgeschlagen: %s (%s)", name, type(e).__name__)
        zaehler[name] = -1


# --- 1. Prozessabbruch während Versand / Abschluss ------------------------------------------------------------------------------------------------------------------------------

def recovery(jetzt: datetime) -> int:
    grenze = _z(jetzt - timedelta(minutes=config.STALE_MINUTEN))
    n = 0
    with db.verbindung() as c:
        for v in c.execute("SELECT id, mandant_ref FROM vorgang WHERE status='versenden' AND (versenden_seit IS NULL OR versenden_seit < ?)", (grenze,)).fetchall():
            if c.execute("UPDATE vorgang SET status='versand_unklar', versand_fehler='unklar' WHERE id=? AND status='versenden'", (v["id"],)).rowcount:
                c.execute("INSERT INTO versand(vorgang_id, mandant_ref, zeit, ergebnis, code, message_id) VALUES (?,?,?,?,?,?)", (v["id"], v["mandant_ref"], _z(jetzt), "unklar", "abbruch", ""))
                db.audit_in(c, v["mandant_ref"], "system", "versand_abbruch", "vorgang=%d" % v["id"])
                n += 1
        n += c.execute("UPDATE vorgang SET status='abschluss_fehlgeschlagen' WHERE status='abschluss_laeuft' AND (abschluss_seit IS NULL OR abschluss_seit < ?)", (grenze,)).rowcount
    return n


# --- 2. Abschluss nachholen ------------------------------------------------------------------------------------------------------------------------------------------------------

def abschluesse(jetzt: datetime) -> int:
    n = 0
    with db.verbindung() as c:
        kandidaten = c.execute("SELECT id, mandant_ref FROM vorgang WHERE (status='unterschrieben' AND unterschrieben_am < ?) OR (status='abschluss_fehlgeschlagen' AND abschluss_versuche < ?)",
                               (_z(jetzt - timedelta(seconds=30)), config.ABSCHLUSS_VERSUCHE_MAX)).fetchall()
    for v in kandidaten:
        try:
            if abschluss.ausfuehren(v["mandant_ref"], v["id"]):
                n += 1
        except Exception:
            pass                                                     # ausfuehren hat den Fehlzustand gesetzt und protokolliert
    return n


# --- 3. Wiederherstellbares Token: sofort weg, wenn es nicht mehr gebraucht wird ----------------------------------------------------------------------------------------

def token_entzug(jetzt: datetime) -> int:
    with db.verbindung() as c:
        return c.execute("UPDATE link SET token_enc=NULL WHERE token_enc IS NOT NULL AND (gesperrt=1 OR verbraucht_am IS NOT NULL OR laeuft_ab <= ? OR "
                         "vorgang_id IN (SELECT id FROM vorgang WHERE status NOT IN ('versendet','versand_unklar')))", (_z(jetzt),)).rowcount


# --- 4. Erinnerungen an das Mitglied -------------------------------------------------------------------------------------------------------------------------------------------

def erinnerungen(jetzt: datetime) -> int:
    """Höchstens eine Erinnerung je Vorgang und Durchlauf. Mehrere fällige: die neueste wird gesendet, die älteren gelten als übersprungen."""
    if config.versand_modus() == "aus" or config.basis_url() is None or config.hauptschluessel() is None:
        return 0
    gesendet = 0
    with db.verbindung() as c:
        vorgaenge = c.execute("SELECT DISTINCT e.vorgang_id AS id, e.mandant_ref AS mandant_ref FROM erinnerung e JOIN vorgang v ON v.id=e.vorgang_id "
                              "WHERE e.ergebnis IS NULL AND e.faellig_am <= ? AND v.status='versendet' AND v.erinnerungen_aus=0", (_z(jetzt),)).fetchall()
    for v in vorgaenge:
        gesendet += _erinnerung_fuer(v["mandant_ref"], v["id"], jetzt)
    return gesendet


def _erinnerung_fuer(mandant_ref: str, vorgang_id: int, jetzt: datetime) -> int:
    with db.verbindung() as c:
        faellig = c.execute("SELECT * FROM erinnerung WHERE vorgang_id=? AND mandant_ref=? AND ergebnis IS NULL AND faellig_am <= ? ORDER BY nr", (vorgang_id, mandant_ref, _z(jetzt))).fetchall()
        link = c.execute("SELECT * FROM link WHERE vorgang_id=? AND mandant_ref=? ORDER BY id DESC LIMIT 1", (vorgang_id, mandant_ref)).fetchone()
        if not faellig:
            return 0
        if link is None or link["gesperrt"] or link["verbraucht_am"] or link["laeuft_ab"] <= _z(jetzt) or not link["token_enc"]:
            return 0
        e = faellig[-1]
        for aeltere in faellig[:-1]:
            c.execute("UPDATE erinnerung SET ergebnis='uebersprungen' WHERE id=? AND ergebnis IS NULL", (aeltere["id"],))
        # Anspruch: höchstens 3 Versuche, mindestens 5 Minuten Abstand, nur ein Prozess gewinnt
        if c.execute("UPDATE erinnerung SET versuche=versuche+1, letzter_versuch=? WHERE id=? AND ergebnis IS NULL AND versuche=? AND versuche<? AND (letzter_versuch IS NULL OR letzter_versuch<=?)",
                     (_z(jetzt), e["id"], e["versuche"], config.ERINNERUNG_VERSUCHE_MAX, _z(jetzt - timedelta(minutes=config.ERINNERUNG_ABSTAND_MINUTEN)))).rowcount != 1:
            return 0
    v = db.vorgang_intern(mandant_ref, vorgang_id)
    adresse = mailversand.mitglied_adresse(mandant_ref, v) if v else None
    try:
        token = schluessel.entschluesseln(mandant_ref, "link-token", link["token_enc"])
    except schluessel.Entschluesselung:
        token = None
    if not adresse or not token:
        _erinnerung_ergebnis(e["id"], "fehlgeschlagen")
        return 0
    ablauf = abschluss.berlin(link["laeuft_ab"])
    vorlage = mailtexte.ERINNERUNG_LETZTE_TEXT if e["nr"] == _STUFE_LETZTE else mailtexte.ERINNERUNG_TEXT
    stunden = max(1, round((_dt(link["laeuft_ab"]) - jetzt).total_seconds() / 3600))
    text = vorlage.format(link="%s/u#%s" % (config.basis_url(), token), ablauf=ablauf, stunden=stunden)
    ergebnis = mailversand.senden(mandant_ref, vorgang_id, "erinnerung", mailtexte.ERINNERUNG_BETREFF, text, an=adresse)
    token = None
    if ergebnis == "gesendet":
        _erinnerung_ergebnis(e["id"], "gesendet", jetzt)
        return 1
    if ergebnis == "unklar":
        _erinnerung_ergebnis(e["id"], "unklar")                  # nie wiederholen: lieber eine Erinnerung zu wenig als doppelt
    elif ergebnis == "fehlgeschlagen":
        if e["versuche"] + 1 >= config.ERINNERUNG_VERSUCHE_MAX:
            _erinnerung_ergebnis(e["id"], "fehlgeschlagen")
    else:                                                        # uebersprungen / zurueckgestellt: nichts verbraucht
        with db.verbindung() as c:
            c.execute("UPDATE erinnerung SET versuche=versuche-1, letzter_versuch=NULL WHERE id=? AND ergebnis IS NULL AND versuche>0", (e["id"],))
    return 0


def _erinnerung_ergebnis(erinnerung_id: int, ergebnis: str, gesendet_am: datetime | None = None) -> None:
    with db.verbindung() as c:
        c.execute("UPDATE erinnerung SET ergebnis=?, gesendet_am=? WHERE id=?", (ergebnis, _z(gesendet_am) if gesendet_am else None, erinnerung_id))


# --- 5. Hinweise an die Beratungsstelle ---------------------------------------------------------------------------------------------------------------------------------------

def _hinweis(mandant_ref: str, vorgang_id: int, art: str, **kw) -> str:
    return mailversand.senden(mandant_ref, vorgang_id, "info", mailtexte.stelle_betreff(vorgang_id), mailtexte.stelle_text(art, vorgang_id, **kw))


def _erledigt(ergebnis: str) -> bool:
    """Ein Hinweis gilt als erledigt, wenn er gesendet wurde oder endgültig nicht (mehr) zustellbar ist. Schalter aus / Limit: später erneut."""
    return ergebnis in ("gesendet", "unklar", "fehlgeschlagen")


def stellen_hinweise(jetzt: datetime) -> int:
    n = 0
    with db.verbindung() as c:
        gesperrt = c.execute("SELECT v.id AS id, v.mandant_ref AS mandant_ref, l.id AS link_id FROM vorgang v JOIN link l ON l.id=(SELECT MAX(id) FROM link WHERE vorgang_id=v.id) "
                             "WHERE v.status IN ('versendet','versand_unklar') AND l.gesperrt=1 AND l.verbraucht_am IS NULL AND l.fehlversuche>=5 AND COALESCE(v.link_sperre_gemeldet,0)<>l.id").fetchall()
        fertig = c.execute("SELECT id, mandant_ref, aufbewahrung_bis FROM vorgang WHERE status='abgeschlossen' AND bst_info_am IS NULL").fetchall()
        abrufe = c.execute("SELECT id, vorgang_id, mandant_ref, zaehler FROM download_log WHERE bst_benachrichtigt_am IS NULL ORDER BY id").fetchall()
    for v in gesperrt:
        if _erledigt(_hinweis(v["mandant_ref"], v["id"], "link_gesperrt")):
            db.vorgang_aendern(v["mandant_ref"], v["id"], link_sperre_gemeldet=v["link_id"])
            n += 1
    for v in fertig:
        if _erledigt(_hinweis(v["mandant_ref"], v["id"], "unterschrieben", bis=abschluss.berlin(v["aufbewahrung_bis"])[:10])):
            db.vorgang_aendern(v["mandant_ref"], v["id"], bst_info_am=_z(jetzt))
            n += 1
    for a in abrufe:
        if _erledigt(_hinweis(a["mandant_ref"], a["vorgang_id"], "abruf", zaehler=a["zaehler"], max=config.DOWNLOAD_MAX_ABRUFE)):
            with db.verbindung() as c:
                c.execute("UPDATE download_log SET bst_benachrichtigt_am=? WHERE id=?", (_z(jetzt), a["id"]))
            n += 1
    return n


# --- 6. Bestätigung an das Mitglied -------------------------------------------------------------------------------------------------------------------------------------------

def mitglied_bestaetigung(jetzt: datetime) -> int:
    n = 0
    if config.versand_modus() == "aus" or config.basis_url() is None:
        return 0
    with db.verbindung() as c:
        offen = c.execute("SELECT id, mandant_ref FROM vorgang WHERE status='abgeschlossen' AND mitglied_mail_am IS NULL AND mitglied_mail_versuche < ?", (config.MITGLIED_MAIL_VERSUCHE_MAX,)).fetchall()
    for o in offen:
        v = db.vorgang_intern(o["mandant_ref"], o["id"])
        adresse = mailversand.mitglied_adresse(o["mandant_ref"], v)
        if not adresse:
            db.vorgang_aendern(o["mandant_ref"], o["id"], mitglied_mail_am=_z(jetzt))
            continue
        einstellung = db.stelle_einstellung_lesen(o["mandant_ref"]) or {}
        mit_link = bool(einstellung.get("mitglied_download_link", 1))
        if mit_link:
            with db.verbindung() as c:
                token = download.link_erzeugen(c, o["mandant_ref"], o["id"])
            text = mailtexte.FERTIG_TEXT_MIT_LINK.format(link="%s/d#%s" % (config.basis_url(), token), max=config.DOWNLOAD_MAX_ABRUFE)
        else:
            text = mailtexte.FERTIG_TEXT_OHNE_LINK
        ergebnis = mailversand.senden(o["mandant_ref"], o["id"], "info", mailtexte.FERTIG_BETREFF, text, an=adresse)
        token = None
        if ergebnis in ("gesendet", "unklar"):
            db.vorgang_aendern(o["mandant_ref"], o["id"], mitglied_mail_am=_z(jetzt))
            n += ergebnis == "gesendet"
        elif ergebnis == "fehlgeschlagen":
            with db.verbindung() as c:
                c.execute("UPDATE vorgang SET mitglied_mail_versuche=mitglied_mail_versuche+1 WHERE id=? AND mandant_ref=?", (o["id"], o["mandant_ref"]))
    return n


# --- 7./8. Vorwarnungen und Löschungen -----------------------------------------------------------------------------------------------------------------------------------------

def fristen_nicht_unterschrieben(jetzt: datetime) -> int:
    """Link-Ablauf (Tag 7) ohne Unterschrift: Vorgang löschen, 24 Stunden vorher Vorwarnung. Gleiches für hochgeladene, nie versendete Vorgänge (7 Tage nach dem Anlegen)."""
    n = 0
    vorwarnung = timedelta(hours=config.VORWARNUNG_STUNDEN)
    with db.verbindung() as c:
        versendet = c.execute("SELECT v.id AS id, v.mandant_ref AS mandant_ref, v.vorwarnung_am AS vorwarnung_am, "
                              "(SELECT MAX(laeuft_ab) FROM link WHERE vorgang_id=v.id) AS ablauf, v.versendet_am AS versendet_am FROM vorgang v WHERE v.status IN ('versendet','versand_unklar')").fetchall()
        nie = c.execute("SELECT id, mandant_ref, vorwarnung_am, erstellt FROM vorgang WHERE status IN ('neu','bereit','bestaetigung_noetig')").fetchall()
    kandidaten = []
    for v in versendet:
        ablauf = v["ablauf"] or (_z(_dt(v["versendet_am"]) + timedelta(days=config.LINK_TAGE)) if v["versendet_am"] else None)
        kandidaten.append((v, ablauf, "nicht_unterschrieben", "vorwarnung_loeschung", "geloescht_nicht_unterschrieben"))
    for v in nie:
        kandidaten.append((v, _z(_dt(v["erstellt"]) + timedelta(days=config.NICHT_VERSENDET_TAGE)), "nicht_versendet", "vorwarnung_nicht_versendet", "geloescht_nicht_versendet"))
    for v, ablauf, grund, art_vor, art_weg in kandidaten:
        if not ablauf:
            continue
        ende = _dt(ablauf)
        if jetzt >= ende:
            if loeschen.vorgang_loeschen(v["mandant_ref"], v["id"], grund) is not None:
                _hinweis(v["mandant_ref"], v["id"], art_weg)
                n += 1
        elif jetzt >= ende - vorwarnung and not v["vorwarnung_am"]:
            if _erledigt(_hinweis(v["mandant_ref"], v["id"], art_vor, bis=abschluss.berlin(_z(ende)))):
                db.vorgang_aendern(v["mandant_ref"], v["id"], vorwarnung_am=_z(jetzt))
    return n


def fristen_aufbewahrung(jetzt: datetime) -> int:
    """Unterschriebene Dokumente nach der Aufbewahrungsfrist der Beratungsstelle (30–365 Tage, je Vorgang festgehalten) löschen, 14 Tage vorher warnen."""
    n = 0
    with db.verbindung() as c:
        zeilen = c.execute("SELECT id, mandant_ref, aufbewahrung_bis, vorwarnung_am FROM vorgang WHERE status='abgeschlossen' AND aufbewahrung_bis IS NOT NULL").fetchall()
    for v in zeilen:
        bis = _dt(v["aufbewahrung_bis"])
        if jetzt >= bis:
            if loeschen.vorgang_loeschen(v["mandant_ref"], v["id"], "aufbewahrung") is not None:
                _hinweis(v["mandant_ref"], v["id"], "geloescht_aufbewahrung")
                n += 1
        elif jetzt >= bis - timedelta(days=config.VORWARNUNG_AUFBEWAHRUNG_TAGE) and not v["vorwarnung_am"]:
            if _erledigt(_hinweis(v["mandant_ref"], v["id"], "vorwarnung_aufbewahrung", bis=abschluss.berlin(v["aufbewahrung_bis"])[:10])):
                db.vorgang_aendern(v["mandant_ref"], v["id"], vorwarnung_am=_z(jetzt))
    return n


# --- 9. Aufräumen -------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def zaehleintraege_loeschen(jetzt: datetime) -> int:
    """Zähleinträge (Vorgangszeilen ohne Inhalt) werden 3 Jahre nach Ende des Kalenderjahres des Versands gelöscht (rollierend, unabhängig vom Vertragsende).
    Nur Einträge im Zustand 'geloescht' (der Inhalt ist schon weg). Löschprotokoll: nur Zahlen."""
    jahr_jetzt = int(berlinzeit.monat(_z(jetzt))[:4])
    grenze = jahr_jetzt - db.ZAEHLEINTRAG_JAHRE - 1                  # Versandjahr <= grenze: Ende dieses Jahres + 3 Jahre ist erreicht
    with db.verbindung() as c:
        je_jahr = c.execute("SELECT substr(zaehlmonat, 1, 4) AS jahr, COUNT(*) AS n FROM vorgang WHERE status='geloescht' AND zaehlmonat IS NOT NULL AND CAST(substr(zaehlmonat, 1, 4) AS INTEGER) <= ? "
                            "GROUP BY 1", (grenze,)).fetchall()
        n = 0
        for z in je_jahr:
            c.execute("DELETE FROM vorgang WHERE status='geloescht' AND zaehlmonat IS NOT NULL AND substr(zaehlmonat, 1, 4)=?", (z["jahr"],))
            db.loeschprotokoll_schreiben(c, "zaehleintraege", "-", None, {"eintraege": z["n"], "jahr": int(z["jahr"])}, jetzt)
            n += z["n"]
    return n


def aufraeumen(jetzt: datetime) -> int:
    n = 0
    alt = _z(jetzt - timedelta(hours=24))
    with db.verbindung() as c:
        n += c.execute("DELETE FROM zugang_ip WHERE zeit < ?", (alt,)).rowcount
        n += c.execute("DELETE FROM zugang_unbekannt WHERE zeit < ?", (alt,)).rowcount
        n += c.execute("DELETE FROM sitzung WHERE laeuft_ab < ?", (_z(jetzt),)).rowcount
        n += c.execute("DELETE FROM handoff WHERE laeuft_ab <= ?", (_z(jetzt),)).rowcount                   # Handy-Übergabe: Bild nie länger als 10 Minuten
        c.execute("DELETE FROM handoff_log WHERE zeit < ?", (_z(jetzt - timedelta(hours=2)),))
        n += c.execute("DELETE FROM loeschprotokoll WHERE loeschen_nach <= ?", (_z(jetzt),)).rowcount
        reste = c.execute("SELECT DISTINCT v.id AS id, v.mandant_ref AS mandant_ref FROM vorgang v JOIN dokument d ON d.vorgang_id=v.id WHERE v.status='abgeschlossen' AND d.rolle IN ('A','B','M')").fetchall()
    for r in reste:                                              # Absturz zwischen Abschluss und Löschung der Originale
        n += abschluss.originale_loeschen(r["mandant_ref"], r["id"])
    return n


def lauf(jetzt: datetime | None = None) -> dict:
    jetzt = jetzt or datetime.now(timezone.utc)
    z: dict = {}
    for name, f in (("recovery", recovery), ("abschluesse", abschluesse), ("token_entzug", token_entzug), ("erinnerungen", erinnerungen), ("stellen_hinweise", stellen_hinweise),
                    ("mitglied_bestaetigung", mitglied_bestaetigung), ("fristen_nicht_unterschrieben", fristen_nicht_unterschrieben), ("fristen_aufbewahrung", fristen_aufbewahrung),
                    ("zaehleintraege", zaehleintraege_loeschen), ("aufraeumen", aufraeumen)):
        _schritt(name, z, f, jetzt)
    return z


# --- Thread ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

_gestartet = False
_lock_datei = None


def _sperre_halten() -> bool:
    """Nur ein Prozess führt die Jobs aus. Die Sperre hält, solange der Prozess lebt."""
    global _lock_datei
    if _lock_datei is not None:
        return True
    import fcntl
    try:
        f = open(config.daten_verzeichnis() / "jobs.lock", "a")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _lock_datei = f
    return True


def _schleife() -> None:
    while True:
        try:
            if _sperre_halten():
                lauf()
        except Exception as e:
            LOG.error("Job-Durchlauf fehlgeschlagen (%s)", type(e).__name__)
        time.sleep(config.JOB_INTERVALL_SEKUNDEN)


def starten() -> bool:
    """Startet den Job-Thread (einmal je Prozess). Mit SIGNNEXT_JOBS=0 abgeschaltet."""
    global _gestartet
    if _gestartet or os.environ.get("SIGNNEXT_JOBS", "1") == "0":
        return False
    _gestartet = True
    threading.Thread(target=_schleife, name="signnext-jobs", daemon=True).start()
    return True
