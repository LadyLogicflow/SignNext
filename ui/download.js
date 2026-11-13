// SignNext Download-Seite - CSP 'self' konform
'use strict';

const API = '/api/v1/download';
let token = null;

document.addEventListener('DOMContentLoaded', () => {
    token = window.location.hash.substring(1);
    if (!token) {
        showError('Kein gültiger Download-Link');
        return;
    }
    history.replaceState(null, '', window.location.pathname);

    document.getElementById('loading').classList.add('hidden');
    document.getElementById('download-ready').classList.remove('hidden');
    document.getElementById('btn-download').addEventListener('click', downloadFile);
});

async function downloadFile() {
    try {
        const res = await fetch(API, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({token})
        });

        if (res.status === 404) {
            const data = await res.json();
            showError(data.meldung || data.error || 'Download nicht gefunden');
            return;
        }

        if (res.status === 429) {
            showError('Bitte später erneut');
            return;
        }

        if (!res.ok) {
            const data = await res.json();
            showError(data.meldung || data.error || 'Download fehlgeschlagen');
            return;
        }

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'dokument.pdf';
        a.click();
        URL.revokeObjectURL(url);

        // Abrufzähler aus Response-Headern
        const abruf = res.headers.get('X-Abruf');
        const abrufMax = res.headers.get('X-Abruf-Max');
        if (abruf && abrufMax) {
            const infoEl = document.getElementById('download-info');
            infoEl.textContent = `Abruf ${abruf} von ${abrufMax}`;
        }
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
