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
    document.getElementById('erinnerungen-aktiv').addEventListener('change', saveErinnerungen);
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
        const {name, geburtsdatum, email, personen} = pruefungData;

        document.getElementById('name-wert').textContent = name.wert || '(fehlt)';
        setHerkunft('name-herkunft', name.herkunft);

        const gebInput = document.getElementById('geburtsdatum');
        if (geburtsdatum.gesetzt) {
            gebInput.value = geburtsdatum.wert || '';
            gebInput.readOnly = true;
            gebInput.style.backgroundColor = '#f9fafb';
            setHerkunft('geb-herkunft', 'gesetzt');
        } else if (geburtsdatum.wert) {
            gebInput.value = geburtsdatum.wert;
            setHerkunft('geb-herkunft', geburtsdatum.herkunft);
        } else {
            setHerkunft('geb-herkunft', geburtsdatum.herkunft);
        }

        // Abgleich-Button wenn bestaetigung_noetig
        if (geburtsdatum.bestaetigung_noetig) {
            const gebGroup = gebInput.closest('.form-group');
            const abgleichBtn = document.createElement('button');
            abgleichBtn.type = 'button';
            abgleichBtn.className = 'btn btn-secondary';
            abgleichBtn.textContent = 'Abgleich bestätigen';
            abgleichBtn.style.marginTop = '0.5rem';
            abgleichBtn.addEventListener('click', async () => {
                try {
                    await api('POST', `/vorgaenge/${vorgangId}/abgleich-bestaetigen`, {bestaetigt: true});
                    alert('Abgleich bestätigt');
                    await loadPruefung();
                } catch (err) {
                    alert('Fehler: ' + err.message);
                }
            });
            gebGroup.appendChild(abgleichBtn);
        }

        if (email.wert) {
            document.getElementById('email').value = email.wert;
        }
        setHerkunft('email-herkunft', email.herkunft);

        if (personen.wert) {
            document.getElementById('personen').value = personen.wert;
        }
        setHerkunft('pers-herkunft', personen.herkunft);

        // Erinnerungen-Status
        document.getElementById('erinnerungen-aktiv').checked = pruefungData.erinnerungen_aktiv || false;

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
        fehlt: '(fehlt)',
        gesetzt: '(gesetzt)'
    };
    const el = document.getElementById(id);
    el.textContent = labels[herkunft] || '';
    el.className = 'herkunft';
    if (herkunft === 'fehlt' || herkunft === 'abweichend') {
        el.classList.add('warning-text');
    } else if (herkunft === 'gesetzt') {
        el.classList.add('success-text');
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

        if (zugang.geprueft_am) {
            status.textContent = `✓ Mail-Zugang erfolgreich geprüft (${zugang.host || 'unbekannt'})`;
            status.className = 'success-text';
        } else if (zugang.konfiguriert) {
            status.textContent = `⚠ Mail-Zugang eingerichtet (${zugang.host || 'unbekannt'}), aber nicht geprüft`;
            status.className = 'warning-text';
        } else {
            status.textContent = '✗ Kein Mail-Zugang eingerichtet';
            status.className = 'error-text';
        }
    } catch (err) {
        const status = document.getElementById('mail-status');
        status.textContent = 'Fehler beim Laden';
        status.className = 'error-text';
    }
}

async function saveErinnerungen() {
    const aktiv = document.getElementById('erinnerungen-aktiv').checked;

    try {
        await api('PUT', `/vorgaenge/${vorgangId}/erinnerungen`, {erinnerungen_aktiv: aktiv});
    } catch (err) {
        showError('erinnerungen-error', err.message);
    }
}

async function versenden() {
    if (!document.getElementById('versand-bestaetigung').checked) {
        showError('versand-error', 'Bitte bestätigen Sie den Versand');
        return;
    }

    if (!pruefungData.versandbereit_pdf || pruefungData.fehlend.length > 0) {
        const fehlt = pruefungData.fehlend.join(', ');
        showError('versand-error', `Vorgang kann nicht versendet werden. Fehlend: ${fehlt}`);
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
