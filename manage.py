"""Betriebskommandos (im Container: ``docker compose exec signnext python manage.py <Kommando>``):

  schluesselwechsel              Daten auf den aktuellen Hauptschlüssel umschlüsseln (siehe schluesselwechsel.py)
  jobs-einmal                    einen Durchlauf der Hintergrund-Jobs
  demo [mandant]                 NUR Testinstanz (SIGNNEXT_DEMO=1): Demo-Vorgang aus erfundenen PDFs, gibt Mitglieds-Link und Geburtsdatum aus (keine Mail)
  demo-download <nr> [mandant]   NUR Testinstanz: Download-Link für einen abgeschlossenen Demo-Vorgang
  tarif-liste                    Tarife (Limit je Monat, Preis in Cent), Zuordnungen, Pilot-Standard, Sperrmodus
  tarif-preis <basis|plus> <Cent>            Preis des Tarifs (nur ein Wert; SignNext rechnet nichts ab). Mit Preis > 0 gilt die Sperre am Limit (SIGNNEXT_SPERRE=auto)
  tarif-limit <basis|plus> <Zahl|unbegrenzt> Limit je Kalendermonat
  tarif-setzen <Mandant> <basis|plus>        Tarif einer Beratungsstelle (wirkt sofort)
  zaehlung [Mandant] [JJJJ-MM]               Zahlen je Beratungsstelle und Monat für die manuelle Abrechnung (nur Zahlen)"""
import sys

import config
import db

HILFE = ("Aufruf: python manage.py schluesselwechsel | jobs-einmal | demo [mandant] | demo-download <vorgang> [mandant] | tarif-liste | tarif-preis <tarif> <cent> | "
         "tarif-limit <tarif> <zahl|unbegrenzt> | tarif-setzen <mandant> <tarif> | zaehlung [mandant] [JJJJ-MM]")


def _demo(argv: list) -> int:
    import demo
    db.init(str(config.daten_verzeichnis() / "signnext.db"))
    try:
        if argv[1] == "demo":
            e = demo.vorgang_erzeugen(argv[2] if len(argv) > 2 else "demo")
            print("Demo-Vorgang Nr. %d (%d Person(en))" % (e["vorgang_id"], e["personen"]))
            print("Mitglieds-Link:  %s" % e["link"])
        else:
            e = demo.download_link_erzeugen(int(argv[2]), argv[3] if len(argv) > 3 else "demo")
            print("Download-Link für Vorgang Nr. %d:  %s" % (e["vorgang_id"], e["link"]))
        print("Geburtsdatum (erfunden):  %s  (eingeben als %s)" % (e["geburtsdatum"], e["code"]))
        if not config.basis_url():
            print("Hinweis: SIGNNEXT_BASIS_URL ist nicht gesetzt, vor den Pfad die Adresse der Testinstanz setzen.")
        return 0
    except demo.DemoFehler as f:
        print("Abbruch: %s" % f.code)
        return 1
    except (ValueError, IndexError):
        print(HILFE)
        return 2


def _tarif(argv: list) -> int:
    import tarif
    db.init(str(config.daten_verzeichnis() / "signnext.db"))
    try:
        kommando = argv[1]
        if kommando == "tarif-liste" and len(argv) == 2:
            e = tarif.liste()[0]
            for t in e["tarife"]:
                print("Tarif %-6s Limit je Monat: %-10s Preis: %d Cent" % (t["name"], "unbegrenzt" if t["limit_monat"] is None else t["limit_monat"], t["preis_cent"]))
            print("Zuordnungen: %s" % (", ".join("%s=%s" % (z["mandant_ref"], z["tarif"]) for z in e["zuordnung"]) or "keine"))
            print("Standard Tarif plus für: %s" % (", ".join(e["plus_referenzen"]) or "niemanden"))
            print("Sperrmodus (SIGNNEXT_SPERRE): %s" % e["sperre"])
        elif kommando == "tarif-preis" and len(argv) == 4:
            tarif.preis_setzen(argv[2], int(argv[3]))
            print("Preis von %s: %d Cent" % (argv[2], int(argv[3])))
        elif kommando == "tarif-limit" and len(argv) == 4:
            limit = None if argv[3] == "unbegrenzt" else int(argv[3])
            tarif.limit_setzen(argv[2], limit)
            print("Limit von %s: %s" % (argv[2], "unbegrenzt" if limit is None else limit))
        elif kommando == "tarif-setzen" and len(argv) == 4:
            tarif.zuordnen(argv[2], argv[3])
            print("Beratungsstelle %s: Tarif %s" % (argv[2], argv[3]))
        elif kommando == "zaehlung" and len(argv) in (2, 3, 4):
            monat = None
            rest = argv[2:]
            if rest and len(rest[-1]) == 7 and rest[-1][4] == "-":
                monat = rest[-1]
                rest = rest[:-1]
            mandant = rest[0] if rest else None
            zeilen = tarif.zaehlung(mandant, monat)
            for z in zeilen:
                print("%s  %-12s %4d Vorgänge  Tarif %s" % (z["monat"], z["mandant_ref"], z["anzahl"], z["tarif"]))
            if not zeilen:
                print("Keine Zählung für diese Auswahl.")
        else:
            print(HILFE)
            return 2
        return 0
    except ValueError:
        print("Abbruch: Eingabe ungültig")
        return 1


def main(argv: list) -> int:
    if len(argv) >= 2 and argv[1] in ("tarif-liste", "tarif-preis", "tarif-limit", "tarif-setzen", "zaehlung"):
        return _tarif(argv)
    if len(argv) >= 2 and argv[1] in ("demo", "demo-download") and (len(argv) in (2, 3) if argv[1] == "demo" else len(argv) in (3, 4)):
        return _demo(argv)
    if len(argv) == 2 and argv[1] == "jobs-einmal":
        import jobs
        db.init(str(config.daten_verzeichnis() / "signnext.db"))
        print(" ".join("%s=%s" % (k, v) for k, v in sorted(jobs.lauf().items())))
        return 0
    if len(argv) != 2 or argv[1] != "schluesselwechsel":
        print(HILFE)
        return 2
    import schluesselwechsel
    db.init(str(config.daten_verzeichnis() / "signnext.db"))
    try:
        zahlen = schluesselwechsel.wechseln()
    except Exception as e:
        print("Abbruch: %s" % type(e).__name__)
        return 1
    print("umgeschluesselt: %d, schon aktuell: %d" % (zahlen["umgeschluesselt"], zahlen["schon_aktuell"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
