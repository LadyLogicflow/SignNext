// SignNext Berater-Seiten, gemeinsam: Demo-Banner, Sperrseite außerhalb des Heimnetzes, Link-Anzeige mit Kopieren-Knopf und QR - CSP 'self' konform
// Wird vor dem Seitenskript eingebunden. Jede Seite wartet mit `await Berater.bereit()`; false heißt: die Seite ist gesperrt, nichts weiter laden.
'use strict';

(function () {
    const STANDARD_BANNER = 'DEMO – Testdaten, keine echten Personendaten';
    const NUR_HEIMNETZ = 'Diese Demo ist nur im Heimnetz erreichbar.';
    let meAntwort = null;

    async function ladeMe() {
        try {
            const res = await fetch('/api/v1/me', {credentials: 'same-origin'});
            let data = {};
            try {
                data = await res.json();
            } catch (err) {
                data = {};
            }
            return {status: res.status, data};
        } catch (err) {
            return {status: 0, data: {}};
        }
    }

    function bannerZeigen(text) {
        if (document.getElementById('demo-banner')) return;
        const banner = document.createElement('div');
        banner.id = 'demo-banner';
        banner.className = 'demo-banner';
        banner.setAttribute('role', 'status');
        banner.textContent = text;
        const container = document.querySelector('.container');
        container.insertBefore(banner, container.firstChild);
    }

    // Eigene Seite statt Weiterleitung: keine Anmeldung im Portal, die Demo gibt es nur im Heimnetz
    function nurHeimnetzSeite() {
        const container = document.querySelector('.container');
        container.textContent = '';
        const kopf = document.createElement('header');
        const h1 = document.createElement('h1');
        h1.textContent = 'SignNext Demo';
        kopf.appendChild(h1);
        const main = document.createElement('main');
        main.id = 'app';
        const box = document.createElement('div');
        box.id = 'nur-heimnetz';
        box.className = 'info-box';
        box.setAttribute('role', 'alert');
        box.textContent = NUR_HEIMNETZ;
        main.appendChild(box);
        container.appendChild(kopf);
        container.appendChild(main);
    }

    async function bereit() {
        if (!meAntwort) meAntwort = ladeMe();
        const {status, data} = await meAntwort;
        if (status === 403 && data && data.grund === 'demo_nicht_erlaubt') {
            if (!document.getElementById('nur-heimnetz')) nurHeimnetzSeite();
            return false;
        }
        if (status === 200 && data && data.demo === true) bannerZeigen(data.demo_banner || STANDARD_BANNER);
        return true;
    }

    // true, wenn die Demo läuft (nur nach `await Berater.bereit()` sinnvoll)
    async function istDemo() {
        if (!meAntwort) meAntwort = ladeMe();
        const {status, data} = await meAntwort;
        return status === 200 && !!data && data.demo === true;
    }

    function qrAlsDatenAdresse(svg) {
        return QrBild.adresse(svg);                                                      // ergänzt den SVG-Namensraum, den das Backend weglässt
    }

    function kopieren(text, knopf) {
        const fertig = () => {
            const alt = knopf.textContent;
            knopf.textContent = 'Kopiert!';
            setTimeout(() => { knopf.textContent = alt; }, 2500);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(fertig, () => { knopf.textContent = 'Nicht möglich, bitte markieren und kopieren'; });
        } else {
            knopf.textContent = 'Nicht möglich, bitte markieren und kopieren';
        }
    }

    // Link (mit Kopieren-Knopf), optional QR-Code und Hinweis in einen Container schreiben (Demo: Anzeige am Bildschirm, wenn keine Mail geht)
    function linkBoxZeigen(container, {link, qr_svg, hinweis, titel}) {
        container.textContent = '';
        if (titel) {
            const h = document.createElement('strong');
            h.textContent = titel;
            container.appendChild(h);
        }
        const zeile = document.createElement('div');
        zeile.className = 'link-zeile';
        const feld = document.createElement('input');
        feld.type = 'text';
        feld.readOnly = true;
        feld.className = 'link-feld';
        feld.value = link;
        feld.setAttribute('aria-label', 'Zugangslink');
        feld.addEventListener('focus', () => feld.select());
        zeile.appendChild(feld);
        const knopf = document.createElement('button');
        knopf.type = 'button';
        knopf.className = 'btn btn-secondary link-kopieren';
        knopf.textContent = 'Link kopieren';
        knopf.addEventListener('click', () => kopieren(link, knopf));
        zeile.appendChild(knopf);
        container.appendChild(zeile);
        const adresse = qrAlsDatenAdresse(qr_svg);
        if (adresse) {
            const img = document.createElement('img');
            img.className = 'link-qr';
            img.alt = 'QR-Code zum Zugangslink';
            img.width = 180;
            img.height = 180;
            img.src = adresse;
            container.appendChild(img);
        }
        if (hinweis) {
            const p = document.createElement('p');
            p.className = 'link-hinweis';
            p.textContent = hinweis;
            container.appendChild(p);
        }
        container.classList.remove('hidden');
    }

    window.Berater = {bereit, istDemo, kopieren, linkBoxZeigen, qrAlsDatenAdresse};
})();
