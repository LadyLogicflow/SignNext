"""Erfundene Test-PDFs mit dem Aufbau der beiden Dokumenttypen (reportlab, nur für Tests). Alle Namen, Nummern und Adressen sind frei erfunden."""
import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

H = A4[1]

STNR = "123/4567/8901"
STNR_EHE = "123/4567/8902"
ID_A = "98765432101"
ID_B = "98765432102"
STNR_ZIFFERN = "12345678901"
STNR_EHE_ZIFFERN = "12345678902"
MNR = "1234567"
MAIL = "zzyzx.beispiel@example.org"
NAME = "Zzyzx"
VORNAME = "Quentin"
DOB = "01.02.1960"
PERSONENDATEN = (STNR, STNR_EHE, STNR_ZIFFERN, STNR_EHE_ZIFFERN, ID_A, ID_B, MNR, MAIL, NAME, VORNAME, DOB)


def _zeile(c, y_top, text, x=62, groesse=9):
    c.setFont("Helvetica", groesse)
    c.drawString(x, H - y_top, text)


def _fuell(c, ab, bis, schritt=12, text="Dies ist ein erfundener Fülltext für den Test der Textebene dieses Dokuments."):
    y = ab
    while y < bis:
        _zeile(c, y, text)
        y += schritt


def _weiss_setzen(c, modus):
    if modus == "rgb":
        c.setFillColorRGB(1, 1, 1)
    elif modus == "gray":
        c.setFillGray(1)
    elif modus == "cmyk":
        c.setFillColorCMYK(0, 0, 0, 0)
    elif modus == "fast":
        c.setFillColorRGB(0.99, 0.99, 0.99)
    elif modus == "hellgrau":
        c.setFillColorRGB(0.9, 0.9, 0.9)
    else:                                               # 'cover', 'alpha', 'none': schwarz
        c.setFillColorRGB(0, 0, 0)


def b_pdf(personen=2, weiss="rgb", weisse_zeile="{mnr}; {vorname}; {name}; {mail}", zweite_weisse=None, mnr_text=MNR, stnr_zeile=None, id_zeile=None,
          bloecke=(1, 4), seiten=4, gruppen_je_block=None, text_mnr=True):
    """Anschreiben: weiße Zeile oben auf Seite 1, Kopfdaten, Unterschriftsblöcke auf den angegebenen Seiten."""
    puffer = io.BytesIO()
    c = canvas.Canvas(puffer, pagesize=A4)
    for s in range(1, seiten + 1):
        if s == 1:
            if weisse_zeile is not None:
                _weiss_setzen(c, weiss)
                z = weisse_zeile.format(mnr=MNR, vorname=VORNAME, name=NAME, mail=MAIL)
                if weiss == "alpha":
                    c.setFillAlpha(0)
                _zeile(c, 35, z, x=40)
                if weiss == "cover":
                    c.setFillColorRGB(1, 1, 1)
                    c.rect(30, H - 45, 500, 14, stroke=0, fill=1)
                c.setFillAlpha(1)
                if zweite_weisse:
                    _weiss_setzen(c, "rgb")
                    _zeile(c, 60, zweite_weisse, x=40)
            c.setFillColorRGB(0, 0, 0)
            if text_mnr:
                _zeile(c, 355, "Mitglieds-Nr.: %s 99 1234" % mnr_text)
            _zeile(c, 366, stnr_zeile or ("Steuernummer: %s Ehepartner: %s" % (STNR, STNR_EHE)))
            _zeile(c, 378, id_zeile or ("Steuer-Idnr.: %s Ehepartner: %s" % (ID_A, ID_B)))
            _fuell(c, 100, 340, 14)
        else:
            c.setFillColorRGB(0, 0, 0)
            _fuell(c, 80, 500, 14)
        if s in bloecke:
            _zeile(c, 700, "Neuss, 13.11.2026")
            n = gruppen_je_block or personen
            _zeile(c, 758, " ".join(["_" * 28] * n))
            _zeile(c, 769, "Quentin Zzyzx" if n == 1 else "Quentin Zzyzx    Erika Zzyzx")
        c.showPage()
    c.save()
    return puffer.getvalue()


