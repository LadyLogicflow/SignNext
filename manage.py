"""Betriebskommandos (im Container: ``docker compose exec signnext python manage.py <Kommando>``):

  schluesselwechsel              Daten auf den aktuellen Hauptschlüssel umschlüsseln (siehe schluesselwechsel.py)
  jobs-einmal                    einen Durchlauf der Hintergrund-Jobs
  demo [mandant]                 NUR Testinstanz (SIGNNEXT_DEMO=1): Demo-Vorgang aus erfundenen PDFs, gibt Mitglieds-Link und Geburtsdatum aus (keine Mail)
  demo-download <nr> [mandant]   NUR Testinstanz: Download-Link für einen abgeschlossenen Demo-Vorgang"""
import sys

import config
import db

HILFE = "Aufruf: python manage.py schluesselwechsel | jobs-einmal | demo [mandant] | demo-download <vorgang> [mandant]"


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
        print("Geburtsdatum (erfunden):  %s  (eingeben als %s)" % (e["geburtsdatum"], e["code"][:2] + "." + e["code"][2:4] + "." + e["code"][4:]))
        if not config.basis_url():
            print("Hinweis: SIGNNEXT_BASIS_URL ist nicht gesetzt, vor den Pfad die Adresse der Testinstanz setzen.")
        return 0
    except demo.DemoFehler as f:
        print("Abbruch: %s" % f.code)
        return 1
    except (ValueError, IndexError):
        print(HILFE)
        return 2


def main(argv: list) -> int:
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
