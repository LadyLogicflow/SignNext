// SignNext Handy-Seite (/h): Unterschrift auf dem Handy zeichnen und an den Computer übergeben - CSP 'self' konform
// Der Link (QR-Code vom Computer) trägt das Kennzeichen nur im Fragment (#...), es geht nie in der Adresse an den Server.
'use strict';

const API = '/api/v1/handy';
const ALLGEMEINER_FEHLER = 'Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.';
const LINK_UNGUELTIG = 'Dieser Link gilt nicht mehr. Bitte am Computer einen neuen QR-Code anzeigen.';
let token = null;
let zeichenfeld = null;

document.addEventListener('DOMContentLoaded', async () => {
    token = window.location.hash.substring(1);
    history.replaceState(null, '', window.location.pathname);

    const knopf = document.getElementById('btn-uebertragen');
    zeichenfeld = Zeichenfeld.binde(document.getElementById('zeichenfeld'), {
        onAenderung: (inhalt) => {
            knopf.disabled = !inhalt;
            if (inhalt) loescheMeldung();
        }
    });
    knopf.addEventListener('click', uebertragen);

    if (!token) {
        linkUngueltig(LINK_UNGUELTIG);
        return;
    }
    try {
        const info = await api('/info', {token});
        const d = info.dokument && info.dokument.von > 1 ? info.dokument : null;
        const teile = [];
        if (d) teile.push(`Dokument ${d.nr} von ${d.von}`);
        if (info.personen > 1) teile.push(`Person ${info.person} von ${info.personen}`);
        if (teile.length) zeichenfeld.setzeBeschriftung(`Unterschrift ${teile.join(' · ')}`);
        if (d) {
            const zeile = document.getElementById('handy-dokument');
            zeile.textContent = `Sie unterschreiben: Dokument ${d.nr} von ${d.von}: ${d.titel}`;
            zeile.classList.remove('hidden');
        }
    } catch (err) {
        if (err.status === 404) linkUngueltig(err.message);
        else zeigeMeldung(err.message);
    }
});

async function api(pfad, body) {
    let res;
    try {
        res = await fetch(API + pfad, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
    } catch (err) {
        throw Object.assign(new Error('Keine Verbindung. Bitte prüfen Sie Ihr Netz und versuchen Sie es erneut.'), {status: 0});
    }
    let data = {};
    try {
        data = await res.json();
    } catch (err) {
        data = {};
    }
    if (!res.ok) {
        throw Object.assign(new Error(data.meldung || data.error || ALLGEMEINER_FEHLER), {status: res.status, code: data.error});
    }
    return data;
}

async function uebertragen() {
    if (!zeichenfeld.hatInhalt()) {
        zeigeMeldung('Bitte unterschreiben Sie im Feld.');
        return;
    }
    const knopf = document.getElementById('btn-uebertragen');
    knopf.disabled = true;
    zeichenfeld.sperren(true);
    loescheMeldung();
    try {
        await api('/unterschrift', {token, bild: zeichenfeld.png()});
        token = null;
        zeichenfeld.leeren();
        document.getElementById('step-zeichnen').classList.add('hidden');
        document.getElementById('step-fertig').classList.remove('hidden');
    } catch (err) {
        zeichenfeld.sperren(false);
        knopf.disabled = !zeichenfeld.hatInhalt();
        if (err.status === 404 || err.code === 'schon_gezeichnet') linkUngueltig(err.message);
        else zeigeMeldung(err.message);
    }
}

// Der Link ist nicht (mehr) gültig: nur die Meldung zeigen, kein Zeichenfeld
function linkUngueltig(text) {
    document.getElementById('step-zeichnen').classList.add('hidden');
    zeigeMeldung(text);
}

function zeigeMeldung(text) {
    const el = document.getElementById('meldung');
    el.textContent = text;
    el.className = 'error';
}

function loescheMeldung() {
    const el = document.getElementById('meldung');
    el.textContent = '';
    el.className = 'error hidden';
}
