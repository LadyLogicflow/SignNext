// SignNext Unterschriftsseite - CSP 'self' konform
'use strict';

const API = '/api/v1/zugang';
let token = null;
let textHash = null;
let currentPerson = 1;
let gesehenSeiten = new Set();
let canvasDrawing = false;
let canvasHasContent = false;

// Init
document.addEventListener('DOMContentLoaded', async () => {
    token = window.location.hash.substring(1);
    if (!token) {
        showError('code-error', 'Kein gültiger Zugangslink');
        return;
    }
    history.replaceState(null, '', window.location.pathname);

    try {
        const hinweis = await api('GET', '/hinweis');
        const hinweisEl = document.getElementById('hinweis-text');
        hinweisEl.textContent = `${hinweis.kennung || ''}\n\n${hinweis.zugangshinweis || 'Bitte geben Sie Ihr Geburtsdatum ein.'}`;
        if (!hinweis.freigegeben) {
            hinweisEl.textContent += '\n\n[ENTWURF - Texte noch nicht freigegeben]';
        }
    } catch (err) {
        console.error(err);
    }

    document.getElementById('form-code').addEventListener('submit', checkCode);
    document.getElementById('btn-gelesen').addEventListener('click', markGelesen);
    document.getElementById('btn-unterschreiben').addEventListener('click', sendUnterschrift);
    setupCanvas();
});

async function api(method, path, body = null) {
    const opts = {
        method,
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'Origin': window.location.origin}
    };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(API + path, opts);
    const data = await res.json();

    if (!res.ok) {
        if (res.status === 401 && data.error === 'sitzung_ungueltig') {
            alert('Ihre Sitzung ist abgelaufen. Bitte laden Sie die Seite neu.');
            location.reload();
            throw new Error('Sitzung abgelaufen');
        }
        if (data.error === 'angaben_stimmen_nicht') {
            throw {code: 'wrong_code', data};
        }
        throw new Error(data.meldung || data.error || 'Fehler');
    }
    return data;
}

async function checkCode(e) {
    e.preventDefault();
    const code = document.getElementById('code').value.replace(/\./g, '');
    if (!/^\d{6}$/.test(code)) {
        showError('code-error', 'Bitte im Format TT.MM.JJ eingeben');
        return;
    }

    try {
        await api('POST', '/pruefen', {token, code});
        hideError('code-error');
        document.getElementById('versuch-info').classList.add('hidden');
        await loadSeiten();
    } catch (err) {
        if (err.code === 'wrong_code') {
            const {versuch, von, gesperrt} = err.data;
            const info = document.getElementById('versuch-info');
            if (gesperrt) {
                showError('code-error', 'Der Link wurde nach zu vielen Fehlversuchen gesperrt');
                info.classList.add('hidden');
            } else {
                showError('code-error', 'Das Geburtsdatum stimmt nicht');
                info.textContent = `Versuch ${versuch} von ${von}`;
                info.classList.remove('hidden');
            }
        } else {
            showError('code-error', err.message);
        }
    }
}

async function loadSeiten() {
    showStep('step-seiten');
    const status = await api('GET', '/status');
    const {seiten, geliefert} = status;

    document.getElementById('seiten-gesamt').textContent = seiten;
    document.getElementById('seiten-fortschritt').classList.remove('hidden');

    const container = document.getElementById('seiten-container');
    for (let i = 1; i <= seiten; i++) {
        const img = document.createElement('img');
        img.alt = `Seite ${i}`;
        img.addEventListener('load', () => {
            gesehenSeiten.add(i);
            updateSeitenProgress(seiten);
        });
        container.appendChild(img);

        try {
            const r = await fetch(`${API}/seite/${i}`, {credentials: 'same-origin'});
            if (!r.ok) {
                if (r.status === 401) {
                    alert('Sitzung abgelaufen. Bitte geben Sie Ihr Geburtsdatum erneut ein.');
                    showStep('step-hinweis');
                    return;
                }
                throw new Error(`Seite ${i} konnte nicht geladen werden (${r.status})`);
            }
            const blob = await r.blob();
            img.src = URL.createObjectURL(blob);
        } catch (err) {
            alert(`Fehler beim Laden der Seiten: ${err.message}`);
            return;
        }
    }
}

