// SignNext Unterschriftsseite - CSP 'self' konform
'use strict';

const API = '/api/v1/zugang';
const HANDY_ABFRAGE_MS = 2000;
let token = null;
let textHash = null;
let currentPerson = 1;
let personenGesamt = 1;
let gesehenSeiten = new Set();
let zeichenfeld = null;
let handoff = null;           // {id, ende (ms), timer} solange ein QR-Code für das Handy offen ist

// Init
document.addEventListener('DOMContentLoaded', async () => {
    token = window.location.hash.substring(1);
    if (!token) {
        showError('code-error', 'Kein gültiger Zugangslink');
        return;
    }
    history.replaceState(null, '', window.location.pathname);

    try {
        const hinweis = await api('GET', '/hinweis');
        const hinweisEl = document.getElementById('hinweis-text');
        hinweisEl.textContent = `${hinweis.kennung || ''}\n\n${hinweis.zugangshinweis || 'Bitte geben Sie Ihr Geburtsdatum ein.'}`;
        if (!hinweis.freigegeben) {
            hinweisEl.textContent += '\n\n[ENTWURF - Texte noch nicht freigegeben]';
        }
    } catch (err) {
        console.error(err);
    }

    document.getElementById('form-code').addEventListener('submit', checkCode);
    document.getElementById('btn-gelesen').addEventListener('click', markGelesen);
    document.getElementById('btn-unterschreiben').addEventListener('click', sendUnterschrift);
    document.getElementById('btn-neu-laden').addEventListener('click', () => location.reload());
    document.getElementById('btn-handy').addEventListener('click', handyStarten);
    document.getElementById('btn-handy-neu').addEventListener('click', handyStarten);
    document.getElementById('btn-handy-abbrechen').addEventListener('click', handyAbbrechen);
    zeichenfeld = Zeichenfeld.binde(document.getElementById('zeichenfeld'), {onAenderung: () => hideError('unterschrift-error')});
});

class ApiFehler extends Error {
    constructor(meldung, status, code, daten) {
        super(meldung);
        this.status = status;
        this.code = code;
        this.daten = daten;
    }
}

async function api(method, path, body = null) {
    const opts = {
        method,
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'Origin': window.location.origin}
    };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(API + path, opts);
    let data = {};
    try {
        data = await res.json();
    } catch (err) {
        data = {};
    }

    if (!res.ok) {
        if (res.status === 401 && data.error === 'sitzung_ungueltig') {
            sitzungAbgelaufen();
            const e = new ApiFehler('Sitzung abgelaufen', res.status, data.error, data);
            e.sitzung = true;
            throw e;
        }
        throw new ApiFehler(data.meldung || data.error || 'Fehler', res.status, data.error, data);
    }
    return data;
}

// Meldungen stehen im Text der Seite (nie als Dialog): fehler = rot, info = Hinweisfeld
function zeigeMeldung(text, art = 'fehler', neuLaden = false) {
    const el = document.getElementById('meldung');
    el.textContent = text;
    el.className = art === 'info' ? 'info-box' : 'error';
    document.getElementById('btn-neu-laden').classList.toggle('hidden', !neuLaden);
}

function loescheMeldung() {
    const el = document.getElementById('meldung');
    el.textContent = '';
    el.className = 'error hidden';
    document.getElementById('btn-neu-laden').classList.add('hidden');
}

function sitzungAbgelaufen() {
    handyBeenden();
    zeigeMeldung('Ihre Sitzung ist abgelaufen. Bitte laden Sie die Seite neu und geben Sie Ihr Geburtsdatum erneut ein.', 'fehler', true);
}

async function checkCode(e) {
    e.preventDefault();
    const code = document.getElementById('code').value.replace(/\./g, '');
    if (!/^\d{6}$/.test(code)) {
        showError('code-error', 'Bitte im Format TT.MM.JJ eingeben');
        return;
    }

    try {
        await api('POST', '/pruefen', {token, code});
        hideError('code-error');
        loescheMeldung();
        document.getElementById('versuch-info').classList.add('hidden');
        await loadSeiten();
    } catch (err) {
        if (err.code === 'angaben_stimmen_nicht') {
            const {versuch, von, gesperrt} = err.daten;
            const info = document.getElementById('versuch-info');
            if (gesperrt) {
                showError('code-error', 'Der Link wurde nach zu vielen Fehlversuchen gesperrt');
                info.classList.add('hidden');
            } else {
                showError('code-error', 'Das Geburtsdatum stimmt nicht');
                info.textContent = `Versuch ${versuch} von ${von}`;
                info.classList.remove('hidden');
            }
        } else if (!err.sitzung) {
            showError('code-error', err.message);
        }
    }
}

