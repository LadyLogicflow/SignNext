"""Kindprozess für fremde PDFs. Zwei Arbeitsweisen, beide mit Grenzen für Speicher und Rechenzeit, bei Fehlern keine Ausgabe von Inhalten:
- ohne Argument: wertet EIN PDF aus (stdin = Bytes, stdout = JSON), siehe pdf_analyse.py;
- ``render <seite>``: stdout = JPEG der Seite (ab 1), 834 Pixel breit, Qualität 82 (Seitenbilder der Unterschriftsseite)."""
import io
import json
import os
import resource
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SPEICHER = 2 * 1024 ** 3
CPU_SEKUNDEN = 30
BREITE_PIXEL = 834
JPEG_QUALITAET = 82


def _grenzen():
    resource.setrlimit(resource.RLIMIT_AS, (SPEICHER, SPEICHER))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SEKUNDEN, CPU_SEKUNDEN))


def auswerten() -> int:
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


def rendern(seite: int) -> int:
    import pypdfium2 as pdfium
    from pdf_pruefung import Abgelehnt, pruefe
    daten = sys.stdin.buffer.read()
    try:
        pruefe(daten)                                    # dieselben Regeln wie beim Eingang, auch hier
        pdf = pdfium.PdfDocument(daten)
        if not 1 <= seite <= len(pdf):
            return 3
        page = pdf[seite - 1]
        breite_pt = page.get_width()
        bild = page.render(scale=BREITE_PIXEL / breite_pt).to_pil().convert("RGB")
        ausgabe = io.BytesIO()
        bild.save(ausgabe, "JPEG", quality=JPEG_QUALITAET)
    except (Abgelehnt, Exception):
        return 2
    sys.stdout.buffer.write(ausgabe.getvalue())
    return 0


def main(argv: list) -> int:
    _grenzen()
    if len(argv) == 3 and argv[1] == "render" and argv[2].isdigit():
        return rendern(int(argv[2]))
    return auswerten()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
