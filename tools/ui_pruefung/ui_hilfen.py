"""Gemeinsame Hilfen der jsdom-Prüfung: Treiber aufrufen, Voraussetzungen prüfen."""
import json
import shutil
import subprocess
from pathlib import Path

HIER = Path(__file__).resolve().parent
DRIVE = HIER / "drive_sn.js"
BEREIT = (shutil.which("node") is not None) and (HIER / "node_modules" / "jsdom").is_dir()
HINWEIS = "braucht Node und jsdom (siehe README.md): cd tools/ui_pruefung && npm install jsdom@24 --no-save"


def lauf(**kw):
    p = subprocess.run(["node", str(DRIVE), json.dumps(kw)], capture_output=True, text=True, timeout=120, cwd=str(HIER))
    zeilen = [z for z in p.stdout.splitlines() if z.startswith("RESULT ")]
    if not zeilen:
        raise AssertionError(p.stdout[-400:] + p.stderr[-400:])
    o = json.loads(zeilen[-1][7:])
    if not o["ok"]:
        raise AssertionError(o["fehler"])
    return o
