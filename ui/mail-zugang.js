// SignNext Mail-Zugang der Beratungsstelle - CSP 'self' konform. Alle Rückmeldungen stehen im Text der Seite (keine Browser-Meldungen), Löschen erst nach Rückfrage.
'use strict';

const ALLGEMEINER_FEHLER = 'Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.';
let hasExisting = false;
const el = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', async () => {
    if (!(await Berater.bereit())) return;
    await loadMailZugang();

    el('form-mail').addEventListener('submit', saveMailZugang);
    el('btn-test').addEventListener('click', testMailZugang);
    el('btn-delete').addEventListener('click', () => el('loeschen-rueckfrage').classList.remove('hidden'));
    el('btn-loeschen-nein').addEventListener('click', () => el('loeschen-rueckfrage').classList.add('hidden'));
    el('btn-loeschen-ja').addEventListener('click', deleteMailZugang);

    // Live-Prüfung beim Tippen
    el('host').addEventListener('input', validateHost);
    el('port').addEventListener('change', validatePort);
    el('benutzer').addEventListener('input', validateBenutzer);
    el('absender-name').addEventListener('input', validateAbsenderName);
    el('absender-adresse').addEventListener('input', validateAbsenderAdresse);
});

async function api(method, path, body = null) {
    const opts = {method, credentials: 'same-origin', headers: {'Content-Type': 'application/json'}};
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch('/api/v1' + path, opts);
    if (!res.ok) {
        let daten = {};
        try { daten = await res.json(); } catch (err) { daten = {}; }
        throw Object.assign(new Error(daten.meldung || ALLGEMEINER_FEHLER), {daten, status: res.status});
    }
    if (res.status === 204) return null;
    return res.json();
}

function meldung(text) {
    const box = el('seite-meldung');
    box.textContent = text || '';
    box.classList.toggle('hidden', !text);
}

async function loadMailZugang() {
    try {
        const data = await api('GET', '/mail-zugang');
        zeigeHinweise(data && data.hinweise);
        if (data && data.konfiguriert) {
            hasExisting = true;
            el('host').value = data.host || '';
            el('port').value = data.port || '';
            el('benutzer').value = data.benutzer || '';
            el('absender-name').value = data.absender_name || '';
            el('absender-adresse').value = data.absender_adresse || '';
            el('signatur').value = data.signatur || '';
            el('bcc-an-absender').checked = data.bcc_an_absender || false;
        } else {
            hasExisting = false;
        }
        el('passwort-hinweis').textContent = hasExisting
            ? 'Bleibt das Feld leer, wird das bisherige Passwort beibehalten.'
            : 'Beim ersten Einrichten bitte eintragen.';
        el('loading').classList.add('hidden');
        el('mail-zugang').classList.remove('hidden');
    } catch (err) {
        el('loading').classList.add('hidden');
        el('seite-fehler').textContent = 'Der Mail-Zugang konnte nicht geladen werden. ' + err.message;
        el('seite-fehler').classList.remove('hidden');
    }
}

function validateHost() {
    const value = el('host').value.trim();
    if (!value) return fehler('host-error', 'Bitte tragen Sie den Server Ihres E-Mail-Anbieters ein.');
    if (!/^[a-zA-Z0-9.-]+$/.test(value)) return fehler('host-error', 'Der Servername darf nur Buchstaben, Ziffern, Punkt und Bindestrich enthalten, zum Beispiel smtp.beispiel.de.');
    return ok('host-error');
}

function validatePort() {
    const value = el('port').value;
    if (!value) return fehler('port-error', 'Bitte wählen Sie einen Port.');
    if (value !== '465' && value !== '587') return fehler('port-error', 'Es sind nur die Ports 465 und 587 möglich.');
    return ok('port-error');
}

function validateBenutzer() {
    return el('benutzer').value.trim() ? ok('benutzer-error') : fehler('benutzer-error', 'Bitte tragen Sie den Benutzernamen ein.');
}

function validateAbsenderName() {
    return el('absender-name').value.trim() ? ok('absender-name-error') : fehler('absender-name-error', 'Bitte tragen Sie den Namen ein, der beim Mitglied als Absender erscheint.');
}

