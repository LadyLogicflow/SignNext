"""QR-Code als SVG (serverseitig, Bibliothek segno, rein Python). Nur für Adressen, die SignNext selbst erzeugt (Demo-Link, Handy-Übergabe); der Inhalt wird nie protokolliert."""
import segno


def svg(adresse: str) -> str:
    """Inline-SVG (ohne XML-Kopf, ohne Skripte) des QR-Codes für ``adresse``. Fehlerkorrektur M, ruhiger Rand."""
    if not isinstance(adresse, str) or not adresse or len(adresse) > 500:
        raise ValueError("Adresse für den QR-Code ungültig")
    code = segno.make(adresse, error="m", micro=False)
    return code.svg_inline(scale=5, border=3, dark="#000000", light="#ffffff", title="QR-Code")