async function loadSeiten() {
    showStep('step-seiten');
    const status = await api('GET', '/status');
    const {seiten} = status;

    document.getElementById('seiten-gesamt').textContent = seiten;
    document.getElementById('seiten-fortschritt').classList.remove('hidden');

    const container = document.getElementById('seiten-container');
    for (let i = 1; i <= seiten; i++) {
        const img = document.createElement('img');
        img.alt = `Seite ${i}`;
        img.addEventListener('load', () => {
            gesehenSeiten.add(i);
            updateSeitenProgress(seiten);
        });
        container.appendChild(img);

        try {
            const r = await fetch(`${API}/seite/${i}`, {credentials: 'same-origin'});
            if (!r.ok) {
                if (r.status === 401) {
                    showStep('step-hinweis');
                    zeigeMeldung('Die Sitzung ist abgelaufen. Bitte geben Sie Ihr Geburtsdatum erneut ein.');
                    return;
                }
                throw new Error(`Seite ${i} konnte nicht geladen werden (${r.status})`);
            }
            const blob = await r.blob();
            img.src = URL.createObjectURL(blob);
        } catch (err) {
            zeigeMeldung(`Fehler beim Laden der Seiten: ${err.message}`);
            return;
        }
    }
}

function updateSeitenProgress(gesamt) {
    document.getElementById('seiten-geladen').textContent = gesehenSeiten.size;
    if (gesehenSeiten.size === gesamt) {
        document.getElementById('btn-gelesen').disabled = false;
    }
}

async function markGelesen() {
    try {
        await api('POST', '/gelesen', {bestaetigt: true});
        loescheMeldung();
        await loadTexte();
    } catch (err) {
        if (!err.sitzung) zeigeMeldung(`Fehler: ${err.message}`);
    }
}

function personenBeschriftung() {
    return personenGesamt > 1 ? `Unterschrift Person ${currentPerson} von ${personenGesamt}` : 'Unterschrift';
}

async function loadTexte() {
    showStep('step-unterschrift');
    const status = await api('GET', '/status');
    personenGesamt = status.personen || 1;
    currentPerson = status.naechste_person || currentPerson;
    zeichenfeld.setzeBeschriftung(personenBeschriftung());

    const texte = await api('GET', '/texte');
    textHash = texte.hash;

    const datenschutz = document.getElementById('datenschutz-text');
    const dsText = `Datenschutzinformation\n\n${texte.texte.datenschutz || '[PLATZHALTER]'}\n\n---\n\n${texte.texte.unterschriftssatz || '[PLATZHALTER]'}`;
    datenschutz.textContent = texte.freigegeben ? dsText : `[ENTWURF - Texte noch nicht freigegeben]\n\n${dsText}`;

    document.getElementById('haken-text').textContent = texte.texte.haken || 'Ich habe die Datenschutzinformation gelesen';
}

async function sendUnterschrift() {
    if (!document.getElementById('check-datenschutz').checked) {
        showError('unterschrift-error', 'Bitte bestätigen Sie, dass Sie die Datenschutzinformation gelesen haben');
        return;
    }

    if (!zeichenfeld.hatInhalt()) {
        showError('unterschrift-error', handoff ? 'Bitte am Handy unterschreiben oder den QR-Code abbrechen und hier zeichnen' : 'Bitte unterschreiben Sie im Feld');
        return;
    }

    const knopf = document.getElementById('btn-unterschreiben');
    knopf.disabled = true;
    try {
        const result = await api('POST', '/unterschrift', {
            person: currentPerson,
            bild: zeichenfeld.png(),
            ich_unterschreibe: true,
            haken: true,
            text_hash: textHash
        });

        if (result.fertig) {
            showStep('step-fertig');
        } else if (result.naechste_person) {
            const bisher = currentPerson;
            currentPerson = result.naechste_person;
            document.getElementById('check-datenschutz').checked = false;
            zeichenfeld.leeren();
            await loadTexte();
            const info = document.getElementById('person-info');
            info.textContent = `Person ${bisher} hat unterschrieben. Jetzt ist Person ${currentPerson} an der Reihe.`;
            info.classList.remove('hidden');
        }
    } catch (err) {
        if (!err.sitzung) showError('unterschrift-error', err.message);
    } finally {
        knopf.disabled = false;
    }
}

// ----- Handy-Übergabe: QR-Code am Computer, Unterschrift auf dem Handy, Bild kommt zurück ins Zeichenfeld -----

function handyAnzeigen(an) {
    document.getElementById('handy-box').classList.toggle('hidden', !an);
    document.getElementById('zeichenfeld').classList.toggle('hidden', an);
}

