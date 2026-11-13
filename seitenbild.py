"""Seitenbilder des echten Dokuments für die Unterschriftsseite: JPEG, 834 Pixel Breite, Qualität 82, Seite für Seite auf Abruf, nichts wird gespeichert.

Gerendert wird im Kindprozess (pdf_worker.py render), mit denselben Speicher- und Zeitgrenzen wie die Auswertung: Das Dokument ist fremder Inhalt."""
import os
import subprocess
import sys
import tempfile

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_worker.py")
TIMEOUT_SEKUNDEN = 40
MAX_AUSGABE = 4 * 1024 * 1024


class Fehler(Exception):
    pass


def rendern(pdf_bytes: bytes, seite: int) -> bytes:
    """JPEG der Seite ``seite`` (ab 1). Wirft Fehler."""
    umgebung = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run([sys.executable, WORKER, "render", str(int(seite))], input=pdf_bytes, capture_output=True, timeout=TIMEOUT_SEKUNDEN,
                           cwd=tempfile.gettempdir(), env=umgebung)
    except subprocess.TimeoutExpired:
        raise Fehler("zeitueberschreitung")
    if p.returncode != 0 or not p.stdout.startswith(b"\xff\xd8") or len(p.stdout) > MAX_AUSGABE:
        raise Fehler("render_fehlgeschlagen")
    return p.stdout
