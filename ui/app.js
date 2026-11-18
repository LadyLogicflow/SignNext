// SignNext Unterschriftsseite - CSP 'self' konform
'use strict';

const API = '/api/v1/zugang';
const HANDY_ABFRAGE_MS = 2000;
const ALLGEMEINER_FEHLER = 'Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.';
const HANDY_NICHT_MOEGLICH = 'Die Übergabe ans Handy ist gerade nicht möglich. Bitte unterschreiben Sie hier im Feld.';
let token = null;
let textHash = null;
let currentPerson = 1;
let personenGesamt = 1;
let gesehenSeiten = new Set();
let zeichenfeld = null;
let stand = null;             // letzter Stand vom Server (GET /status)
let dokument = null;          // aktuelles Dokument {rolle, titel, nr, von, seiten}
let texteNoetig = true;       // Datenschutzinformation und Haken fehlen für die nächste Person noch
let handoff = null;           // {id, ende (ms), timer} solange ein QR-Code für das Handy offen ist

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
        hinweisEl.textContent = hinweis.zugangshinweis || 'Bitte geben Sie Ihr Geburtsdatum ein.';       // die Textkennung steht im Nachweis, nicht auf der Seite
    } catch (err) {
        console.error(err);
    }

    document.getElementById('form-code').addEventListener('submit', checkCode);
    document.getElementById('btn-gelesen').addEventListener('click', markGelesen);
    document.getElementById('btn-unterschreiben').addEventListener('click', sendUnterschrift);
    document.getElementById('btn-neu-laden').addEventListener('click', () => location.reload());
    document.getElementById('btn-naechstes-dokument').addEventListener('click', async () => {
        loescheMeldung();
        try {
            await weiter();
        } catch (err) {
            if (!err.sitzung) zeigeMeldung(err.message);
        }
    });
    document.getElementById('btn-handy').addEventListener('click', handyStarten);
    document.getElementById('btn-handy-neu').addEventListener('click', handyStarten);
    document.getElementById('btn-handy-abbrechen').addEventListener('click', handyAbbrechen);
    zeichenfeld = Zeichenfeld.binde(document.getElementById('zeichenfeld'), {onAenderung: () => hideError('unterschrift-error')});
});

class ApiFehler extends Error {
    constructor(meldung, status, code, daten) {
        super(meldung);
        this.status = status;
        this.code = code;
        this.daten = daten;
    }
}

async function api(method, path, body = null) {
    const opts = {
        method,
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'Origin': window.location.origin}
    };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(API + path, opts);
    let data = {};
    try {
        data = await res.json();
    } catch (err) {
        data = {};
    }

    if (!res.ok) {
        if (res.status === 401 && data.error === 'sitzung_ungueltig') {
            sitzungAbgelaufen();
            const e = new ApiFehler('Sitzung abgelaufen', res.status, data.error, data);
            e.sitzung = true;
            throw e;
        }
        throw new ApiFehler(data.meldung || data.error || ALLGEMEINER_FEHLER, res.status, data.error, data);
    }
    return data;
}

// Meldungen stehen im Text der Seite (nie als Dialog): fehler = rot, info = Hinweisfeld
function zeigeMeldung(text, art = 'fehler', neuLaden = false) {
    const el = document.getElementById('meldung');
    el.textContent = text;
    el.className = art === 'info' ? 'info-box' : 'error';
    document.getElementById('btn-neu-laden').classList.toggle('hidden', !neuLaden);
}

function loescheMeldung() {
    const el = document.getElementById('meldung');
    el.textContent = '';
    el.className = 'error hidden';
    document.getElementById('btn-neu-laden').classList.add('hidden');
}

function sitzungAbgelaufen() {
    handyBeenden();
    zeigeMeldung('Ihre Sitzung ist abgelaufen. Bitte laden Sie die Seite neu und geben Sie Ihr Geburtsdatum erneut ein.', 'fehler', true);
}