function qrBild(svg) {
    const img = document.getElementById('handy-qr');
    if (typeof svg === 'string' && svg.trim().startsWith('<svg')) {
        img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);       // als Bild, nie als Markup eingefügt
        img.classList.remove('hidden');
    } else {
        img.removeAttribute('src');
        img.classList.add('hidden');
    }
}

function handyStatus(text) {
    document.getElementById('handy-status').textContent = text;
}

function restZeit(sekunden) {
    const s = Math.max(0, Math.round(sekunden));
    return `Noch ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} Minuten gültig.`;
}

function handyBeenden() {
    if (handoff && handoff.timer) clearTimeout(handoff.timer);
    handoff = null;
    handyAnzeigen(false);
}

async function handyStarten() {
    loescheMeldung();
    hideError('unterschrift-error');
    if (handoff && handoff.timer) clearTimeout(handoff.timer);
    handoff = null;
    try {
        const r = await api('POST', '/handoff', {person: currentPerson});
        handoff = {id: r.handoff_id, timer: null};
        handyAnzeigen(true);
        qrBild(r.qr_svg);
        document.getElementById('btn-handy-neu').classList.add('hidden');
        handyStatus('Warten auf das Handy: Bitte den QR-Code mit der Handy-Kamera scannen und dort unterschreiben.');
        document.getElementById('handy-rest').textContent = restZeit(r.sekunden);
        handoff.timer = setTimeout(handyAbfragen, HANDY_ABFRAGE_MS);
    } catch (err) {
        if (!err.sitzung) zeigeMeldung(err.message);
    }
}

async function handyAbfragen() {
    const aktuell = handoff;
    if (!aktuell) return;
    try {
        const r = await api('GET', `/handoff/${aktuell.id}`);
        if (handoff !== aktuell) return;
        document.getElementById('handy-rest').textContent = restZeit(r.sekunden_rest);
        if (r.status === 'gezeichnet') {
            handyStatus('Unterschrift vom Handy erhalten ...');
            await handyAbholen(aktuell);
            return;
        }
        if (r.status === 'abgelaufen') {
            handyStatus('Der QR-Code ist abgelaufen.');
            qrBild(null);
            document.getElementById('handy-rest').textContent = '';
            document.getElementById('btn-handy-neu').classList.remove('hidden');
            handoff = null;
            return;
        }
        handyStatus(r.status === 'verbunden'
            ? 'Handy verbunden. Bitte dort unterschreiben.'
            : 'Warten auf das Handy: Bitte den QR-Code mit der Handy-Kamera scannen und dort unterschreiben.');
        aktuell.timer = setTimeout(handyAbfragen, HANDY_ABFRAGE_MS);
    } catch (err) {
        if (handoff !== aktuell) return;
        if (err.sitzung) return;
        if (err.status === 404 || err.status === 410) {            // der Code ist am Server nicht mehr bekannt: wie abgelaufen behandeln
            handyStatus('Der QR-Code gilt nicht mehr.');
            qrBild(null);
            document.getElementById('handy-rest').textContent = '';
            document.getElementById('btn-handy-neu').classList.remove('hidden');
            handoff = null;
            return;
        }
        handyStatus(`Fehler bei der Abfrage: ${err.message}`);
        aktuell.timer = setTimeout(handyAbfragen, HANDY_ABFRAGE_MS);
    }
}

async function handyAbholen(aktuell) {
    try {
        const r = await api('POST', `/handoff/${aktuell.id}/abholen`);
        await zeichenfeld.setzeBild(r.bild);
        handyBeenden();
        zeigeMeldung('Die Unterschrift vom Handy wurde übernommen. Mit „löschen“ können Sie sie verwerfen und neu zeichnen.', 'info');
    } catch (err) {
        handoff = null;
        document.getElementById('btn-handy-neu').classList.remove('hidden');
        if (!err.sitzung) handyStatus(`Die Unterschrift konnte nicht übernommen werden: ${err.message}`);
    }
}

async function handyAbbrechen() {
    const aktuell = handoff;
    handyBeenden();
    if (aktuell) {
        try {
            await api('DELETE', `/handoff/${aktuell.id}`);
        } catch (err) {
            // der Abbruch am Server ist nur Aufräumen; der Code verfällt ohnehin
        }
    }
}

function showStep(id) {
    document.querySelectorAll('.step').forEach(s => s.classList.add('hidden'));
    document.getElementById(id).classList.remove('hidden');
}

function showError(id, msg) {
    const el = document.getElementById(id);
    el.textContent = msg;
    el.classList.remove('hidden');
}

function hideError(id) {
    document.getElementById(id).classList.add('hidden');
}
