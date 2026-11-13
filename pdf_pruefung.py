"""Sicherheitsprüfung fremder PDFs vor der Auswertung (läuft im Kindprozess, siehe pdf_worker.py).

Abgelehnt werden: kein PDF, verschlüsselt, zu viele Seiten, aktive Inhalte (JavaScript, OpenAction, Launch, eingebettete Dateien, RichMedia, XFA).
Gewöhnliche Links (URI) und Formularfelder ohne Aktionen sind erlaubt."""
import io

from pypdf import PdfReader

MAX_BYTES = 15 * 1024 * 1024
MAX_SEITEN = 30
# Klartext-Marken: fangen unkomprimierte Fälle ab, die Strukturprüfung unten die übrigen.
ROH_MARKEN = (b"/JavaScript", b"/EmbeddedFile", b"/OpenAction", b"/Launch", b"/RichMedia")
AKTIONEN_VERBOTEN = {"/JavaScript", "/Launch", "/ImportData", "/Rendition", "/GoToE"}
ANNOT_VERBOTEN = {"/FileAttachment", "/RichMedia", "/Screen", "/Movie", "/Sound", "/3D"}


class Abgelehnt(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _aktion_pruefen(aktion) -> None:
    """Prüft eine Aktion samt Folgeaktionen (/Next) auf verbotene Typen."""
    gesehen = 0
    while aktion is not None and gesehen < 20:
        aktion = aktion.get_object() if hasattr(aktion, "get_object") else aktion
        if not hasattr(aktion, "get"):
            return
        if str(aktion.get("/S", "")) in AKTIONEN_VERBOTEN or "/JS" in aktion:
            raise Abgelehnt("aktive_inhalte")
        folge = aktion.get("/Next")
        if isinstance(folge, list):
            for f in folge:
                _aktion_pruefen(f)
            return
        aktion = folge
        gesehen += 1


def pruefe(daten: bytes) -> PdfReader:
    """Wirft Abgelehnt(code). Liefert bei Erfolg den geöffneten Reader."""
    if len(daten) > MAX_BYTES:
        raise Abgelehnt("zu_gross")
    if daten[:1024].find(b"%PDF-") < 0:
        raise Abgelehnt("kein_pdf")
    for marke in ROH_MARKEN:
        if marke in daten:
            raise Abgelehnt("aktive_inhalte")
    try:
        reader = PdfReader(io.BytesIO(daten))
        if reader.is_encrypted:
            raise Abgelehnt("verschluesselt")
        seiten = len(reader.pages)
    except Abgelehnt:
        raise
    except Exception:
        raise Abgelehnt("defekt")
    if seiten < 1:
        raise Abgelehnt("defekt")
    if seiten > MAX_SEITEN:
        raise Abgelehnt("zu_viele_seiten")
    try:
        root = reader.trailer["/Root"]
        if "/OpenAction" in root or "/AA" in root:
            raise Abgelehnt("aktive_inhalte")
        namen = root.get("/Names")
        if namen is not None:
            namen = namen.get_object()
            if "/JavaScript" in namen or "/EmbeddedFiles" in namen:
                raise Abgelehnt("aktive_inhalte")
        form = root.get("/AcroForm")
        if form is not None:
            form = form.get_object()
            if "/XFA" in form:
                raise Abgelehnt("aktive_inhalte")
        for seite in reader.pages:
            if "/AA" in seite:
                raise Abgelehnt("aktive_inhalte")
            annots = seite.get("/Annots")
            if annots is None:
                continue
            for a in annots.get_object():
                a = a.get_object()
                if str(a.get("/Subtype", "")) in ANNOT_VERBOTEN or "/AA" in a:
                    raise Abgelehnt("aktive_inhalte")
                if "/A" in a:
                    _aktion_pruefen(a["/A"])
    except Abgelehnt:
        raise
    except Exception:
        raise Abgelehnt("defekt")
    return reader