def a_pdf(ehegatte=True, stnr=STNR, id_a=ID_A, id_b=ID_B, mnr=MNR, mitwirkung=True, fv_zeile="15720 FV NRW", doppelt=False, seiten=12, kind_ids=True,
          zweiter_block=False, unterschrift_text="Datum, Unterschrift, gegebenenfalls Unterschrift Ehegatte / Lebenspartner(in)"):
    """Steuerprotokoll: Wasserzeichen und Kopfzeile auf jeder Seite, Hauptvordruck auf Seite 1, Mitwirkungsblock mit Unterschriftsstelle auf der vorletzten Seite."""
    puffer = io.BytesIO()
    c = canvas.Canvas(puffer, pagesize=A4)
    unterschrift_seite = seiten - 1
    for s in range(1, seiten + 1):
        c.setFillColorRGB(0, 0, 0)
        _zeile(c, 17, "*** Vorschau *** *** Vorschau *** *** Vorschau ***", x=43)
        _zeile(c, 30, "Finanzamt Neuss Steuernummer %s erstellt am: 13.11.2026" % stnr, x=43)
        if s == 1:
            _zeile(c, 248, "8 Identifikationsnummer %s" % id_a, x=71)
            _zeile(c, 259, "8 Geburtsdatum %s" % DOB, x=71)
            _zeile(c, 271, "9 Name %s" % NAME, x=71)
            _zeile(c, 283, "10 Vorname %s" % VORNAME, x=71)
            if ehegatte:
                _zeile(c, 454, "20 Identifikationsnummer %s" % id_b, x=68)
                _zeile(c, 466, "20 Geburtsdatum 03.04.1962", x=68)
                _zeile(c, 478, "21 Name Zzyzx", x=68)
                _zeile(c, 489, "22 Vorname Erika", x=68)
            _fuell(c, 80, 230, 14)
        elif s == 4 and kind_ids:
            _zeile(c, 363, "4 Identifikationsnummer 99999999999", x=71)       # Anlage Kind: darf nie als Person A/B zählen
            _zeile(c, 387, "6 Geburtsdatum 09.09.2010", x=71)
            _zeile(c, 399, "8 Identifikationsnummer 99999999998", x=71)       # gleiche Zeilennummer wie Person A, aber nicht auf Seite 1
            _fuell(c, 80, 340, 14)
        elif s != unterschrift_seite:
            _fuell(c, 80, 500, 14)
        if s == unterschrift_seite:
            _fuell(c, 80, 340, 14)
            _zeile(c, 417, "Bei der Anfertigung dieser Erklärung hat mitgewirkt:" if mitwirkung else "Anderer Block:", x=82)
            for i, t in enumerate(["Beispiel Beratungsstelle e.V.", "Musterweg 1", "12345 Musterstadt"]):
                _zeile(c, 436 + 12 * i, t, x=82)
            _zeile(c, 507, fv_zeile, x=82)
            _zeile(c, 519, mnr, x=82)
            if doppelt:
                _zeile(c, 531, "15720 FV NRW", x=82)
                _zeile(c, 543, "7654321", x=82)
            _zeile(c, 560, "Die Steuererklärung wurde unter Mitwirkung einer selbständig tätigen Person erstellt.", x=82)
            _zeile(c, 617, "Die vorstehenden Daten dieser Steuererklärung wurden geprüft.", x=82)
            _zeile(c, 697, " ".join(["_" * 37] * (2 if True else 1)), x=82)
            _zeile(c, 715, unterschrift_text, x=82)
            if zweiter_block:
                _zeile(c, 740, " ".join(["_" * 37] * 2), x=82)
                _zeile(c, 752, "Datum, Unterschrift, noch eine", x=82)
        _zeile(c, 782, "*** Vorschau *** *** Vorschau ***", x=43)
        c.showPage()
    c.save()
    return puffer.getvalue()


def bild_pdf():
    """Ein Scan: nur ein Bild, keine Textebene."""
    from PIL import Image
    bild = Image.new("RGB", (200, 200), (200, 200, 200))
    b = io.BytesIO()
    bild.save(b, "PNG")
    b.seek(0)
    from reportlab.lib.utils import ImageReader
    puffer = io.BytesIO()
    c = canvas.Canvas(puffer, pagesize=A4)
    c.drawImage(ImageReader(b), 50, 400, 400, 400)
    c.showPage()
    c.save()
    return puffer.getvalue()


def mit_encrypt(daten: bytes) -> bytes:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(daten)))
    w.encrypt("passwort-fuer-den-test")
    p = io.BytesIO()
    w.write(p)
    return p.getvalue()


def mit_javascript(daten: bytes, art: str) -> bytes:
    """art: 'js' (Dokument-JavaScript), 'openaction', 'embedded', 'launch', 'annot_js', 'aa'."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(daten)))
    if art == "js":
        js = DictionaryObject({NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("app.alert('x');")})
        namen = DictionaryObject({NameObject("/Names"): ArrayObject([TextStringObject("x"), w._add_object(js)])})
        w._root_object[NameObject("/Names")] = DictionaryObject({NameObject("/JavaScript"): w._add_object(namen)})
    elif art == "embedded":
        w.add_attachment("anlage.txt", b"inhalt")
    elif art == "xfa":
        w._root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject([]), NameObject("/XFA"): TextStringObject("x")})
    elif art == "fileattach":
        annot = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/FileAttachment"),
                                  NameObject("/Rect"): ArrayObject([FloatObject(10), FloatObject(10), FloatObject(50), FloatObject(20)])})
        w.pages[0][NameObject("/Annots")] = ArrayObject([w._add_object(annot)])
    elif art == "link":
        aktion = DictionaryObject({NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject("https://example.org")})
        annot = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
                                  NameObject("/Rect"): ArrayObject([FloatObject(10), FloatObject(10), FloatObject(50), FloatObject(20)]), NameObject("/A"): w._add_object(aktion)})
        w.pages[0][NameObject("/Annots")] = ArrayObject([w._add_object(annot)])
    elif art == "openaction":
        aktion = DictionaryObject({NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject("https://example.org")})
        w._root_object[NameObject("/OpenAction")] = w._add_object(aktion)
    elif art == "aa":
        w.pages[0][NameObject("/AA")] = DictionaryObject()
    elif art in ("launch", "annot_js", "annot_jskey"):
        aktion = DictionaryObject({NameObject("/S"): NameObject({"launch": "/Launch", "annot_js": "/JavaScript", "annot_jskey": "/URI"}[art])})
        if art in ("annot_js", "annot_jskey"):
            aktion[NameObject("/JS")] = TextStringObject("app.alert(1)")
        annot = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
                                  NameObject("/Rect"): ArrayObject([]), NameObject("/A"): w._add_object(aktion)})
        w.pages[0][NameObject("/Annots")] = ArrayObject([w._add_object(annot)])
    p = io.BytesIO()
    w.write(p)
    return p.getvalue()
