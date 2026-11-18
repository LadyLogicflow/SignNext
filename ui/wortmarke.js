// SignNext Produkt-Wortmarke "SignNext" wie bei BookNext/SortNext (BSt-Next-Stylebook): "Sign" weiß und fett (Archivo Black), "Next" in Lime, dahinter der Chevron.
// Gebaut aus Text und einem kleinen SVG-Pfad (kein Bild, nichts aus fremder Quelle) - CSP 'self' konform. Die Farben stehen in berater.css.
'use strict';

(function () {
    const SVG_NAMENSRAUM = 'http://www.w3.org/2000/svg';

    function chevron() {
        const svg = document.createElementNS(SVG_NAMENSRAUM, 'svg');
        svg.setAttribute('viewBox', '0 0 20 28');
        svg.setAttribute('class', 'wortmarke-chevron');
        svg.setAttribute('aria-hidden', 'true');
        const pfad = document.createElementNS(SVG_NAMENSRAUM, 'path');
        pfad.setAttribute('d', 'M4 4 L15 14 L4 24');
        pfad.setAttribute('fill', 'none');
        pfad.setAttribute('stroke', '#A3E635');
        pfad.setAttribute('stroke-width', '5');
        pfad.setAttribute('stroke-linecap', 'round');
        pfad.setAttribute('stroke-linejoin', 'round');
        svg.appendChild(pfad);
        return svg;
    }

    function zeigen(ziel) {
        if (!ziel || ziel.firstChild) return;
        const basis = document.createElement('span');
        basis.className = 'wortmarke-sign';
        basis.textContent = 'Sign';
        const next = document.createElement('span');
        next.className = 'wortmarke-next';
        next.textContent = 'Next';
        ziel.append(basis, next, chevron());
    }

    window.Wortmarke = {zeigen};
})();