async function checkCode(e) {
    e.preventDefault();
    const code = document.getElementById('code').value.replace(/\./g, '');
    if (!/^\d{8}$/.test(code)) {
        showError('code-error', 'Bitte im Format TT.MM.JJJJ eingeben (acht Ziffern)');
        return;
    }

    try {
        await api('POST', '/pruefen', {token, code});
        hideError('code-error');
        loescheMeldung();
        document.getElementById('versuch-info').classList.add('hidden');
        await weiter();
    } catch (err) {
        if (err.code === 'angaben_stimmen_nicht') {
            const {versuch, von, gesperrt} = err.daten;
            const info = document.getElementById('versuch-info');
            if (gesperrt) {
                showError('code-error', 'Der Link wurde nach zu vielen Fehlversuchen gesperrt');
                info.classList.add('hidden');
            } else {
                showError('code-error', 'Das Geburtsdatum stimmt nicht');
                info.textContent = `Versuch ${versuch} von ${von}`;
                info.classList.remove('hidden');
            }
        } else if (!err.sitzung) {
            showError('code-error', err.message);
        }
    }
}

// ----- Ablauf je Dokument: lesen -> (Datenschutz einmal je Person) -> zeichnen -> nächstes Dokument -----
// Der Stand des Vorgangs kommt vom Server (GET /status); er bestimmt, was als Nächstes erscheint.

function mehrereDokumente() {
    return !!dokument && dokument.von > 1;
}

function dokumentParam() {
    return mehrereDokumente() && dokument.rolle ? `?dokument=${encodeURIComponent(dokument.rolle)}` : '';
}

function personenImDokument() {
    const d = ((stand && stand.dokumente) || []).find(x => dokument && x.rolle === dokument.rolle);
    return d && Array.isArray(d.erforderlich) && d.erforderlich.length ? d.erforderlich.length : personenGesamt;
}

async function weiter() {
    const s = await api('GET', '/status');
    stand = s;
    personenGesamt = s.personen || 1;
    if (s.naechste_person) currentPerson = s.naechste_person;
    dokument = s.dokument || null;
    texteNoetig = s.texte_noetig !== false;
    fortschrittZeichnen();
    if (s.fertig === true || s.schritt === 'fertig') {
        showStep('step-fertig');
        return;
    }
    if (s.schritt === 'lesen') await seitenLaden();
    else await unterschriftZeigen();
}

function fortschrittZeichnen() {
    const liste = document.getElementById('dok-fortschritt');
    liste.textContent = '';
    const alle = (stand && stand.dokumente) || [];
    if (alle.length < 2) {
        liste.classList.add('hidden');
        return;
    }
    alle.forEach((d, i) => {
        const li = document.createElement('li');
        const aktuell = !!dokument && d.rolle === dokument.rolle;
        li.className = d.fertig ? 'dok-fertig' : (aktuell ? 'dok-aktuell' : 'dok-offen');
        if (aktuell && !d.fertig) li.setAttribute('aria-current', 'step');
        const marke = document.createElement('b');
        marke.textContent = d.fertig ? '✓' : String(i + 1);
        const text = document.createElement('span');
        const noetig = (d.erforderlich || []).length;
        const fertigePersonen = (d.unterschrieben || []).length;
        text.textContent = `Dokument ${i + 1}: ${d.titel}` + (noetig > 1 && !d.fertig && fertigePersonen > 0 ? ` (${fertigePersonen} von ${noetig} unterschrieben)` : '');
        li.append(marke, text);
        liste.appendChild(li);
    });
    liste.classList.remove('hidden');
}