function updateSeitenProgress(gesamt) {
    document.getElementById('seiten-geladen').textContent = gesehenSeiten.size;
    if (gesehenSeiten.size === gesamt) {
        document.getElementById('btn-gelesen').disabled = false;
    }
}

async function markGelesen() {
    try {
        await api('POST', '/gelesen', {bestaetigt: true});
        await loadTexte();
    } catch (err) {
        alert('Fehler: ' + err.message);
    }
}

async function loadTexte() {
    showStep('step-unterschrift');
    const texte = await api('GET', '/texte');
    textHash = texte.hash;

    const datenschutz = document.getElementById('datenschutz-text');
    const dsText = `Datenschutzinformation\n\n${texte.texte.datenschutz || '[PLATZHALTER]'}\n\n---\n\n${texte.texte.unterschriftssatz || '[PLATZHALTER]'}`;
    datenschutz.textContent = texte.freigegeben ? dsText : `[ENTWURF - Texte noch nicht freigegeben]\n\n${dsText}`;

    document.getElementById('haken-text').textContent = texte.texte.haken || 'Ich habe die Datenschutzinformation gelesen';
}

function setupCanvas() {
    const canvas = document.getElementById('unterschrift-canvas');
    const ctx = canvas.getContext('2d');
    ctx.strokeStyle = '#000';
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    let drawing = false;
    let lastX = 0;
    let lastY = 0;

    const getCoords = (e, rect) => {
        const clientX = e.clientX || (e.touches && e.touches[0].clientX);
        const clientY = e.clientY || (e.touches && e.touches[0].clientY);
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        return {
            x: (clientX - rect.left) * scaleX,
            y: (clientY - rect.top) * scaleY
        };
    };

    const startDraw = (e) => {
        e.preventDefault();
        drawing = true;
        const rect = canvas.getBoundingClientRect();
        const {x, y} = getCoords(e, rect);
        lastX = x;
        lastY = y;
        canvasHasContent = true;
    };

    const draw = (e) => {
        if (!drawing) return;
        e.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const {x, y} = getCoords(e, rect);

        ctx.beginPath();
        ctx.moveTo(lastX, lastY);
        ctx.lineTo(x, y);
        ctx.stroke();

        lastX = x;
        lastY = y;
    };

    const stopDraw = () => {
        drawing = false;
    };

    canvas.addEventListener('mousedown', startDraw);
    canvas.addEventListener('mousemove', draw);
    canvas.addEventListener('mouseup', stopDraw);
    canvas.addEventListener('mouseleave', stopDraw);

    canvas.addEventListener('touchstart', startDraw);
    canvas.addEventListener('touchmove', draw);
    canvas.addEventListener('touchend', stopDraw);

    document.getElementById('btn-clear').addEventListener('click', () => {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        canvasHasContent = false;
    });
}

async function sendUnterschrift() {
    if (!document.getElementById('check-datenschutz').checked) {
        showError('unterschrift-error', 'Bitte bestätigen Sie, dass Sie die Datenschutzinformation gelesen haben');
        return;
    }

    if (!canvasHasContent) {
        showError('unterschrift-error', 'Bitte unterschreiben Sie im Feld');
        return;
    }

    const canvas = document.getElementById('unterschrift-canvas');
    const bild = canvas.toDataURL('image/png');

    try {
        const result = await api('POST', '/unterschrift', {
            person: currentPerson,
            bild,
            ich_unterschreibe: true,
            haken: true,
            text_hash: textHash
        });

        if (result.fertig) {
            showStep('step-fertig');
        } else if (result.naechste_person) {
            currentPerson = result.naechste_person;
            document.getElementById('check-datenschutz').checked = false;
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            canvasHasContent = false;
            await loadTexte();
            alert(`Person ${currentPerson} ist jetzt an der Reihe`);
        }
    } catch (err) {
        showError('unterschrift-error', err.message);
    }
}

function showStep(id) {
    document.querySelectorAll('.step').forEach(s => s.classList.add('hidden'));
    document.getElementById(id).classList.remove('hidden');
}

function showError(id, msg) {
    const el = document.getElementById(id);
    el.textContent = msg;
    el.classList.remove('hidden');
}

function hideError(id) {
    document.getElementById(id).classList.add('hidden');
}
