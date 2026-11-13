// SignNext Prüfansicht Beratungsstelle - CSP 'self' konform
'use strict';

const vorgangId = new URLSearchParams(window.location.search).get('id');
let pruefungData = null;

document.addEventListener('DOMContentLoaded', async () => {
    if (!vorgangId) {
        alert('Keine Vorgangs-ID in URL');
        return;
    }

    await loadPruefung();
    await loadMailZugang();

    document.getElementById('form-angaben').addEventListener('submit', saveAngaben);
    document.getElementById('btn-versenden').addEventListener('click', versenden);

    // Live-Validation
    document.getElementById('geburtsdatum').addEventListener('input', validateGeburtsdatum);
    document.getElementById('email').addEventListener('input', validateEmail);
    document.getElementById('personen').addEventListener('change', validatePersonen);
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

async function loadPruefung() {
    try {
        pruefungData = await api('GET', `/vorgaenge/${vorgangId}/pruefung`);

        // PDF laden
        document.getElementById('pdf-viewer').src = `/api/v1/vorgaenge/${vorgangId}/dokument`;

        // Angaben anzeigen
        const {name_a, geburtsdatum, email, personen} = pruefungData;

        document.getElementById('name-wert').textContent = name_a.wert || '(fehlt)';
        setHerkunft('name-herkunft', name_a.herkunft);

        if (geburtsdatum.wert) {
            document.getElementById('geburtsdatum').value = geburtsdatum.wert;
        }
        setHerkunft('geb-herkunft', geburtsdatum.herkunft);

        if (email.wert) {
            document.getElementById('email').value = email.wert;
        }
        setHerkunft('email-herkunft', email.herkunft);

        if (personen.wert) {
            document.getElementById('personen').value = personen.wert;
        }
        setHerkunft('pers-herkunft', personen.herkunft);

        document.getElementById('loading').classList.add('hidden');
        document.getElementById('pruefung').classList.remove('hidden');
    } catch (err) {
        alert('Fehler beim Laden: ' + err.message);
    }
}

function setHerkunft(id, herkunft) {
    const labels = {
        pdf: '(aus PDF)',
        manuell: '(manuell)',
        abweichend: '(abweichend)',
        fehlt: '(fehlt)'
    };
    const el = document.getElementById(id);
    el.textContent = labels[herkunft] || '';
    el.className = 'herkunft';
    if (herkunft === 'fehlt' || herkunft === 'abweichend') {
        el.classList.add('warning-text');
    }
}

function validateGeburtsdatum() {
    const input = document.getElementById('geburtsdatum');
    const error = document.getElementById('geb-error');
    const value = input.value.trim();

    if (!value) {
        hideError('geb-error');
        return true;
    }

    if (!/^\d{2}\.\d{2}\.\d{4}$/.test(value)) {
        showError('geb-error', 'Format: TT.MM.JJJJ');
        return false;
    }

    hideError('geb-error');
    return true;
}

function validateEmail() {
    const input = document.getElementById('email');
    const error = document.getElementById('email-error');
    const value = input.value.trim();

    if (!value) {
        hideError('email-error');
        return true;
    }

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
        showError('email-error', 'Keine gültige E-Mail-Adresse');
        return false;
    }

    hideError('email-error');
    return true;
}

function validatePersonen() {
    const select = document.getElementById('personen');
    const error = document.getElementById('pers-error');

    if (!select.value) {
        showError('pers-error', 'Bitte Personenzahl wählen');
        return false;
    }

    hideError('pers-error');
    return true;
}

async function saveAngaben(e) {
    e.preventDefault();

    if (!validateGeburtsdatum() || !validateEmail() || !validatePersonen()) {
        alert('Bitte Fehler beheben');
        return;
    }

    const geburtsdatum = document.getElementById('geburtsdatum').value.trim() || null;
    const email = document.getElementById('email').value.trim() || null;
    const personen = document.getElementById('personen').value ? parseInt(document.getElementById('personen').value) : null;

    try {
        await api('PUT', `/vorgaenge/${vorgangId}/angaben`, {geburtsdatum, email, personen});
        alert('Angaben gespeichert');
        await loadPruefung();
    } catch (err) {
        alert('Fehler: ' + err.message);
    }
}

async function loadMailZugang() {
    try {
        const zugang = await api('GET', '/mail-zugang');
        const status = document.getElementById('mail-status');

        if (zugang.geprueft) {
            status.innerHTML = '<span class="success-text">✓ Mail-Zugang erfolgreich geprüft</span>';
        } else if (zugang.server) {
            status.innerHTML = '<span class="warning-text">⚠ Mail-Zugang eingerichtet, aber nicht geprüft</span>';
        } else {
            status.innerHTML = '<span class="error-text">✗ Kein Mail-Zugang eingerichtet</span>';
        }
    } catch (err) {
        document.getElementById('mail-status').innerHTML = '<span class="error-text">Fehler beim Laden</span>';
    }
}

async function versenden() {
    if (!document.getElementById('versand-bestaetigung').checked) {
        showError('versand-error', 'Bitte bestätigen Sie den Versand');
        return;
    }

    if (!pruefungData.kann_versenden) {
        showError('versand-error', 'Vorgang kann nicht versendet werden (fehlende Angaben oder Mail-Zugang)');
        return;
    }

    if (!confirm('Link wirklich versenden?')) {
        return;
    }

    try {
        await api('POST', `/vorgaenge/${vorgangId}/versenden`, {bestaetigt: true});
        alert('Link wurde versendet!');
        document.getElementById('versand-hinweise').innerHTML = '<p class="success-text">✓ Link erfolgreich versendet</p>';
        document.getElementById('btn-versenden').disabled = true;
    } catch (err) {
        showError('versand-error', err.message);
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