async function seitenLaden() {
    showStep('step-seiten');
    const seiten = stand.seiten || (dokument && dokument.seiten) || 0;
    const titel = document.getElementById('dokument-titel');
    if (mehrereDokumente()) {
        titel.textContent = `Dokument ${dokument.nr} von ${dokument.von}: ${dokument.titel}`;
        titel.classList.remove('hidden');
    } else {
        titel.classList.add('hidden');
    }
    gesehenSeiten = new Set();
    document.getElementById('btn-gelesen').disabled = true;
    document.getElementById('seiten-geladen').textContent = '0';
    document.getElementById('seiten-gesamt').textContent = seiten;
    document.getElementById('seiten-fortschritt').classList.remove('hidden');

    const container = document.getElementById('seiten-container');
    container.textContent = '';
    for (let i = 1; i <= seiten; i++) {
        const img = document.createElement('img');
        img.alt = `Seite ${i}`;
        img.addEventListener('load', () => {
            gesehenSeiten.add(i);
            updateSeitenProgress(seiten);
        });
        container.appendChild(img);

        try {
            const r = await fetch(`${API}/seite/${i}${dokumentParam()}`, {credentials: 'same-origin'});
            if (!r.ok) {
                if (r.status === 401) {
                    showStep('step-hinweis');
                    zeigeMeldung('Die Sitzung ist abgelaufen. Bitte geben Sie Ihr Geburtsdatum erneut ein.');
                    return;
                }
                throw new Error(`Seite ${i} konnte nicht geladen werden (${r.status})`);
            }
            const blob = await r.blob();
            img.src = URL.createObjectURL(blob);
        } catch (err) {
            zeigeMeldung(`Fehler beim Laden der Seiten: ${err.message}`);
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
        await api('POST', '/gelesen', Object.assign({bestaetigt: true}, mehrereDokumente() ? {dokument: dokument.rolle} : {}));
        loescheMeldung();
        await weiter();
    } catch (err) {
        if (!err.sitzung) zeigeMeldung(`Fehler: ${err.message}`);
    }
}

function personenBeschriftung() {
    const teile = [];
    if (mehrereDokumente()) teile.push(`Dokument ${dokument.nr} von ${dokument.von}`);
    if (personenImDokument() > 1) teile.push(`Person ${currentPerson} von ${personenImDokument()}`);
    return teile.length ? `Unterschrift ${teile.join(' · ')}` : 'Unterschrift';
}

async function unterschriftZeigen() {
    showStep('step-unterschrift');
    zeichenfeld.setzeBeschriftung(personenBeschriftung());
    const zeile = document.getElementById('dokument-zeile');
    if (mehrereDokumente()) {
        zeile.textContent = `Sie unterschreiben: Dokument ${dokument.nr} von ${dokument.von}: ${dokument.titel}`;      // eigene Zeile über dem unveränderten Unterschriftssatz
        zeile.classList.remove('hidden');
    } else {
        zeile.classList.add('hidden');
    }
    const block = document.getElementById('texte-block');
    if (!texteNoetig) {                                   // Datenschutzinformation und Haken gelten einmal je Person und Vorgang
        block.classList.add('hidden');
        textHash = null;
        return;
    }
    block.classList.remove('hidden');
    await loadTexte();
}

async function loadTexte() {
    const texte = await api('GET', '/texte');
    textHash = texte.hash;

    // Fehlt ein Text, wird nicht unterschrieben: zurück zum Lesen, mit verständlichem Satz (kein Platzhalter im Text)
    if (!texte.texte || !texte.texte.datenschutz || !texte.texte.unterschriftssatz) {
        showStep('step-seiten');
        throw new Error('Die Datenschutzinformation konnte nicht geladen werden. Bitte versuchen Sie es später noch einmal oder wenden Sie sich an Ihre Beratungsstelle.');
    }
    const datenschutz = document.getElementById('datenschutz-text');
    datenschutz.textContent = `Datenschutzinformation\n\n${texte.texte.datenschutz}\n\n---\n\n${texte.texte.unterschriftssatz}`;

    document.getElementById('haken-text').textContent = texte.texte.haken || 'Ich habe die Datenschutzinformation gelesen';
}

// Nach diesen Fehlern des Servers hat sich der Stand geändert: Seite neu abgleichen statt weiterzumachen
const STAND_GEAENDERT = ['lesen_fehlt', 'reihenfolge', 'dokument_nicht_dran'];

async function sendUnterschrift() {
    if (texteNoetig && !document.getElementById('check-datenschutz').checked) {
        showError('unterschrift-error', 'Bitte bestätigen Sie, dass Sie die Datenschutzinformation gelesen haben');
        return;
    }

    if (!zeichenfeld.hatInhalt()) {
        showError('unterschrift-error', handoff ? 'Bitte am Handy unterschreiben oder den QR-Code abbrechen und hier zeichnen' : 'Bitte unterschreiben Sie im Feld');
        return;
    }

    const knopf = document.getElementById('btn-unterschreiben');
    knopf.disabled = true;
    try {
        const daten = {person: currentPerson, bild: zeichenfeld.png(), ich_unterschreibe: true};
        if (mehrereDokumente()) daten.dokument = dokument.rolle;
        if (texteNoetig) {
            daten.haken = true;
            daten.text_hash = textHash;
        }
        const bisherDokument = dokument;
        const bisher = currentPerson;
        const result = await api('POST', '/unterschrift', daten);
        document.getElementById('check-datenschutz').checked = false;
        zeichenfeld.leeren();

        if (result.fertig) {
            ((stand && stand.dokumente) || []).forEach(d => { d.fertig = true; });
            fortschrittZeichnen();
            showStep('step-fertig');
        } else if (result.dokument_fertig) {
            dokumentFertigZeigen(bisherDokument, result.naechste);
        } else {
            await weiter();
            const info = document.getElementById('person-info');
            info.textContent = `Person ${bisher} hat unterschrieben. Jetzt ist Person ${currentPerson} an der Reihe.`;
            info.classList.remove('hidden');
        }
    } catch (err) {
        if (err.sitzung) return;
        showError('unterschrift-error', err.message);
        if (STAND_GEAENDERT.includes(err.code)) {
            try {
                await weiter();
            } catch (e) {
                // der Abgleich ist nur Hilfe; die Meldung steht schon da
            }
        }
    } finally {
        knopf.disabled = false;
    }
}

// Zwischenschritt: dieses Dokument ist von allen unterschrieben, das nächste wird gelesen
function dokumentFertigZeigen(fertig, naechste) {
    const alle = (stand && stand.dokumente) || [];
    const dieses = alle.find(d => fertig && d.rolle === fertig.rolle);
    if (dieses) dieses.fertig = true;
    const folge = alle.find(d => naechste && d.rolle === naechste.dokument);
    fortschrittZeichnen();
    document.getElementById('dokument-fertig-titel').textContent = `Dokument ${fertig ? fertig.nr : ''} von ${fertig ? fertig.von : ''} unterschrieben ✓`;
    document.getElementById('dokument-fertig-text').textContent = fertig ? `${fertig.titel} ist von allen Personen unterschrieben.` : '';
    document.getElementById('btn-naechstes-dokument').textContent = `Weiter zu Dokument ${fertig ? fertig.nr + 1 : ''}: ${folge ? folge.titel : ''}`;
    document.getElementById('person-info').classList.add('hidden');
    showStep('step-dokument-fertig');
}

// ----- Handy-Übergabe: QR-Code am Computer, Unterschrift auf dem Handy, Bild kommt zurück ins Zeichenfeld -----

function handyAnzeigen(an) {
    document.getElementById('handy-box').classList.toggle('hidden', !an);
    document.getElementById('zeichenfeld').classList.toggle('hidden', an);
}

function qrBild(svg) {
    const img = document.getElementById('handy-qr');
    const adresse = QrBild.adresse(svg);
    if (adresse) {
        img.src = adresse;                                                              // als Bild, nie als Markup eingefügt
        img.classList.remove('hidden');
    } else {
        img.removeAttribute('src');
        img.classList.add('hidden');
    }
}

function handyStatus(text) {
    document.getElementById('handy-status').textContent = text;
}

function restZeit(sekunden) {
    const s = Math.max(0, Math.round(sekunden));
    return `Noch ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} Minuten gültig.`;
}

function handyBeenden() {
    if (handoff && handoff.timer) clearTimeout(handoff.timer);
    handoff = null;
    handyAnzeigen(false);
}

async function handyStarten() {
    loescheMeldung();
    hideError('unterschrift-error');
    if (handoff && handoff.timer) clearTimeout(handoff.timer);
    handoff = null;
    try {
        const r = await api('POST', '/handoff', Object.assign({person: currentPerson}, mehrereDokumente() ? {dokument: dokument.rolle} : {}));
        handoff = {id: r.handoff_id, timer: null};
        handyAnzeigen(true);
        qrBild(r.qr_svg);
        document.getElementById('btn-handy-neu').classList.add('hidden');
        handyStatus('Warten auf das Handy: Bitte den QR-Code mit der Handy-Kamera scannen und dort unterschreiben.');
        document.getElementById('handy-rest').textContent = restZeit(r.sekunden);
        handoff.timer = setTimeout(handyAbfragen, HANDY_ABFRAGE_MS);
    } catch (err) {
        // Gibt es die Übergabe nicht (oder liefert der Server keinen lesbaren Text), bleibt das Zeichenfeld nutzbar und der Satz sagt das
        const fehlt = [404, 405, 501].includes(err.status) && (!err.code || err.message === err.code);
        if (!err.sitzung) zeigeMeldung(fehlt ? HANDY_NICHT_MOEGLICH : err.message);
    }
}

async function handyAbfragen() {
    const aktuell = handoff;
    if (!aktuell) return;
    try {
        const r = await api('GET', `/handoff/${aktuell.id}`);
        if (handoff !== aktuell) return;
        document.getElementById('handy-rest').textContent = restZeit(r.sekunden_rest);
        if (r.status === 'gezeichnet') {
            handyStatus('Unterschrift vom Handy erhalten ...');
            await handyAbholen(aktuell);
            return;
        }
        if (r.status === 'abgelaufen') {
            handyStatus('Der QR-Code ist abgelaufen.');
            qrBild(null);
            document.getElementById('handy-rest').textContent = '';
            document.getElementById('btn-handy-neu').classList.remove('hidden');
            handoff = null;
            return;
        }
        handyStatus(r.status === 'verbunden'
            ? 'Handy verbunden. Bitte dort unterschreiben.'
            : 'Warten auf das Handy: Bitte den QR-Code mit der Handy-Kamera scannen und dort unterschreiben.');
        aktuell.timer = setTimeout(handyAbfragen, HANDY_ABFRAGE_MS);
    } catch (err) {
        if (handoff !== aktuell) return;
        if (err.sitzung) return;
        if (err.status === 404 || err.status === 410) {            // der Code ist am Server nicht mehr bekannt: wie abgelaufen behandeln
            handyStatus('Der QR-Code gilt nicht mehr.');
            qrBild(null);
            document.getElementById('handy-rest').textContent = '';
            document.getElementById('btn-handy-neu').classList.remove('hidden');
            handoff = null;
            return;
        }
        handyStatus(`Fehler bei der Abfrage: ${err.message}`);
        aktuell.timer = setTimeout(handyAbfragen, HANDY_ABFRAGE_MS);
    }
}

async function handyAbholen(aktuell) {
    try {
        const r = await api('POST', `/handoff/${aktuell.id}/abholen`);
        await zeichenfeld.setzeBild(r.bild);
        handyBeenden();
        zeigeMeldung('Die Unterschrift vom Handy wurde übernommen. Mit „löschen“ können Sie sie verwerfen und neu zeichnen.', 'info');
    } catch (err) {
        handoff = null;
        document.getElementById('btn-handy-neu').classList.remove('hidden');
        if (!err.sitzung) handyStatus(`Die Unterschrift konnte nicht übernommen werden: ${err.message}`);
    }
}

async function handyAbbrechen() {
    const aktuell = handoff;
    handyBeenden();
    if (aktuell) {
        try {
            await api('DELETE', `/handoff/${aktuell.id}`);
        } catch (err) {
            // der Abbruch am Server ist nur Aufräumen; der Code verfällt ohnehin
        }
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
