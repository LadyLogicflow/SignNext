// SignNext Download-Seite - CSP 'self' konform
'use strict';

const API = '/api/v1/download';
let token = null;

document.addEventListener('DOMContentLoaded', async () => {
    token = window.location.hash.substring(1);
    if (!token) {
        showError('Kein gültiger Download-Link');
        return;
    }
    history.replaceState(null, '', window.location.pathname);

    try {
        const result = await api('POST', '', {token});

        document.getElementById('loading').classList.add('hidden');
        document.getElementById('download-ready').classList.remove('hidden');

        const infoEl = document.getElementById('download-info');
        const abrufe = result.abrufe || 0;
        const maxAbrufe = result.max_abrufe || 0;
        infoEl.textContent = `Abruf ${abrufe} von ${maxAbrufe}`;

        document.getElementById('btn-download').addEventListener('click', downloadFile);
    } catch (err) {
        document.getElementById('loading').classList.add('hidden');
        showError(err.message);
    }
});

async function api(method, path, body = null) {
    const opts = {
        method,
        headers: {'Content-Type': 'application/json'}
    };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(API + path, opts);

    if (!res.ok) {
        const data = await res.json();
        throw new Error(data.meldung || data.error || 'Fehler beim Download');
    }

    return res.json();
}

async function downloadFile() {
    try {
        const res = await fetch(API, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({token, download: true})
        });

        if (!res.ok) {
            const data = await res.json();
            throw new Error(data.meldung || data.error || 'Download fehlgeschlagen');
        }

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'dokument.pdf';
        a.click();
        URL.revokeObjectURL(url);

        // Info aktualisieren
        const result = await api('POST', '', {token});
        const infoEl = document.getElementById('download-info');
        const abrufe = result.abrufe || 0;
        const maxAbrufe = result.max_abrufe || 0;
        infoEl.textContent = `Abruf ${abrufe} von ${maxAbrufe}`;
    } catch (err) {
        showError(err.message);
    }
}

function showError(msg) {
    const errorDiv = document.getElementById('error');
    const errorText = document.getElementById('error-text');
    errorText.textContent = msg;
    errorDiv.classList.remove('hidden');
}
