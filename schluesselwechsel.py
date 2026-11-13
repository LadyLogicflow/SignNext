"""Schlüsselwechsel: verschlüsselte Werte (Mail-Passwörter, Mailadressen der Mitglieder) mit dem neuen Hauptschlüssel neu verschlüsseln.

Vorgehen im Betrieb: neuen Schlüssel als SIGNNEXT_HAUPTSCHLUESSEL setzen, den bisherigen als SIGNNEXT_HAUPTSCHLUESSEL_ALT, ``python manage.py schluesselwechsel`` ausführen,
danach prüfen, dass nichts mehr unter dem alten Schlüssel liegt. Der Code-HMAC des Geburtsdatums lässt sich nicht umrechnen (das Datum wird nie gespeichert);
er bleibt am alten Schlüssel, der alte Schlüssel muss also konfiguriert bleiben, bis alle betroffenen Vorgänge abgelaufen oder gelöscht sind (Link: 7 Tage)."""
import config
import db
import schluessel


def wechseln() -> dict:
    """Liefert Zahlen (umgeschluesselt, schon_aktuell). Bricht ohne beide Schlüssel ab; bei einem Fehler wird nichts geändert (eine Transaktion)."""
    schluessel.aktuell()
    if config.hauptschluessel_alt() is None:
        raise schluessel.HauptschluesselFehlt("SIGNNEXT_HAUPTSCHLUESSEL_ALT fehlt")
    neu = schluessel.kid(schluessel.aktuell())
    zahlen = {"umgeschluesselt": 0, "schon_aktuell": 0}
    with db.verbindung() as c:
        ziele = [("mail_zugang", "mandant_ref", "passwort_enc", "mailpasswort"), ("vorgang", "id", "email_enc", "mitglied-mail")]
        for tabelle, schl, spalte, zweck in ziele:
            for z in c.execute("SELECT %s AS k, mandant_ref, %s AS wert FROM %s WHERE %s IS NOT NULL" % (schl, spalte, tabelle, spalte)).fetchall():
                if z["wert"].split(":", 2)[1:2] == [neu]:
                    zahlen["schon_aktuell"] += 1
                    continue
                c.execute("UPDATE %s SET %s=? WHERE %s=?" % (tabelle, spalte, schl), (schluessel.umschluesseln(z["mandant_ref"], zweck, z["wert"]), z["k"]))
                zahlen["umgeschluesselt"] += 1
    return zahlen