function validateAbsenderAdresse() {
    const value = el('absender-adresse').value.trim();
    if (!value) return fehler('absender-adresse-error', 'Bitte tragen Sie die Absenderadresse ein.');
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return fehler('absender-adresse-error', 'Das ist keine gültige E-Mail-Adresse.');
    return ok('absender-adresse-error');
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

const FELD_FEHLER = {
    host: 'host-error', port: 'port-error', benutzer: 'benutzer-error', passwort: 'passwort-error',
    absender_name: 'absender-name-error', absender_adresse: 'absender-adresse-error', signatur: 'signatur-error'
};

async function saveMailZugang(e) {
    e.preventDefault();
    meldung('');
    el('save-error').classList.add('hidden');
    const alleOk = [validateHost(), validatePort(), validateBenutzer(), validateAbsenderName(), validateAbsenderAdresse()].every(Boolean);
    const passwort = el('passwort').value;
    let passwortOk = true;
    if (!hasExisting && !passwort) passwortOk = fehler('passwort-error', 'Beim ersten Einrichten ist das Passwort nötig.');
    else ok('passwort-error');
    if (!alleOk || !passwortOk) {
        fehler('save-error', 'Bitte korrigieren Sie die markierten Angaben.');
        return;
    }
    const data = {
        host: el('host').value.trim(),
        port: parseInt(el('port').value, 10),
        benutzer: el('benutzer').value.trim(),
        absender_name: el('absender-name').value.trim(),
        absender_adresse: el('absender-adresse').value.trim(),
        signatur: el('signatur').value.trim() || null,
        bcc_an_absender: el('bcc-an-absender').checked
    };
    if (passwort) data.passwort = passwort;
    try {
        await api('PUT', '/mail-zugang', data);
        el('passwort').value = '';
        el('test-result').classList.add('hidden');
        await loadMailZugang();
        meldung('Der Mail-Zugang wurde gespeichert. Senden Sie jetzt eine Testmail, um ihn zu prüfen.');
    } catch (err) {
        // Fehler mit Feldname (z. B. 422 absender_adresse bei abweichender Domain) stehen am Feld, alle anderen im Formularfehler
        const feld = err.daten && FELD_FEHLER[err.daten.feld];
        if (feld) fehler(feld, err.message);
        else fehler('save-error', err.message);
    }
}

// Demo: Hinweise des Servers (z. B. Benutzername ungleich Absenderadresse), nur anzeigen
function zeigeHinweise(hinweise) {
    const box = el('mail-hinweise');
    box.textContent = '';
    if (!Array.isArray(hinweise) || hinweise.length === 0) {
        box.classList.add('hidden');
        return;
    }
    hinweise.forEach(text => {
        const p = document.createElement('p');
        p.textContent = String(text);
        box.appendChild(p);
    });
    box.classList.remove('hidden');
}

function ergebnisZeigen(erfolg, titel, text, technik) {
    const box = el('test-output');
    box.textContent = '';
    const kopf = document.createElement('p');
    kopf.className = erfolg ? 'success-text' : 'error-text';
    const strong = document.createElement('strong');
    strong.textContent = (erfolg ? '✓ ' : '✗ ') + titel;
    kopf.appendChild(strong);
    box.appendChild(kopf);
    if (text) {
        const p = document.createElement('p');
        p.textContent = text;
        box.appendChild(p);
    }
    const zeilen = Object.entries(technik || {}).filter(([, wert]) => wert);
    if (zeilen.length) {
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = 'Technische Angaben';
        details.appendChild(summary);
        const dl = document.createElement('dl');
        zeilen.forEach(([name, wert]) => {
            const dt = document.createElement('dt');
            dt.textContent = name;
            const dd = document.createElement('dd');
            dd.textContent = String(wert);
            dl.append(dt, dd);
        });
        details.appendChild(dl);
        box.appendChild(details);
    }
    el('test-result').classList.remove('hidden');
}

async function testMailZugang() {
    meldung('');
    el('save-error').classList.add('hidden');
    if (!hasExisting) {
        fehler('save-error', 'Bitte speichern Sie zuerst den Mail-Zugang, dann können Sie eine Testmail senden.');
        return;
    }
    const knopf = el('btn-test');
    knopf.disabled = true;
    try {
        const result = await api('POST', '/mail-zugang/pruefen', {});
        const v = result.verbindung || {};
        ergebnisZeigen(true, 'Die Testmail wurde gesendet', `Sie ging an die Absenderadresse ${el('absender-adresse').value.trim()}. Kommt sie dort an, ist alles eingerichtet.`,
            {'Verschlüsselung (TLS)': v.tls_version, 'Verfahren': v.cipher, 'Zertifikat gültig bis': v.zertifikat_gueltig_bis, 'Aussteller': v.aussteller});
    } catch (err) {
        ergebnisZeigen(false, 'Die Testmail konnte nicht gesendet werden', err.message, null);
    } finally {
        knopf.disabled = false;
    }
}

async function deleteMailZugang() {
    el('loeschen-rueckfrage').classList.add('hidden');
    el('save-error').classList.add('hidden');
    if (!hasExisting) {
        fehler('save-error', 'Es ist kein Mail-Zugang eingerichtet, der gelöscht werden könnte.');
        return;
    }
    try {
        await api('DELETE', '/mail-zugang');
        hasExisting = false;
        el('form-mail').reset();
        el('test-result').classList.add('hidden');
        el('passwort-hinweis').textContent = 'Beim ersten Einrichten bitte eintragen.';
        meldung('Der Mail-Zugang wurde gelöscht.');
    } catch (err) {
        fehler('save-error', err.message);
    }
}
