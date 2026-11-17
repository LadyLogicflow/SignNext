// SignNext Einstellungen - CSP 'self' konform
'use strict';

document.addEventListener('DOMContentLoaded', async () => {
    if (!(await Berater.bereit())) return;
    await loadEinstellungen();
    await demoTestmail();

    document.getElementById('form-einstellungen').addEventListener('submit', saveEinstellungen);
    document.getElementById('verantwortlicher').addEventListener('input', validateVerantwortlicher);
    document.getElementById('datenschutz-link').addEventListener('input', validateLink);
    document.getElementById('aufbewahrung-tage').addEventListener('input', validateTage);
});

const ALLGEMEINER_FEHLER = 'Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.';
const el = (id) => document.getElementById(id);

async function api(method, path, body = null) {
    const opts = {
        method,
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}
    };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch('/api/v1' + path, opts);
    if (!res.ok) {
        let err = {};
        try { err = await res.json(); } catch (e) { err = {}; }
        throw new Error(err.meldung || ALLGEMEINER_FEHLER);
    }
    return res.json();
}

function meldung(text) {
    const box = el('seite-meldung');
    box.textContent = text || '';
    box.classList.toggle('hidden', !text);
}

async function loadEinstellungen() {
    try {
        const data = await api('GET', '/einstellungen');

        el('verantwortlicher').value = data.verantwortlicher || '';
        el('datenschutz-link').value = data.datenschutz_link || '';
        el('aufbewahrung-tage').value = data.aufbewahrung_tage || 90;
        el('mitglied-download-link').checked = data.mitglied_download_link || false;

        el('loading').classList.add('hidden');
        el('einstellungen').classList.remove('hidden');
    } catch (err) {
        el('loading').classList.add('hidden');
        el('seite-fehler').textContent = 'Die Einstellungen konnten nicht geladen werden. ' + err.message;
        el('seite-fehler').classList.remove('hidden');
    }
}

function validateVerantwortlicher() {
    const value = el('verantwortlicher').value.trim();
    if (value.length < 5) return fehler('verantwortlicher-error', 'Bitte tragen Sie Name, Anschrift und Kontakt Ihrer Beratungsstelle ein (mindestens 5 Zeichen).');
    if (value.length > 500) return fehler('verantwortlicher-error', 'Bitte kürzen Sie den Text auf höchstens 500 Zeichen.');
    return ok('verantwortlicher-error');
}

function validateLink() {
    const value = el('datenschutz-link').value.trim();
    if (!value) return fehler('link-error', 'Bitte tragen Sie den Link zu Ihrer Datenschutzerklärung ein.');
    if (!value.startsWith('https://')) return fehler('link-error', 'Die Adresse muss mit https:// beginnen.');
    return ok('link-error');
}

function validateTage() {
    const value = parseInt(el('aufbewahrung-tage').value, 10);
    if (isNaN(value)) return fehler('tage-error', 'Bitte geben Sie eine Zahl ein.');
    if (value < 30) return fehler('tage-error', 'Die Dokumente müssen mindestens 30 Tage gespeichert bleiben.');
    if (value > 365) return fehler('tage-error', 'Die Dokumente dürfen höchstens 365 Tage gespeichert bleiben.');
    return ok('tage-error');
}

function fehler(id, text) {
    el(id).textContent = text;
    el(id).classList.remove('hidden');
    return false;
}

function ok(id) {
    el(id).classList.add('hidden');
    return true;
}

async function saveEinstellungen(e) {
    e.preventDefault();
    meldung('');
    el('save-error').classList.add('hidden');

    const alleOk = [validateVerantwortlicher(), validateLink(), validateTage()].every(Boolean);
    if (!alleOk) {
        fehler('save-error', 'Bitte korrigieren Sie die markierten Angaben.');
        return;
    }

    const verantwortlicher = el('verantwortlicher').value.trim();
    const datenschutz_link = el('datenschutz-link').value.trim();
    const aufbewahrung_tage = parseInt(el('aufbewahrung-tage').value, 10);
    const mitglied_download_link = el('mitglied-download-link').checked;

    try {
        await api('PUT', '/einstellungen', {verantwortlicher, datenschutz_link, aufbewahrung_tage, mitglied_download_link});
        await loadEinstellungen();
        meldung('Die Einstellungen wurden gespeichert.');
    } catch (err) {
        fehler('save-error', err.message);
    }
}

// Demo: Testmail nur an eine der erlaubten Adressen (GET /demo liefert die Liste, POST /demo/testmail sendet)
const VERSAND_TEXT = {
    aus: 'Der Versand ist ausgeschaltet.',
    testadressen: 'Der Versand geht nur an die erlaubten Testadressen.',
    an: 'Der Versand ist eingeschaltet.'
};

async function demoTestmail() {
    if (!(await Berater.istDemo())) return;
    let demo;
    try {
        demo = await api('GET', '/demo');
    } catch (err) {
        return;
    }
    const bereich = document.getElementById('demo-testmail');
    bereich.classList.remove('hidden');
    const info = [VERSAND_TEXT[demo.versand] || ''];
    if (!demo.mail_zugang_geprueft) info.push('Der Mail-Zugang wurde noch nicht erfolgreich geprüft.');
    const auswahl = document.getElementById('demo-testadresse');
    (demo.testadressen || []).forEach(adresse => {
        const o = document.createElement('option');
        o.value = adresse;
        o.textContent = adresse;
        auswahl.appendChild(o);
    });
    const knopf = document.getElementById('btn-demo-testmail');
    knopf.disabled = !(demo.testadressen || []).length;
    if (knopf.disabled) info.push('Es ist keine Testadresse erlaubt.');
    document.getElementById('demo-versand-info').textContent = info.filter(Boolean).join(' ');
    knopf.addEventListener('click', demoTestmailSenden);
}

async function demoTestmailSenden() {
    const knopf = document.getElementById('btn-demo-testmail');
    const fehler = document.getElementById('demo-testmail-fehler');
    const erfolg = document.getElementById('demo-testmail-erfolg');
    fehler.classList.add('hidden');
    erfolg.classList.add('hidden');
    knopf.disabled = true;
    try {
        const an = document.getElementById('demo-testadresse').value;
        const r = await api('POST', '/demo/testmail', {an});
        erfolg.textContent = r.ergebnis === 'gesendet' ? `Die Testmail wurde an ${an} gesendet.` : `Ergebnis: ${r.ergebnis}`;
        erfolg.classList.remove('hidden');
    } catch (err) {
        fehler.textContent = err.message;
        fehler.classList.remove('hidden');
    } finally {
        knopf.disabled = false;
    }
}
