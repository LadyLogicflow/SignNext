// SignNext Vorgangsliste - CSP 'self' konform
'use strict';

document.addEventListener('DOMContentLoaded', async () => {
    if (!(await Berater.bereit())) return;
    await demoBereich();
    await loadVorgaenge();
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

async function loadVorgaenge() {
    try {
        const data = await api('GET', '/vorgaenge');
        const liste = document.getElementById('vorgaenge-liste');
        liste.textContent = '';

        if (!data.vorgaenge || data.vorgaenge.length === 0) {
            liste.textContent = 'Keine Vorgänge vorhanden';
        } else {
            data.vorgaenge.forEach(vorgang => {
                const card = document.createElement('div');
                card.className = 'card';

                const statusText = getStatusText(vorgang.status);
                const statusClass = vorgang.status === 'abgeschlossen' ? 'success-text' : '';

                let html = `<h3>Vorgang ${vorgang.id}</h3>`;
                html += `<p class="${statusClass}">Status: ${statusText}</p>`;
                html += `<p>Erstellt: ${new Date(vorgang.erstellt).toLocaleDateString('de-DE')}</p>`;

                if (vorgang.status === 'abgeschlossen') {
                    html += '<div style="margin-top: 1rem;">';
                    html += `<button class="btn btn-secondary" data-id="${vorgang.id}" data-type="dokument" style="margin-right: 0.5rem;">Dokument herunterladen</button>`;
                    html += `<button class="btn btn-secondary" data-id="${vorgang.id}" data-type="nachweis">Nachweis herunterladen</button>`;
                    html += '</div>';
                }

                const temp = document.createElement('div');
                temp.innerHTML = html;

                while (temp.firstChild) {
                    card.appendChild(temp.firstChild);
                }

                const buttons = card.querySelectorAll('button');
                buttons.forEach(btn => {
                    btn.addEventListener('click', () => downloadVorgang(btn.dataset.id, btn.dataset.type));
                });

                liste.appendChild(card);
            });
        }

        document.getElementById('loading').classList.add('hidden');
        document.getElementById('vorgaenge').classList.remove('hidden');
    } catch (err) {
        alert('Fehler beim Laden: ' + err.message);
    }
}

function getStatusText(status) {
    const statusMap = {
        'bestaetigung_noetig': 'Bestätigung nötig',
        'bereit': 'Bereit',
        'versenden': 'Wird versendet',
        'versendet': 'Versendet',
        'versand_unklar': 'Versand unklar',
        'unterschrieben': 'Unterschrieben',
        'abschluss_laeuft': 'Abschluss läuft',
        'abgeschlossen': 'Abgeschlossen',
        'abschluss_fehlgeschlagen': 'Abschluss fehlgeschlagen',
        'geloescht': 'Gelöscht'
    };
    return statusMap[status] || status;
}

async function downloadVorgang(id, type) {
    try {
        const res = await fetch(`/api/v1/vorgaenge/${id}/download/${type}`, {
            credentials: 'same-origin'
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.meldung || err.error || 'Download fehlgeschlagen');
        }

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${type}_${id}.pdf`;
        a.click();
        URL.revokeObjectURL(url);
    } catch (err) {
        alert('Fehler: ' + err.message);
    }
}

// Demo: Beispiel-PDFs (feste Links in der Seite) und Demo-Vorgang mit Link, Geburtsdatum und Code
async function demoBereich() {
    if (!(await Berater.istDemo())) return;
    document.getElementById('demo-bereich').classList.remove('hidden');
    document.getElementById('btn-demo-vorgang').addEventListener('click', demoVorgangAnlegen);
}

async function demoVorgangAnlegen() {
    const knopf = document.getElementById('btn-demo-vorgang');
    const fehler = document.getElementById('demo-fehler');
    fehler.classList.add('hidden');
    knopf.disabled = true;
    try {
        const r = await api('POST', '/demo/vorgang', {});
        Berater.linkBoxZeigen(document.getElementById('demo-ergebnis'), {
            titel: `Demo-Vorgang ${r.vorgang_id} angelegt`,
            link: r.link,
            hinweis: `Zum Öffnen des Links wird das Geburtsdatum ${r.geburtsdatum} verlangt (Eingabe: ${r.code}).`
        });
        await loadVorgaenge();
    } catch (err) {
        fehler.textContent = err.message;
        fehler.classList.remove('hidden');
    } finally {
        knopf.disabled = false;
    }
}
