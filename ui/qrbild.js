// SignNext QR-Bild, gemeinsam für die Unterschriftsseite und die Berater-Seiten - CSP 'self' konform.
// Das Backend liefert den QR-Code als SVG-Text ohne Namensraum (zum Einbetten gedacht). Als Bild (<img>) zeichnet ein Browser so ein SVG nicht,
// deshalb wird der Namensraum hier ergänzt. Das SVG wird nie als Markup in die Seite eingefügt, nur als Bild über eine Daten-Adresse gezeigt.
'use strict';

(function () {
    const SVG_NAMENSRAUM = 'http://www.w3.org/2000/svg';

    function adresse(svg) {
        if (typeof svg !== 'string') return null;
        let text = svg.trim();
        if (!/^<svg[\s>]/.test(text)) return null;
        const kopf = text.slice(0, text.indexOf('>') + 1);
        if (!/\sxmlns\s*=/.test(kopf)) text = '<svg xmlns="' + SVG_NAMENSRAUM + '"' + text.slice(4);
        return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(text);
    }

    window.QrBild = {adresse};
})();
