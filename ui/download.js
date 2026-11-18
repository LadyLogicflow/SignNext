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
    document.getElementById('dokument-wahl').classList.add('hidden');
    await abrufen(code, null, null);
}

// Hat der Vorgang mehrere Dokumente, antwortet der Server nach dem richtigen Code mit der Liste; der Code bleibt dafür im Speicher dieser Seite
function dokumentWahl(code, dokumente) {
    const knoepfe = document.getElementById('dokument-wahl-knoepfe');
    knoepfe.textContent = '';
    dokumente.forEach(d => {
        const knopf = document.createElement('button');
        knopf.type = 'button';
        knopf.className = 'btn btn-primary';
        knopf.textContent = `${d.titel} herunterladen`;
        knopf.addEventListener('click', () => abrufen(code, d.rolle, d.titel));
        knoepfe.appendChild(knopf);
    });
    document.getElementById('dokument-wahl').classList.remove('hidden');
}

// Dateinamen bleiben wie bisher (die Titel auf der Seite kommen vom Server)
const DATEINAME_ROLLE = {B: 'Anschreiben', A: 'Protokoll'};

async function abrufen(code, rolle, titel) {
    try {
        const body = {token, code};
        if (rolle) body.dokument = rolle;
        const res = await fetch(API, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
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

        if ((res.headers.get('Content-Type') || '').includes('application/json')) {
            const liste = await res.json();
            hideError('code-error');
            document.getElementById('versuch-info').classList.add('hidden');
            dokumentWahl(code, Array.isArray(liste.dokumente) ? liste.dokumente : []);
            return;
        }

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = DATEINAME_ROLLE[rolle] ? `${DATEINAME_ROLLE[rolle]}.pdf` : (titel ? `${String(titel).replace(/[^A-Za-z0-9_-]+/g, '_')}.pdf` : 'dokument.pdf');
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
