// SignNext Mail-Zugang - CSP 'self' konform
'use strict';

let hasExisting = false;

document.addEventListener('DOMContentLoaded', async () => {
    await loadMailZugang();

    document.getElementById('form-mail').addEventListener('submit', saveMailZugang);
    document.getElementById('btn-test').addEventListener('click', testMailZugang);
    document.getElementById('btn-delete').addEventListener('click', deleteMailZugang);

    // Live-Validierung
    document.getElementById('host').addEventListener('input', validateHost);
    document.getElementById('port').addEventListener('change', validatePort);
    document.getElementById('benutzer').addEventListener('input', validateBenutzer);
    document.getElementById('absender-name').addEventListener('input', validateAbsenderName);
    document.getElementById('absender-adresse').addEventListener('input', validateAbsenderAdresse);
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

    if (res.status === 204) {
        return null;
    }

    return res.json();
}

async function loadMailZugang() {
    try {
        const data = await api('GET', '/mail-zugang');

        if (data && data.konfiguriert) {
            hasExisting = true;
            document.getElementById('host').value = data.host || '';
            document.getElementById('port').value = data.port || '';
            document.getElementById('benutzer').value = data.benutzer || '';
            document.getElementById('absender-name').value = data.absender_name || '';
            document.getElementById('absender-adresse').value = data.absender_adresse || '';
            document.getElementById('signatur').value = data.signatur || '';
            document.getElementById('bcc-an-absender').checked = data.bcc_an_absender || false;
        } else {
            hasExisting = false;
        }

        document.getElementById('loading').classList.add('hidden');
        document.getElementById('mail-zugang').classList.remove('hidden');
    } catch (err) {
        if (err.message.includes('404') || err.message.includes('nicht')) {
            hasExisting = false;
            document.getElementById('loading').classList.add('hidden');
            document.getElementById('mail-zugang').classList.remove('hidden');
        } else {
            alert('Fehler beim Laden: ' + err.message);
        }
    }
}

function validateHost() {
    const input = document.getElementById('host');
    const value = input.value.trim();

    if (!value) {
        showError('host-error', 'Server ist erforderlich');
        return false;
    }

    if (!/^[a-zA-Z0-9.-]+$/.test(value)) {
        showError('host-error', 'Ungültiger Hostname');
        return false;
    }

    hideError('host-error');
    return true;
}

function validatePort() {
    const select = document.getElementById('port');
    const value = select.value;

    if (!value) {
        showError('port-error', 'Bitte Port wählen');
        return false;
    }

    if (value !== '465' && value !== '587') {
        showError('port-error', 'Port muss 465 oder 587 sein');
        return false;
    }

    hideError('port-error');
    return true;
}

function validateBenutzer() {
    const input = document.getElementById('benutzer');
    const value = input.value.trim();

    if (!value) {
        showError('benutzer-error', 'Benutzer ist erforderlich');
        return false;
    }

    hideError('benutzer-error');
    return true;
}

function validateAbsenderName() {
    const input = document.getElementById('absender-name');
    const value = input.value.trim();

    if (!value) {
        showError('absender-name-error', 'Absender-Name ist erforderlich');
        return false;
    }

    hideError('absender-name-error');
    return true;
}

function validateAbsenderAdresse() {
    const input = document.getElementById('absender-adresse');
    const value = input.value.trim();

    if (!value) {
        showError('absender-adresse-error', 'Absender-Adresse ist erforderlich');
        return false;
    }

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
        showError('absender-adresse-error', 'Keine gültige E-Mail-Adresse');
        return false;
    }

    hideError('absender-adresse-error');
    return true;
}

