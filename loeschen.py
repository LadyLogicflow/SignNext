"""Löschen eines einzelnen Vorgangs (nicht unterschrieben nach Fristablauf, Aufbewahrungsfrist, Storno).

Entfernt Dateien und alle Zeilen mit Personenbezug (Unterschriften, Sitzungen, Zugriffsprotokolle, Download-Links und -Protokoll, Erinnerungen, Dokumente, Links samt Token).
Die Vorgangszeile bleibt als reiner Zähleintrag (Baustein 6: Zählung/Tarif), alle Felder mit Personenbezug werden geleert. Das Löschprotokoll enthält nur Zahlen.
Versandzeilen und Audit-Einträge enthalten keine Namen, Adressen oder Inhalte und bleiben bis zum Vertragsende."""
import shutil

import db

GRUENDE = ("nicht_unterschrieben", "nicht_versendet", "aufbewahrung", "storno")


def vorgang_loeschen(mandant_ref: str, vorgang_id: int, grund: str) -> dict | None:
    """Liefert die Zahlen (auch im Löschprotokoll) oder None, wenn es den Vorgang nicht (mehr) gibt oder er schon gelöscht ist. Wiederholbar."""
    if grund not in GRUENDE:
        raise ValueError("grund")
    vid = int(vorgang_id)
    verz = db.dokumente_verzeichnis(mandant_ref) / str(vid)
    dateien = sum(1 for p in verz.rglob("*") if p.is_file()) if verz.is_dir() else 0
    if verz.is_dir():
        shutil.rmtree(verz)
    with db.verbindung() as c:
        v = c.execute("SELECT status FROM vorgang WHERE id=? AND mandant_ref=?", (vid, mandant_ref)).fetchone()
        if v is None or v["status"] == "geloescht":
            return None
        zahlen = {}
        links = [r["id"] for r in c.execute("SELECT id FROM link WHERE vorgang_id=? AND mandant_ref=?", (vid, mandant_ref))]
        zahlen["zugriff_log"] = sum(c.execute("DELETE FROM zugriff_log WHERE link_id=? AND mandant_ref=?", (lid, mandant_ref)).rowcount for lid in links)
        for tabelle in ("download_log", "download_link", "handoff", "abholung", "sitzung", "erinnerung", "unterschrift", "dokument", "link"):
            zahlen[tabelle] = c.execute("DELETE FROM %s WHERE vorgang_id=? AND mandant_ref=?" % tabelle, (vid, mandant_ref)).rowcount
        zahlen["handoff_log"] = c.execute("DELETE FROM handoff_log WHERE vorgang_id=?", (vid,)).rowcount        # nur Zähler der Handy-Übergaben (keine Mandantenspalte, Vorgangsnummern sind eindeutig)
        zahlen["dateien"] = dateien
        c.execute("UPDATE vorgang SET status='geloescht', geloescht_am=?, geloescht_grund=?, name=NULL, email_enc=NULL, code_hmac=NULL, code_kid=NULL, abgleich=NULL, bestaetigt_von=NULL, "
                  "email_herkunft=NULL, datum_herkunft=NULL, personen_herkunft=NULL, versand_fehler=NULL WHERE id=? AND mandant_ref=?", (db.jetzt(), grund, vid, mandant_ref))
        db.loeschprotokoll_schreiben(c, grund, mandant_ref, vid, zahlen)
        db.audit_in(c, mandant_ref, "system", "vorgang_geloescht", "vorgang=%d grund=%s" % (vid, grund))
    return zahlen
