"""Betriebskommandos: ``python manage.py schluesselwechsel`` (siehe schluesselwechsel.py)."""
import sys

import config
import db


def main(argv: list) -> int:
    if len(argv) != 2 or argv[1] != "schluesselwechsel":
        print("Aufruf: python manage.py schluesselwechsel")
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
