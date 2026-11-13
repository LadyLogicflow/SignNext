"""Prüfung und Neukodierung des Unterschriftsbildes. Gespeichert wird NUR das Bild, keine biometrischen Merkmale (Druck, Geschwindigkeit, Strichverlauf).

Das Bild kommt als PNG-Data-URI. Es wird mit Pillow geöffnet (Kopfdaten zuerst, damit riesige Bilder nie dekodiert werden), geprüft und als neues, schlankes PNG
kodiert: Zusatzdaten (Textblöcke, EXIF, eingebettete Daten hinter dem Bild) fallen dabei weg."""
import base64
import binascii
import io

from PIL import Image

PRAEFIX = "data:image/png;base64,"
MAX_BYTES = 200 * 1024
MAX_BREITE, MAX_HOEHE = 1200, 600
MIN_BREITE, MIN_HOEHE = 50, 20
MIN_TINTEN_PIXEL = 80


class Ungueltig(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def pruefen(data_uri) -> bytes:
    """Liefert die neu kodierten PNG-Bytes oder wirft Ungueltig(code): bild_format | bild_zu_gross | bild_ungueltig | bild_leer."""
    if not isinstance(data_uri, str) or not data_uri.startswith(PRAEFIX):
        raise Ungueltig("bild_format")
    if len(data_uri) > MAX_BYTES * 4 // 3 + 200:
        raise Ungueltig("bild_zu_gross")
    try:
        roh = base64.b64decode(data_uri[len(PRAEFIX):], validate=True)
    except (binascii.Error, ValueError):
        raise Ungueltig("bild_format")
    if len(roh) > MAX_BYTES:
        raise Ungueltig("bild_zu_gross")
    try:
        bild = Image.open(io.BytesIO(roh), formats=["PNG"])
        breite, hoehe = bild.size
        if getattr(bild, "is_animated", False) or getattr(bild, "n_frames", 1) != 1:
            raise Ungueltig("bild_ungueltig")
        if not (MIN_BREITE <= breite <= MAX_BREITE and MIN_HOEHE <= hoehe <= MAX_HOEHE):
            raise Ungueltig("bild_zu_gross" if breite > MAX_BREITE or hoehe > MAX_HOEHE else "bild_ungueltig")
        bild.load()
        rgba = bild.convert("RGBA")
    except Ungueltig:
        raise
    except Exception:
        raise Ungueltig("bild_ungueltig")
    weiss = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    flach = Image.alpha_composite(weiss, rgba).convert("L")
    tinte = sum(1 for p in flach.getdata() if p < 200)
    if tinte < MIN_TINTEN_PIXEL:
        raise Ungueltig("bild_leer")
    sauber = Image.frombytes("RGBA", rgba.size, rgba.tobytes())
    ausgabe = io.BytesIO()
    sauber.save(ausgabe, format="PNG", optimize=True)
    daten = ausgabe.getvalue()
    if len(daten) > MAX_BYTES:
        raise Ungueltig("bild_zu_gross")
    return daten
