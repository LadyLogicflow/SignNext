// SignNext Einstellungen - CSP 'self' konform
'use strict';

document.addEventListener('DOMContentLoaded', async () => {
    await loadEinstellungen();

    document.getElementById('form-einstellungen').addEventListener('submit', saveEinstellungen);
    document.getElementById('verantwortlicher').addEventListener('input', validateVerantwortlicher);
    document.getElementById('datenschutz-link').addEventListener('input', validateLink);
    document.getElementById('aufbewahrung-tage').addEventListener('input', validateTage);
});

async function api(method, path, body = null) {
    const opts = {
        method,
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}
    };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch('/api/v1' + path, opts);
    if (!res.ok) {
        const err = await res.json();
        throw new Error(err.meldung || err.error || 'Fehler');
    }
    return res.json();
}

async function loadEinstellungen() {
    try {
        const data = await api('GET', '/einstellungen');

        document.getElementById('verantwortlicher').value = data.verantwortlicher || '';
        document.getElementById('datenschutz-link').value = data.datenschutz_link || '';
        document.getElementById('aufbewahrung-tage').value = data.aufbewahrung_tage || 90;
        document.getElementById('mitglied-download-link').checked = data.mitglied_download_link || false;

        document.getElementById('loading').classList.add('hidden');
        document.getElementById('einstellungen').classList.remove('hidden');
    } catch (err) {
        alert('Fehler beim Laden: ' + err.message);
    }
}

function validateVerantwortlicher() {
    const input = document.getElementById('verantwortlicher');
    const value = input.value.trim();

    if (value.length < 5) {
        showError('verantwortlicher-error', 'Mindestens 5 Zeichen');
        return false;
    }

    if (value.length > 500) {
        showError('verantwortlicher-error', 'Maximal 500 Zeichen');
        return false;
    }

    hideError('verantwortlicher-error');
    return true;
}

function validateLink() {
    const input = document.getElementById('datenschutz-link');
    const value = input.value.trim();

    if (value && !value.startsWith('https://')) {
        showError('link-error', 'Muss mit https:// beginnen');
        return false;
    }

    hideError('link-error');
    return true;
}

function validateTage() {
    const input = document.getElementById('aufbewahrung-tage');
    const error = document.getElementById('tage-error');
    const value = parseInt(input.value);

    if (isNaN(value)) {
        showError('tage-error', 'Bitte eine Zahl eingeben');
        return false;
    }

    if (value < 30) {
        showError('tage-error', 'Mindestens 30 Tage');
        return false;
    }

    if (value > 365) {
        showError('tage-error', 'Maximal 365 Tage');
        return false;
    }

    hideError('tage-error');
    return true;
}

async function saveEinstellungen(e) {
    e.preventDefault();

    if (!validateVerantwortlicher() || !validateLink() || !validateTage()) {
        alert('Bitte Fehler beheben');
        return;
    }

    const verantwortlicher = document.getElementById('verantwortlicher').value.trim();
    const datenschutz_link = document.getElementById('datenschutz-link').value.trim();
    const aufbewahrung_tage = parseInt(document.getElementById('aufbewahrung-tage').value);
    const mitglied_download_link = document.getElementById('mitglied-download-link').checked;

    try {
        await api('PUT', '/einstellungen', {verantwortlicher, datenschutz_link, aufbewahrung_tage, mitglied_download_link});
        alert('Einstellungen gespeichert');
        await loadEinstellungen();
    } catch (err) {
        showError('save-error', err.message);
    }
}

function showError(id, msg) {
    const el = document.getElementById(id);
    el.textContent = msg;
    el.classList.remove('hidden');
}

function hideError(id) {
    document.getElementById(id).classList.add('hidden');
}
