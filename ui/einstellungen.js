// SignNext Einstellungen - CSP 'self' konform
'use strict';

document.addEventListener('DOMContentLoaded', async () => {
    await loadEinstellungen();

    document.getElementById('form-einstellungen').addEventListener('submit', saveEinstellungen);
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

        document.getElementById('aufbewahrung-tage').value = data.aufbewahrung_tage || 90;
        document.getElementById('mitglied-download-link').checked = data.mitglied_download_link || false;

        document.getElementById('loading').classList.add('hidden');
        document.getElementById('einstellungen').classList.remove('hidden');
    } catch (err) {
        alert('Fehler beim Laden: ' + err.message);
    }
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

    if (!validateTage()) {
        alert('Bitte Fehler beheben');
        return;
    }

    const aufbewahrung_tage = parseInt(document.getElementById('aufbewahrung-tage').value);
    const mitglied_download_link = document.getElementById('mitglied-download-link').checked;

    try {
        await api('PUT', '/einstellungen', {aufbewahrung_tage, mitglied_download_link});
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