async function saveMailZugang(e) {
    e.preventDefault();

    if (!validateHost() || !validatePort() || !validateBenutzer() || !validateAbsenderName() || !validateAbsenderAdresse()) {
        alert('Bitte Fehler beheben');
        return;
    }

    const passwort = document.getElementById('passwort').value;
    if (!hasExisting && !passwort) {
        showError('passwort-error', 'Passwort ist beim ersten Einrichten erforderlich');
        return;
    }

    const data = {
        host: document.getElementById('host').value.trim(),
        port: parseInt(document.getElementById('port').value),
        benutzer: document.getElementById('benutzer').value.trim(),
        absender_name: document.getElementById('absender-name').value.trim(),
        absender_adresse: document.getElementById('absender-adresse').value.trim(),
        signatur: document.getElementById('signatur').value.trim() || null,
        bcc_an_absender: document.getElementById('bcc-an-absender').checked
    };

    if (passwort) {
        data.passwort = passwort;
    }

    try {
        await api('PUT', '/mail-zugang', data);
        alert('Mail-Zugang gespeichert');
        document.getElementById('passwort').value = '';
        await loadMailZugang();
    } catch (err) {
        showError('save-error', err.message);
    }
}

async function testMailZugang() {
    if (!hasExisting) {
        alert('Bitte zuerst Mail-Zugang speichern');
        return;
    }

    if (!confirm('Testmail an die Absenderadresse senden?')) {
        return;
    }

    try {
        const result = await api('POST', '/mail-zugang/pruefen', {});

        const output = document.getElementById('test-output');
        const testResult = document.getElementById('test-result');

        let html = '<div class="success-text"><strong>✓ Testmail erfolgreich versendet</strong></div>';
        html += '<dl style="margin-top: 1rem;">';

        if (result.verbindung) {
            const v = result.verbindung;
            if (v.tls_version) {
                html += '<dt>TLS-Version:</dt><dd>' + escapeHtml(v.tls_version) + '</dd>';
            }
            if (v.cipher) {
                html += '<dt>Verfahren:</dt><dd>' + escapeHtml(v.cipher) + '</dd>';
            }
            if (v.zertifikat_gueltig_bis) {
                html += '<dt>Zertifikat gültig bis:</dt><dd>' + escapeHtml(v.zertifikat_gueltig_bis) + '</dd>';
            }
            if (v.aussteller) {
                html += '<dt>Aussteller:</dt><dd>' + escapeHtml(v.aussteller) + '</dd>';
            }
        }

        html += '</dl>';

        output.innerHTML = html;
        testResult.classList.remove('hidden');
    } catch (err) {
        const output = document.getElementById('test-output');
        const testResult = document.getElementById('test-result');

        let errorMsg = err.message;

        // Zertifikatsfehler verständlich anzeigen
        if (errorMsg.includes('CERTIFICATE') || errorMsg.includes('certificate')) {
            errorMsg = 'Zertifikatsfehler: ' + errorMsg;
        } else if (errorMsg.includes('hostname')) {
            errorMsg = 'Der Hostname stimmt nicht mit dem Zertifikat überein';
        } else if (errorMsg.includes('timeout') || errorMsg.includes('Timeout')) {
            errorMsg = 'Verbindung zum Server nicht möglich (Timeout)';
        } else if (errorMsg.includes('refused')) {
            errorMsg = 'Verbindung wurde vom Server abgelehnt';
        }

        output.innerHTML = '<div class="error-text"><strong>✗ Testmail fehlgeschlagen</strong><br>' + escapeHtml(errorMsg) + '</div>';
        testResult.classList.remove('hidden');
    }
}

async function deleteMailZugang() {
    if (!hasExisting) {
        alert('Kein Mail-Zugang vorhanden');
        return;
    }

    if (!confirm('Mail-Zugang wirklich löschen?')) {
        return;
    }

    try {
        await api('DELETE', '/mail-zugang');
        alert('Mail-Zugang gelöscht');
        hasExisting = false;
        document.getElementById('form-mail').reset();
        document.getElementById('test-result').classList.add('hidden');
    } catch (err) {
        alert('Fehler: ' + err.message);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showError(id, msg) {
    const el = document.getElementById(id);
    el.textContent = msg;
    el.classList.remove('hidden');
}

function hideError(id) {
    document.getElementById(id).classList.add('hidden');
}
