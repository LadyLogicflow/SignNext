"""Kindprozess: wertet EIN fremdes PDF aus (stdin = Bytes, stdout = JSON). Grenzen für Speicher und Rechenzeit, bei Fehlern keine Ausgabe von Inhalten."""
import json
import os
import resource
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SPEICHER = 2 * 1024 ** 3
CPU_SEKUNDEN = 30


def main() -> int:
    resource.setrlimit(resource.RLIMIT_AS, (SPEICHER, SPEICHER))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SEKUNDEN, CPU_SEKUNDEN))
    from pdf_analyse import analysiere
    from pdf_pruefung import Abgelehnt
    daten = sys.stdin.buffer.read()
    try:
        ergebnis = {"ok": True, **analysiere(daten)}
    except Abgelehnt as a:
        ergebnis = {"ok": False, "fehler": a.code}
    except BaseException:
        ergebnis = {"ok": False, "fehler": "defekt"}
    sys.stdout.write(json.dumps(ergebnis, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
