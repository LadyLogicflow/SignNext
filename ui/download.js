// SignNext Download-Seite - CSP 'self' konform
'use strict';

const API = '/api/v1/download';
let token = null;

document.addEventListener('DOMContentLoaded', () => {
    token = window.location.hash.substring(1);
    if (!token) {
        document.getElementById('download-form').classList.add('hidden');
        document.getElementById('no-token').classList.remove('hidden');
        return;
    }
    history.replaceState(null, '', window.location.pathname);

    document.getElementById('form-download').addEventListener('submit', downloadFile);
    document.getElementById('code').addEventListener('input', validateCode);
});

function validateCode() {
    const input = document.getElementById('code').value;
    const cleaned = input.replace(/\./g, '');

    if (cleaned.length === 8 && /^\d{8}$/.test(cleaned)) {
        hideError('code-error');
        return true;
    }
    return false;
}

async function downloadFile(e) {
    e.preventDefault();

    const code = document.getElementById('code').value.replace(/\./g, '');
    if (!/^\d{8}$/.test(code)) {
        showError('code-error', 'Bitte im Format TT.MM.JJJJ eingeben (acht Ziffern)');
        return;
    }

    try {
        const res = await fetch(API, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({token, code})
        });

        if (res.status === 401) {
            const data = await res.json();
            if (data.error === 'angaben_stimmen_nicht') {
                const {versuch, von, gesperrt} = data;
                const info = document.getElementById('versuch-info');

                if (gesperrt) {
                    showError('code-error', 'Der Link ist gesperrt. Bitte wenden Sie sich an Ihre Beratungsstelle');
                    document.getElementById('code').disabled = true;
                    document.getElementById('btn-download').disabled = true;
                    info.classList.add('hidden');
                } else {
                    showError('code-error', 'Das Geburtsdatum stimmt nicht');
                    info.textContent = `Versuch ${versuch} von ${von}`;
                    info.classList.remove('hidden');
                }
            } else {
                showError('code-error', data.meldung || 'Zugriff verweigert');
            }
            return;
        }

        if (res.status === 429) {
            showError('code-error', 'Zu viele Versuche, bitte später erneut');
            return;
        }

        if (!res.ok) {
            const data = await res.json();
            showError('code-error', data.meldung || 'Download fehlgeschlagen');
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
            const abrufInfoDiv = document.getElementById('abruf-info');
            infoEl.textContent = `Abruf ${abruf} von ${abrufMax}`;
            abrufInfoDiv.classList.remove('hidden');
        }

        hideError('code-error');
        document.getElementById('versuch-info').classList.add('hidden');
    } catch (err) {
        showError('code-error', err.message);
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
