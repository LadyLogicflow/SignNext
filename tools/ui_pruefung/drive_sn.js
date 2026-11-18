// Treibt Webys SignNext-Oberfläche (ui/) in jsdom. Die echten Dateien aus ui/ werden von einem kleinen Testserver ausgeliefert; die Schnittstelle (/api/v1/...)
// spielt dieselbe Datei nach der Schnittstellenbeschreibung nach (Zugang, Handy-Übergabe, Handy-Seite, Demo). Eingabe: JSON-Argument, Ausgabe: eine Zeile RESULT {...}
const {JSDOM, VirtualConsole} = require('jsdom');
const http = require('http');
const fs = require('fs');
const path = require('path');

const arg = JSON.parse(process.argv[2]);
const UI = path.resolve(__dirname, '..', '..', 'ui');
const SEITEN = {'/u': 'unterschrift.html', '/d': 'download.html', '/h': 'handy.html', '/vorgaenge': 'vorgaenge.html', '/einstellungen': 'einstellungen.html', '/pruefung': 'pruefung.html', '/mail-zugang': 'mail-zugang.html'};
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const QR_SVG = '<svg viewBox="0 0 10 10"><title>QR-Code</title><rect width="10" height="10" fill="#000"/></svg>';        // wie das Backend: ohne Namensraum
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const extern = !!arg.basis;           // Modus mit echtem Server (arg.basis, arg.cookie): kein nachgespielter Server, die Aufrufe werden nur mitgeschrieben

// ----- nachgespielte Schnittstelle (Standardwerte, je Szenario über arg.api überschreibbar) -----
const cfg = Object.assign({
    personen: 1,
    sitzungFehlt: null,                    // z. B. '/status': antwortet dort mit 401 sitzung_ungueltig
    seitenFehler: false,
    unterschriftFehler: null,              // {status, error, meldung}
    handoffStartFehler: null,
    handoffStatus: ['wartet', 'verbunden', 'gezeichnet'],
    handoffAbholFehler: null,
    handoffBild: PNG,
    qrSvg: QR_SVG,
    handyInfo: {person: 1, personen: 1, status: 'verbunden'},
    handyInfoFehler: null,
    handyUnterschriftFehler: null,
    // Berater-Seiten und Demo
    me: {status: 200, body: {email: 'berater@example.org', demo: false}},
    demoInfo: {demo: true, versand: 'testadressen', testadressen: ['a@example.org', 'b@example.org'], mail_zugang_geprueft: true, beispiele: ['anschreiben', 'anschreiben-einzel', 'protokoll-ehepaar', 'protokoll-einzel']},
    testmailFehler: null,
    versenden: {status: 200, body: {ok: true}},
    versendenOhneMail: {status: 200, body: {ok: true}},
    mailZugang: {konfiguriert: true, geprueft_am: '2026-11-01T10:00:00Z', host: 'smtp.example', port: 587, benutzer: 'konto', absender_name: 'Beratungsstelle', absender_adresse: 'noreply@example.org', bcc_an_absender: false},
    mailPut: {status: 200, body: {ok: true}},
}, arg.api || {});
const STANDARD_LISTE = [{id: 1, status: 'bereit', erstellt: '2026-11-17T10:00:00Z', name: 'Mustermann_Steuer2025', personen: 2, link_status: null, link_laeuft_ab: null}];
const STANDARD_VORGANG = {id: 1, status: 'bereit', erstellt: '2026-11-17T13:05:00Z', name: 'Mustermann_Steuer2025', personen: 2, abgleich: {status: 'automatisch', treffer: ['steuernummer', 'mitgliedsnummer'], abweichend: [], fehlend: []},
    bestaetigt_am: null, versendet_am: null, unterschrieben_am: null, abgeschlossen_am: null, aufbewahrung_bis: null, erinnerungen_aus: false, link_status: null, dokumente: []};
const STANDARD_ANGABEN = {name: {wert: 'Erika Muster', herkunft: 'pdf'}, geburtsdatum: {wert: '01.02.1960', herkunft: 'pdf', gesetzt: true}, email: {wert: 'erika@example.org', herkunft: 'pdf'}, personen: {wert: 2, herkunft: 'pdf'}};
const zustand = {v: Object.assign({}, STANDARD_VORGANG, cfg.vorgang || {}), angaben: Object.assign(JSON.parse(JSON.stringify(STANDARD_ANGABEN)), JSON.parse(JSON.stringify(cfg.angaben || {}))), person: 1, unterschrieben: [], handoffAbfragen: 0, abgeholt: false, handoffNummer: 0, downloads: [], gelesen: {}, dokUnter: {}, hakenPerson: {}, vorgaenge: JSON.parse(JSON.stringify(cfg.vorgaenge || STANDARD_LISTE))};
zustand.abgeholt = JSON.parse(JSON.stringify(cfg.abgeholt || {}));
zustand.abrufe = [];
const aufrufe = [];

// Vorgangs-Antwort wie das Backend (Teil 19): ampel + je Dokument abgeholt_am (PDF) / nachweis_abgeholt_am; gelb/gruen erst ab vollständig unterschrieben
function vorgangMitAbholstand() {
    if (!cfg.abholSicht) return zustand.v;
    const liste = dokumente().map(d => {
        const a = zustand.abgeholt[d.rolle] || {};
        return {rolle: d.rolle, titel: d.titel, erforderlich: d.erforderlich, unterschrieben: d.erforderlich.slice(), fertig: true, abgeholt_am: a.dokument || null, dokument_abgeholt_am: a.dokument || null, nachweis_abgeholt_am: a.nachweis || null, dateiname: d.dateiname, nachweis_dateiname: d.nachweis_dateiname};
    });
    const alle = liste.every(d => d.abgeholt_am);
    const ampel = cfg.ampel !== undefined ? cfg.ampel : (alle ? 'gruen' : 'gelb');
    return Object.assign({}, zustand.v, {ampel, dokumente_fortschritt: liste});
}

function senden(res, status, obj, typ) {
    const roh = typ ? obj : JSON.stringify(obj);
    res.writeHead(status, {'Content-Type': typ || 'application/json'});
    res.end(roh);
}

// Dokumente des Vorgangs (cfg.dokumente), Standard: ein Dokument. Je Dokument die Personen mit Unterschriftsstelle (erforderlich).
function dokumente() {
    const liste = cfg.dokumente || [{rolle: 'B', titel: 'Anschreiben', seiten: 2}];
    return liste.map(d => Object.assign({erforderlich: Array.from({length: cfg.personen}, (_, i) => i + 1)}, d));
}

// GET /api/v1/zugang/status: das aktuelle Dokument ist das erste, in dem noch jemand fehlt
function mitgliedStand() {
    const liste = dokumente().map(d => {
        const u = zustand.dokUnter[d.rolle] || [];
        return {rolle: d.rolle, titel: d.titel, seiten: d.seiten, erforderlich: d.erforderlich, unterschrieben: u.slice(), fertig: d.erforderlich.every(p => u.includes(p))};
    });
    const idx = liste.findIndex(d => !d.fertig);
    if (idx < 0) return {schritt: 'fertig', fertig: true, dokumente: liste, personen: cfg.personen, unterschrieben: [], naechste_person: null, texte_noetig: false};
    const cur = liste[idx];
    const naechste = cur.erforderlich.find(p => !cur.unterschrieben.includes(p));
    const gelesen = !!zustand.gelesen[cur.rolle];
    const texteNoetig = !zustand.hakenPerson[naechste];
    return {schritt: !gelesen ? 'lesen' : (texteNoetig ? 'texte' : 'unterschreiben'), dokument: {rolle: cur.rolle, titel: cur.titel, nr: idx + 1, von: liste.length, seiten: cur.seiten},
        dokumente: liste, seiten: cur.seiten, geliefert: cur.seiten, personen: cfg.personen, unterschrieben: cur.unterschrieben.slice(), naechste_person: naechste, texte_noetig: texteNoetig};
}

function api(req, res, pfad, body) {
    const m = req.method;
    const fehler = (status, error, meldung, extra) => senden(res, status, Object.assign({error, meldung}, extra || {}));
    if (cfg.sitzungFehlt && pfad === cfg.sitzungFehlt) return fehler(401, 'sitzung_ungueltig', 'Sitzung ungültig');
    // Mitglied am Computer (Cookie sn_zugang)
    if (pfad === '/api/v1/download' && m === 'POST') {
        if (body.code !== '01021960') return fehler(401, 'angaben_stimmen_nicht', 'falsch', {versuch: 1, von: 5, gesperrt: false});
        if (cfg.downloadDokumente && !body.dokument) return senden(res, 200, {dokumente: cfg.downloadDokumente});
        res.writeHead(200, {'Content-Type': 'application/pdf', 'X-Abruf': '1', 'X-Abruf-Max': '3'});
        return res.end('%PDF-1.4 test');
    }
    if (pfad === '/api/v1/zugang/hinweis') return senden(res, 200, {kennung: cfg.kennung || 'Kennung-1', zugangshinweis: 'Bitte Geburtsdatum eingeben.', freigegeben: cfg.texteFreigegeben !== false});
    if (pfad === '/api/v1/zugang/pruefen') return body.code === '01021960' ? senden(res, 200, {ok: true}) : fehler(401, 'angaben_stimmen_nicht', 'falsch', {versuch: 1, von: 5, gesperrt: false});
    if (pfad === '/api/v1/zugang/status') return senden(res, 200, mitgliedStand());
    if (pfad.startsWith('/api/v1/zugang/seite/')) {
        if (cfg.seitenFehler) return fehler(500, 'fehler', 'Serverfehler');
        res.writeHead(200, {'Content-Type': 'image/png'});
        return res.end(Buffer.from(PNG.split(',')[1], 'base64'));
    }
    if (pfad === '/api/v1/zugang/gelesen') {
        const st = mitgliedStand();
        zustand.gelesen[body.dokument || (st.dokument && st.dokument.rolle)] = true;
        return senden(res, 200, {ok: true});
    }
    if (pfad === '/api/v1/zugang/texte') return senden(res, 200, {hash: 'h1', freigegeben: cfg.texteFreigegeben !== false, texte: Object.assign({datenschutz: 'Datenschutz', unterschriftssatz: 'Satz', haken: 'Ich habe gelesen'}, cfg.texteFehlen || {})});
    if (pfad === '/api/v1/zugang/unterschrift') {
        if (cfg.unterschriftFehler) return fehler(cfg.unterschriftFehler.status, cfg.unterschriftFehler.error, cfg.unterschriftFehler.meldung);
        const st = mitgliedStand();
        if (st.fertig) return fehler(409, 'reihenfolge', 'Es ist schon alles unterschrieben.');
        const rolle = body.dokument || (st.dokumente.length === 1 ? st.dokument.rolle : null);
        if (!rolle || !dokumente().some(d => d.rolle === rolle)) return fehler(400, 'dokument_ungueltig', 'Das Dokument ist ungültig.');
        if (rolle !== st.dokument.rolle) return fehler(409, 'dokument_nicht_dran', 'Dieses Dokument ist noch nicht an der Reihe.');
        if (body.person !== st.naechste_person) return fehler(409, 'reihenfolge', 'Diese Person ist noch nicht an der Reihe.');
        if (!zustand.gelesen[rolle]) return fehler(409, 'lesen_fehlt', 'Bitte lesen Sie dieses Dokument zuerst vollständig.');
        if (st.texte_noetig && !(body.haken === true && body.text_hash)) return fehler(409, 'texte_fehlen', 'Bitte bestätigen Sie die Datenschutzinformation.');
        (zustand.dokUnter[rolle] = zustand.dokUnter[rolle] || []).push(body.person);
        zustand.hakenPerson[body.person] = true;
        zustand.unterschrieben.push(body.person);
        const nach = mitgliedStand();
        if (nach.fertig) return senden(res, 200, {fertig: true});
        return senden(res, 200, {fertig: false, dokument_fertig: nach.dokument.rolle !== rolle, naechste: {dokument: nach.dokument.rolle, person: nach.naechste_person}});
    }
    if (pfad === '/api/v1/zugang/handoff' && m === 'POST') {
        if (cfg.handoffStartFehler) return fehler(cfg.handoffStartFehler.status, cfg.handoffStartFehler.error, cfg.handoffStartFehler.meldung);
        zustand.handoffNummer += 1;
        zustand.handoffAbfragen = 0;
        zustand.abgeholt = false;
        return senden(res, 200, {handoff_id: 'hid' + zustand.handoffNummer, url: 'https://sn.example/h#geheim', qr_svg: cfg.qrSvg, sekunden: 600});
    }
    const h = pfad.match(/^\/api\/v1\/zugang\/handoff\/(hid\d+)(\/abholen)?$/);
    if (h && m === 'GET') {
        const folge = cfg.handoffStatus;
        const status = folge[Math.min(zustand.handoffAbfragen, folge.length - 1)];
        zustand.handoffAbfragen += 1;
        return senden(res, 200, {status, sekunden_rest: 590 - zustand.handoffAbfragen});
    }
    if (h && h[2] && m === 'POST') {
        if (cfg.handoffAbholFehler) return fehler(cfg.handoffAbholFehler.status, cfg.handoffAbholFehler.error, cfg.handoffAbholFehler.meldung);
        if (zustand.abgeholt) return fehler(410, 'schon_abgeholt', 'Die Unterschrift wurde schon abgeholt.');
        zustand.abgeholt = true;
        return senden(res, 200, {bild: cfg.handoffBild});
    }
    if (h && m === 'DELETE') return senden(res, 200, {ok: true});
    // Handy-Seite (kein Cookie, Kennzeichen im Body)
    if (pfad === '/api/v1/handy/info') {
        if (cfg.handyInfoFehler) return fehler(cfg.handyInfoFehler.status, cfg.handyInfoFehler.error, cfg.handyInfoFehler.meldung);
        return senden(res, 200, cfg.handyInfo);
    }
    if (pfad === '/api/v1/handy/unterschrift') {
        if (cfg.handyUnterschriftFehler) return fehler(cfg.handyUnterschriftFehler.status, cfg.handyUnterschriftFehler.error, cfg.handyUnterschriftFehler.meldung);
        return senden(res, 200, {ok: true});
    }
    // Berater (Portal-Anmeldung bzw. Demo)
    if (pfad === '/api/v1/me') return senden(res, cfg.me.status, cfg.me.body);
    if (pfad === '/api/v1/vorgaenge' && m === 'POST') {
        const antwort = cfg.upload || {status: 201, body: {vorgang_id: 2, status: 'bereit', personen: 2, abgleich: {status: 'automatisch'}, name: 'Mustermann_Steuer2025',
            dokumente: [{rolle: 'A', seiten: 2, stellen: 0}, {rolle: 'B', seiten: 3, stellen: 2}]}};
        const fertig = () => {
            if (antwort.status === 201) zustand.vorgaenge.unshift({id: antwort.body.vorgang_id, status: antwort.body.status, erstellt: '2026-11-17T12:00:00Z'});
            senden(res, antwort.status, antwort.body === undefined ? '<html>kaputt</html>' : antwort.body, antwort.body === undefined ? 'text/html' : undefined);
        };
        return cfg.uploadVerzoegerung ? setTimeout(fertig, cfg.uploadVerzoegerung) : fertig();
    }
    if (pfad === '/api/v1/vorgaenge' && m === 'GET') {
        if (cfg.listeFehler) return cfg.listeFehler.body === undefined ? senden(res, cfg.listeFehler.status, '<html>kaputt</html>', 'text/html') : senden(res, cfg.listeFehler.status, cfg.listeFehler.body);
        return senden(res, 200, {vorgaenge: zustand.vorgaenge, status_texte: cfg.statusTexte || {bereit: 'Bereit zum Versand', neu_vom_server: 'Neuer Wert vom Server'}});
    }
    let treffer = /^\/api\/v1\/vorgaenge\/(\d+)$/.exec(pfad);
    if (treffer && m === 'DELETE') {
        const antwort = cfg.loeschen || {status: 200, body: {ok: true}};
        if (antwort.status === 200) zustand.vorgaenge = zustand.vorgaenge.filter(v => v.id !== Number(treffer[1]));
        return senden(res, antwort.status, antwort.body);
    }
    treffer = /^\/api\/v1\/vorgaenge\/(\d+)\/neuer-link$/.exec(pfad);
    if (treffer && m === 'POST') {
        const antwort = cfg.neuerLink || {status: 200, body: {ok: true}};
        if (antwort.status === 200) zustand.vorgaenge = zustand.vorgaenge.map(v => v.id === Number(treffer[1]) ? Object.assign({}, v, {link_status: 'aktiv', link_laeuft_ab: new Date(Date.now() + 7 * 86400000).toISOString()}) : v);
        if (antwort.status === 200) zustand.v.link_status = 'aktiv';
        return senden(res, antwort.status, antwort.body);
    }
    treffer = /^\/api\/v1\/vorgaenge\/(\d+)\/download\/(dokument|nachweis)$/.exec(pfad);
    if (treffer && m === 'GET') {
        if (cfg.downloadFehler) return senden(res, cfg.downloadFehler.status, cfg.downloadFehler.body);
        if (cfg.downloadMehrere && !new URL(req.url, 'http://x').searchParams.get('dokument')) return fehler(400, 'dokument_waehlen', 'Bitte wählen Sie ein Dokument.', {dokumente: cfg.downloadMehrere});
        const rolleAbruf = new URL(req.url, 'http://x').searchParams.get('dokument') || dokumente()[0].rolle;
        zustand.abrufe.push(`${treffer[2]}:${rolleAbruf}`);
        if (cfg.downloadFehlerDatei && cfg.downloadFehlerDatei.art === treffer[2] && cfg.downloadFehlerDatei.rolle === rolleAbruf) return senden(res, cfg.downloadFehlerDatei.status, cfg.downloadFehlerDatei.body);
        if (cfg.abholSicht) {
            zustand.abgeholt[rolleAbruf] = zustand.abgeholt[rolleAbruf] || {};
            if (!zustand.abgeholt[rolleAbruf][treffer[2]]) zustand.abgeholt[rolleAbruf][treffer[2]] = `2026-11-18T09:${String(10 + zustand.abrufe.length).padStart(2, '0')}:00Z`;
        }
        return senden(res, 200, '%PDF-1.4 test', 'application/pdf');
    }
    if (pfad === '/api/v1/demo' && m === 'GET') return senden(res, 200, cfg.demoInfo);
    if (pfad === '/api/v1/demo/vorgang' && m === 'POST') {
        const id = 7 + zustand.vorgaenge.length;
        zustand.vorgaenge.push({id, status: 'bereit', erstellt: '2026-11-17T11:00:00Z'});
        return senden(res, 200, {vorgang_id: id, link: 'https://sn.example/u#demo' + id, geburtsdatum: '01.02.1960', code: '01021960', personen: 2, qr_svg: cfg.qrSvg});
    }
    if (pfad === '/api/v1/demo/testmail' && m === 'POST') {
        if (cfg.testmailFehler) return fehler(cfg.testmailFehler.status, cfg.testmailFehler.error, cfg.testmailFehler.meldung);
        return senden(res, 200, {ergebnis: 'gesendet'});
    }
    if (pfad === '/api/v1/einstellungen' && m === 'PUT') { const a = cfg.einstellungenPut || {status: 200, body: {ok: true}}; return senden(res, a.status, a.body); }
    if (pfad === '/api/v1/mail-zugang' && m === 'DELETE') {
        const a = cfg.mailDelete || {status: 204};
        if (a.status === 204) { res.writeHead(204); return res.end(); }
        return senden(res, a.status, a.body);
    }
    if (pfad === '/api/v1/mail-zugang/pruefen' && m === 'POST') {
        const a = cfg.mailPruefen || {status: 200, body: {ok: true, verbindung: {tls_version: 'TLSv1.3', cipher: 'TLS_AES_256_GCM_SHA384', zertifikat_gueltig_bis: '2027-01-01', aussteller: 'Beispiel CA'}}};
        return senden(res, a.status, a.body);
    }
    if (cfg.hinweisFehler && (pfad === '/api/v1/einstellungen' || (pfad === '/api/v1/mail-zugang' && m === 'GET'))) return senden(res, 500, {x: 1});
    if (pfad === '/api/v1/einstellungen' && cfg.einstellungen) return senden(res, 200, cfg.einstellungen);
    if (pfad === '/api/v1/einstellungen') return senden(res, 200, {gesetzt: true, verantwortlicher: 'Beratungsstelle Beispiel, Musterstr. 1', datenschutz_link: 'https://beispiel.example/datenschutz', aufbewahrung_tage: 90, mitglied_download_link: false});
    if (pfad === '/api/v1/mail-zugang' && m === 'GET') return senden(res, 200, cfg.mailZugang);
    if (pfad === '/api/v1/mail-zugang' && m === 'PUT') return senden(res, cfg.mailPut.status, cfg.mailPut.body);
    if (/^\/api\/v1\/vorgaenge\/\d+$/.test(pfad) && m === 'GET') return cfg.vorgangFehler ? senden(res, cfg.vorgangFehler.status, cfg.vorgangFehler.body) : senden(res, 200, vorgangMitAbholstand());
    if (/^\/api\/v1\/vorgaenge\/\d+\/pruefung$/.test(pfad) && m === 'GET') {
        const a = zustand.angaben;
        const fehlend = [];
        if (!a.email.wert) fehlend.push('email');
        if (!a.geburtsdatum.gesetzt) fehlend.push('geburtsdatum');
        if (![1, 2].includes(a.personen.wert)) fehlend.push('personen');
        return senden(res, 200, {vorgang_id: zustand.v.id, status: zustand.v.status, abgleich: zustand.v.abgleich, name: a.name, geburtsdatum: a.geburtsdatum, email: a.email, personen: a.personen,
            fehlend, versandbereit_pdf: zustand.v.status === 'bereit' && fehlend.length === 0, erinnerungen_aus: zustand.v.erinnerungen_aus});
    }
    if (/^\/api\/v1\/vorgaenge\/\d+\/angaben$/.test(pfad) && m === 'PUT') {
        if (cfg.angabenFehler) return senden(res, cfg.angabenFehler.status, cfg.angabenFehler.body);
        if (!['bereit', 'bestaetigung_noetig'].includes(zustand.v.status)) return fehler(409, 'zustand_passt_nicht', 'Der Vorgang befindet sich nicht in diesem Zustand.');
        const a = zustand.angaben;
        if ('email' in body) a.email = {wert: body.email, herkunft: 'manuell'};
        if ('geburtsdatum' in body) a.geburtsdatum = {wert: null, herkunft: 'manuell', gesetzt: true};
        if ('personen' in body) a.personen = {wert: body.personen, herkunft: 'manuell'};
        return senden(res, 200, {ok: true});
    }
    if (/^\/api\/v1\/vorgaenge\/\d+\/abgleich-bestaetigen$/.test(pfad) && m === 'POST') {
        if (cfg.abgleichFehler) return senden(res, cfg.abgleichFehler.status, cfg.abgleichFehler.body);
        zustand.v.status = 'bereit';
        zustand.v.bestaetigt_am = '2026-11-17T13:20:00Z';
        return senden(res, 200, {ok: true});
    }
    if (/^\/api\/v1\/vorgaenge\/\d+\/erinnerungen$/.test(pfad) && m === 'PUT') {
        if (cfg.erinnerungenFehler) return senden(res, cfg.erinnerungenFehler.status, cfg.erinnerungenFehler.body);
        zustand.v.erinnerungen_aus = body.an === false;
        return senden(res, 200, {ok: true});
    }
    if (/^\/api\/v1\/vorgaenge\/\d+\/dokument$/.test(pfad)) { res.writeHead(200, {'Content-Type': 'application/pdf'}); return res.end('%PDF-1.4'); }
    if (/^\/api\/v1\/vorgaenge\/\d+\/versenden$/.test(pfad) && m === 'POST') {
        const v = body.ohne_mail ? cfg.versendenOhneMail : cfg.versenden;
        if (v.status === 200) Object.assign(zustand.v, {status: 'versendet', versendet_am: '2026-11-17T13:30:00Z', link_status: 'aktiv'});
        return senden(res, v.status, v.body);
    }
    return fehler(404, 'nicht_gefunden', 'unbekannt');
}

const server = http.createServer((req, res) => {
    const pfad = new URL(req.url, 'http://x').pathname;
    const teile = [];
    req.on('data', c => teile.push(c));
    req.on('end', () => {
        if (pfad.startsWith('/api/')) {
            let body = null;
            try { body = teile.length ? JSON.parse(Buffer.concat(teile).toString('utf8')) : null; } catch (e) { body = null; }
            const eintrag = {m: req.method, p: pfad, body, q: new URL(req.url, 'http://x').search};
            if (String(req.headers['content-type'] || '').startsWith('multipart/form-data')) {
                const roh = Buffer.concat(teile).toString('latin1');
                eintrag.dateien = [...roh.matchAll(/name="dateien"; filename="([^"]*)"/g)].map(x => x[1]);
            }
            aufrufe.push(eintrag);
            return api(req, res, pfad, body || {});
        }
        const datei = SEITEN[pfad] || (/^\/[a-z][a-z0-9_-]*\.(js|css)$/.test(pfad) ? pfad.slice(1) : null);
        if (!datei || !fs.existsSync(path.join(UI, datei))) return senden(res, 404, 'nicht gefunden', 'text/plain');
        senden(res, 200, fs.readFileSync(path.join(UI, datei)), datei.endsWith('.js') ? 'text/javascript' : datei.endsWith('.css') ? 'text/css' : 'text/html');
    });
});

async function main() {
    if (!extern) await new Promise(r => server.listen(0, '127.0.0.1', r));
    const BASE = extern ? arg.basis : `http://127.0.0.1:${server.address().port}`;
    const alerts = [];
    const zwischenablage = [];
    const zeichnung = {striche: 0, geleert: 0, bilder: 0, stil: null, capture: 0};
    const fehler = [];
    const vc = new VirtualConsole();
    vc.on('jsdomError', (e) => fehler.push('jsdomError: ' + ((e.detail && e.detail.message) || e.message)));
    const dom = await JSDOM.fromURL(BASE + arg.seite + (arg.fragment || ''), {
        runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
        beforeParse(win) {
            win.URL.createObjectURL = () => 'blob:test';
            win.URL.revokeObjectURL = () => {};
            const echtesKlick = win.HTMLAnchorElement.prototype.click;
            win.HTMLAnchorElement.prototype.click = function () {                 // Download-Verweise nur mitschreiben (jsdom navigiert nicht)
                if (this.hasAttribute('download')) { zustand.downloads.push(this.getAttribute('download')); return; }
                return echtesKlick.call(this);
            };
            win.fetch = async (u, o = {}) => {
                const url = new URL(u, BASE);
                const headers = Object.assign({}, o.headers || {});
                if (extern) {
                    if (arg.cookie) headers.Cookie = 'bst_session=' + arg.cookie;
                    Object.assign(headers, arg.kopf || {});              // z. B. X-Forwarded-For des Proxys im Offen-Modus der Demo
                    if (url.pathname.startsWith('/api/')) {
                        let body = null;
                        try { body = o.body ? JSON.parse(o.body) : null; } catch (e) { body = null; }
                        aufrufe.push({m: o.method || 'GET', p: url.pathname, body});
                    }
                }
                return fetch(url, {method: o.method || 'GET', headers, body: o.body});
            };
            win.alert = (t) => { alerts.push(String(t)); };
            Object.defineProperty(win.navigator, 'clipboard', {value: {writeText: async (t) => { zwischenablage.push(String(t)); }}});
            win.confirm = () => true;
            const echt = win.setTimeout.bind(win);
            win.setTimeout = (f, ms, ...rest) => echt(f, ms === 2000 ? 25 : ms, ...rest);                // nur die Handy-Abfrage (genau 2000 ms) läuft im Test schnell
            win.URL.createObjectURL = () => 'blob:fake';
            const ctx = {
                lineWidth: 1, lineCap: '', lineJoin: '', strokeStyle: '',
                beginPath() {}, moveTo() {}, lineTo() {},
                stroke() { zeichnung.striche += 1; zeichnung.stil = {breite: this.lineWidth, farbe: this.strokeStyle, cap: this.lineCap, join: this.lineJoin}; },
                clearRect() { zeichnung.geleert += 1; },
                drawImage() { zeichnung.bilder += 1; },
            };
            win.HTMLCanvasElement.prototype.getContext = () => ctx;
            win.HTMLCanvasElement.prototype.toDataURL = () => arg.png || PNG;
            win.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({left: 0, top: 0, width: 300, height: 100, right: 300, bottom: 100});
            win.HTMLCanvasElement.prototype.setPointerCapture = () => { zeichnung.capture += 1; };
            if (!win.PointerEvent) {                      // jsdom 24 kennt PointerEvent nicht: Mausereignis mit Zeiger-Eigenschaften
                win.PointerEvent = class extends win.MouseEvent {
                    constructor(typ, init = {}) { super(typ, init); this.pointerId = init.pointerId; this.pointerType = init.pointerType; }
                };
            }
            win.Image = class {
                set src(v) { this._src = v; setTimeout(() => { if (arg.bildFehler) { if (this.onerror) this.onerror(); } else if (this.onload) this.onload(); }, 0); }
                get src() { return this._src; }
            };
        },
    });
    const win = dom.window;
    const doc = win.document;
    await sleep(arg.start || 500);

    const sichtbar = (id) => { const e = doc.getElementById(id); return !!e && !e.classList.contains('hidden'); };
    const text = (id) => { const e = doc.getElementById(id); return e ? e.textContent.trim() : null; };
    const ampelVon = (id) => { const e = doc.getElementById(id); const a = e && !e.classList.contains('hidden') ? e.querySelector('.ampel') : null; return a ? {wert: a.dataset.ampel, klasse: a.className, symbol: a.querySelector('.ampel-symbol').textContent, text: a.querySelector('.ampel-text').textContent, symbol_versteckt: a.querySelector('.ampel-symbol').getAttribute('aria-hidden')} : null; };
    const aktiverSchritt = () => [...doc.querySelectorAll('.step')].filter(s => !s.classList.contains('hidden')).map(s => s.id);
    const berater = () => {
        const ergebnis = (id) => {
            const b = doc.getElementById(id);
            if (!b || b.classList.contains('hidden')) return null;
            const qr = b.querySelector('img.link-qr');
            return {text: b.textContent.replace(/\s+/g, ' ').trim(), link: b.querySelector('input.link-feld') ? b.querySelector('input.link-feld').value : null,
                    link_nur_lesen: b.querySelector('input.link-feld') ? b.querySelector('input.link-feld').readOnly : null,
                    kopieren: b.querySelector('button.link-kopieren') ? b.querySelector('button.link-kopieren').textContent : null,
                    qr: qr ? {tag: qr.tagName, src: qr.getAttribute('src')} : null};
        };
        const banner = doc.getElementById('demo-banner');
        const wm = doc.getElementById('wortmarke');
        const menue = [...doc.querySelectorAll('#berater-nav a')].map(a => ({text: a.textContent.trim(), href: a.getAttribute('href'), aktuell: a.getAttribute('aria-current') === 'page'}));
        const hinweise = [...doc.querySelectorAll('#hinweisleiste .hinweis')].map(h => ({text: h.querySelector('.hinweis-text').textContent.trim(), knopf: h.querySelector('a').textContent.trim(), href: h.querySelector('a').getAttribute('href')}));
        const karten = doc.querySelectorAll('#vorgaenge-liste .zeile').length;
        return {
            wortmarke: wm && wm.firstChild ? {text: wm.textContent, aria: wm.getAttribute('aria-label'), teile: [...wm.children].map(e => [e.tagName.toLowerCase(), e.getAttribute('class'), e.textContent]), chevron_pfad: wm.querySelector('svg path') ? wm.querySelector('svg path').getAttribute('d') : null, chevron_farbe: wm.querySelector('svg path') ? wm.querySelector('svg path').getAttribute('stroke') : null, chevron_versteckt: wm.querySelector('svg') ? wm.querySelector('svg').getAttribute('aria-hidden') : null} : null,
            angemeldet: sichtbar('angemeldet') ? text('angemeldet') : null,
            portal_link: sichtbar('portal-link') ? doc.getElementById('portal-link').getAttribute('href') : null,
            menue, hinweise,
            formular: doc.getElementById('seite-meldung') ? (() => {
                const t = (id) => { const e = doc.getElementById(id); return e && !e.classList.contains('hidden') ? e.textContent.replace(/\s+/g, ' ').trim() : null; };
                const werte = {};
                ['host', 'port', 'benutzer', 'passwort', 'absender-name', 'absender-adresse', 'signatur', 'verantwortlicher', 'datenschutz-link', 'aufbewahrung-tage'].forEach(id => { const e = doc.getElementById(id); if (e) werte[id] = e.value; });
                const fehler = {};
                doc.querySelectorAll('.field-error, .error').forEach(e => { if (e.id && !e.classList.contains('hidden') && e.textContent.trim()) fehler[e.id] = e.textContent.replace(/\s+/g, ' ').trim(); });
                const test = doc.getElementById('test-result');
                return {meldung: t('seite-meldung'), seite_fehler: t('seite-fehler'), fehler, werte, bcc: doc.getElementById('bcc-an-absender') ? doc.getElementById('bcc-an-absender').checked : null,
                    download: doc.getElementById('mitglied-download-link') ? doc.getElementById('mitglied-download-link').checked : null, passwort_hinweis: t('passwort-hinweis'),
                    rueckfrage: doc.getElementById('loeschen-rueckfrage') && !doc.getElementById('loeschen-rueckfrage').classList.contains('hidden'),
                    test: test && !test.classList.contains('hidden') ? {text: [...doc.querySelectorAll('#test-output > p')].map(p => p.textContent.replace(/\s+/g, ' ').trim()), technik: [...doc.querySelectorAll('#test-output dt')].map(dt => dt.textContent + '=' + dt.nextSibling.textContent), erfolg: !!doc.querySelector('#test-output .success-text')} : null};
            })() : null,
            vorgang: doc.getElementById('schrittleiste') ? (() => {
                const t = (id) => { const e = doc.getElementById(id); return e && !e.classList.contains('hidden') ? e.textContent.replace(/\s+/g, ' ').trim() : null; };
                const offen = (id) => { const e = doc.getElementById(id); return !!e && !e.classList.contains('hidden'); };
                const panel = ['pruefen', 'senden', 'status', 'abholen', 'geloescht'].filter(p => offen('panel-' + p));
                const hinweisBox = doc.getElementById('status-hinweis');
                return {
                    titel: doc.getElementById('seitentitel').textContent, seitentitel: doc.title, panel, leiste_sichtbar: offen('schrittleiste'),
                    schritte: [...doc.querySelectorAll('#schrittleiste li')].map(li => ({nr: li.dataset.schritt, klasse: li.className, marke: li.querySelector('b').textContent, aktuell: li.getAttribute('aria-current') === 'step'})),
                    meldung: t('meldung'), seite_fehler: offen('seite-fehler') ? {text: t('seite-fehler-text'), nochmal: offen('btn-neu-laden')} : null, laedt: offen('loading'),
                    pruefen: offen('panel-pruefen') ? {
                        pdf: doc.getElementById('pdf-viewer').getAttribute('src'), reiter: offen('dokument-reiter') ? [...doc.querySelectorAll('#dokument-reiter button')].map(b => ({text: b.textContent, gedrueckt: b.getAttribute('aria-pressed')})) : null,
                        dokument_links: [doc.getElementById('dokument-neues-fenster').getAttribute('href'), doc.getElementById('dokument-datei').getAttribute('href')], dokument_hinweis: t('dokument-hinweis'), name: t('name-wert'), name_hinweis: t('name-hinweis'), personen: doc.getElementById('personen').value, pers_hinweis: t('pers-hinweis'),
                        email: doc.getElementById('email').value, email_hinweis: t('email-hinweis'), geb: doc.getElementById('geburtsdatum').value, geb_platz: doc.getElementById('geburtsdatum').placeholder, geb_hinweis: t('geb-hinweis'),
                        abweichung: t('abweichung'), haken_text: t('angaben-stimmen-text'), haken: doc.getElementById('angaben-stimmen').checked,
                        fehler: {pers: t('pers-error'), email: t('email-error'), geb: t('geb-error'), haken: t('haken-error'), allgemein: t('pruefen-fehler')}, knopf: doc.getElementById('btn-weiter').textContent, knopf_gesperrt: doc.getElementById('btn-weiter').disabled} : null,
                    senden: offen('panel-senden') ? {
                        an: t('senden-an'), mailhinweis: t('senden-mailhinweis-text') && offen('senden-mailhinweis') ? t('senden-mailhinweis-text') : null, versenden_gesperrt: doc.getElementById('btn-versenden').disabled,
                        vor_ort: offen('vor-ort-karte'), versand_fehler: t('versand-error'), ohne_mail: offen('ohne-mail'), vor_ort_fehler: t('vor-ort-fehler')} : null,
                    status: offen('panel-status') ? {
                        ampel: ampelVon('status-ampel'),
                        hinweis: offen('status-hinweis') ? {klasse: hinweisBox.className.replace('hinweis ', '').trim(), text: t('status-hinweis-text'), aktion: offen('btn-status-aktion') ? doc.getElementById('btn-status-aktion').textContent : null} : null,
                        zeitstrahl: [...doc.querySelectorAll('#zeitstrahl li')].map(li => ({text: li.firstChild.textContent, zeit: li.querySelector('small') ? li.querySelector('small').textContent : null, offen: li.classList.contains('offen')})),
                        link_box: ergebnis('demo-link-box'),
                        dokumente: offen('status-dokumente-karte') ? [...doc.querySelectorAll('#status-dokumente li')].map(li => ({titel: li.querySelector('strong').textContent, stand: li.querySelector('span').textContent})) : null,
                        neuer_link: offen('btn-neuer-link'), erinnerungen: offen('erinnerungen-zeile') ? doc.getElementById('erinnerungen-aktiv').checked : null, loeschen: offen('btn-loeschen'),
                        rueckfrage: offen('aktion-rueckfrage') ? {text: t('aktion-rueckfrage-text'), ja: doc.getElementById('btn-rueckfrage-ja').textContent} : null, fehler: t('status-fehler')} : null,
                    abholen: offen('panel-abholen') ? {block_texte: [...doc.querySelectorAll('#abholen-mehrere .abholen-block')].map(k => k.querySelector('p').textContent), dokument_text: doc.getElementById('abholen-dokument-text').textContent, alles: {text: doc.getElementById('btn-alles-dl').textContent, gesperrt: doc.getElementById('btn-alles-dl').disabled, fortschritt: doc.getElementById('alles-fortschritt').classList.contains('hidden') ? null : doc.getElementById('alles-fortschritt').textContent, live: doc.getElementById('alles-fortschritt').getAttribute('aria-live'), rolle: doc.getElementById('alles-fortschritt').getAttribute('role')}, ampel: ampelVon('abholen-ampel'), abgeholt: [...doc.querySelectorAll('[data-abgeholt]')].map(p => [p.dataset.abgeholt, p.textContent]), abgeholt_einzeln: [doc.getElementById('abgeholt-dokument').textContent, doc.getElementById('abgeholt-nachweis').textContent], abrufe: zustand.abrufe.slice(), einzeln_sichtbar: offen('abholen-einzeln'), bloecke: [...doc.querySelectorAll('#abholen-mehrere .abholen-block')].map(k => ({rolle: k.dataset.rolle, titel: k.querySelector('h2').textContent, knoepfe: [...k.querySelectorAll('button')].map(b => b.textContent)})), hinweis: t('abholen-hinweis-text'), frist: t('abholen-frist'), fehler: t('abholen-fehler'), rueckfrage: offen('abholen-rueckfrage') ? t('abholen-rueckfrage-text') : null} : null,
                    geloescht: offen('panel-geloescht') ? t('geloescht-text') : null, downloads: zustand.downloads.slice()};
            })() : null,
            uebersicht: doc.getElementById('uebersicht') && sichtbar('uebersicht') ? {
                karten: [...doc.querySelectorAll('#karten .karte-zahl')].map(k => ({titel: k.querySelector('.karte-titel').textContent, zahl: k.querySelector('.karte-wert').textContent, gedrueckt: k.getAttribute('aria-pressed')})),
                filter: [...doc.querySelectorAll('#filter .filter-knopf')].map(f => ({text: f.textContent, gedrueckt: f.getAttribute('aria-pressed')})),
                meldung: sichtbar('liste-meldung') ? {text: text('liste-meldung'), rolle: doc.getElementById('liste-meldung').getAttribute('role'), fehler: doc.getElementById('liste-meldung').classList.contains('error')} : null,
                leer_hinweis: sichtbar('leer-hinweis'), listen_karte: sichtbar('listen-karte'),
                leer_in_liste: doc.querySelector('#vorgaenge-liste .liste-leer') ? doc.querySelector('#vorgaenge-liste .liste-leer').textContent : null,
                zeilen: [...doc.querySelectorAll('#vorgaenge-liste .zeile')].map(z => ({
                    id: z.dataset.id, ampel: z.querySelector('.ampel') ? [z.querySelector('.ampel').dataset.ampel, z.querySelector('.ampel-symbol').textContent, z.querySelector('.ampel-text').textContent] : null, ampel_zusatz: z.querySelector('.ampel-zeile + .zeile-sub') ? z.querySelector('.ampel-zeile + .zeile-sub').textContent : null, name: z.querySelector('.zeile-name').textContent, sub: z.querySelector('.zeile-sub').textContent,
                    pille: z.querySelector('.pille').textContent, pille_klasse: z.querySelector('.pille').className.replace('pille ', ''),
                    zusatz: z.querySelectorAll('.zeile > div:nth-child(2) .zeile-sub')[0] ? z.querySelectorAll('.zeile > div:nth-child(2) .zeile-sub')[0].textContent : null,
                    personen: z.querySelector('.zeile > div:nth-child(3)').textContent,
                    haupt: z.querySelector('.zeile-aktion > a, .zeile-aktion > button.btn') ? {text: z.querySelector('.zeile-aktion > a, .zeile-aktion > button.btn').textContent, href: z.querySelector('.zeile-aktion > a') ? z.querySelector('.zeile-aktion > a').getAttribute('href') : null,
                        primaer: z.querySelector('.zeile-aktion > a, .zeile-aktion > button.btn').classList.contains('btn-primary')} : null,
                    menue: [...z.querySelectorAll('.mehr-eintrag')].map(b => b.textContent),
                    rueckfrage: z.querySelector('.rueckfrage') ? z.querySelector('.rueckfrage p').textContent : null,
                    rueckfrage_knoepfe: [...z.querySelectorAll('.rueckfrage button')].map(b => b.textContent),
                    fehler: z.querySelector('.zeile-fehler') ? z.querySelector('.zeile-fehler').textContent : null})),
                downloads: zustand.downloads.slice()} : null,
            upload: doc.getElementById('upload-bereich') ? {
                feld_gesperrt: doc.getElementById('upload-feld').classList.contains('gesperrt'), eingabe_gesperrt: doc.getElementById('upload-datei').disabled,
                dateien: [...doc.querySelectorAll('#upload-dateien li > span')].map(s => s.textContent),
                auswahlbox: sichtbar('upload-auswahlbox'), hinweis: text('upload-hinweis'), fehler: sichtbar('upload-fehler') ? text('upload-fehler') : null,
                laeuft: sichtbar('upload-laeuft') ? {text: text('upload-laeuft-text'), wert: doc.getElementById('upload-fortschritt').getAttribute('value')} : null,
                ergebnis: sichtbar('upload-ergebnis') ? {text: [...doc.getElementById('upload-ergebnis').children].map(c => c.textContent.replace(/\s+/g, ' ').trim()).join(' '), weiter: doc.getElementById('upload-weiter') ? doc.getElementById('upload-weiter').getAttribute('href') : null} : null,
                drop_verhindert: zustand.dropVerhindert === true, drag_verhindert: zustand.dragStandardVerhindert === true} : null,
            banner_in_leiste: !!doc.querySelector('#hinweisleiste #demo-banner'),
            ueberschrift: doc.querySelector('h1') ? doc.querySelector('h1').textContent : null,
            banner: banner ? banner.textContent : null,
            testdaten_im_text: /Testdaten/.test(doc.body.textContent),
            demo_elemente: ['demo-bereich', 'demo-testmail', 'btn-demo-vorgang', 'btn-demo-testmail', 'demo-banner'].filter(id => !!doc.getElementById(id)).length,
            testversion_woerter: /\bDemo|Testdaten|Entwurf|ENTWURF|Platzhalter|erfunden|Musterstadt/.test(doc.body.textContent),
            banner_rolle: banner ? banner.getAttribute('role') : null,
            nur_heimnetz: doc.getElementById('nur-heimnetz') ? doc.getElementById('nur-heimnetz').textContent : null,
            demo_bereich: sichtbar('demo-bereich'),
            pdf_links: [...doc.querySelectorAll('#demo-bereich a')].map(a => ({href: a.getAttribute('href'), download: a.hasAttribute('download'), text: a.textContent.trim()})),
            demo_ergebnis: ergebnis('demo-ergebnis'),
            demo_fehler: sichtbar('demo-fehler') ? text('demo-fehler') : null,
            vorgang_karten: karten,
            testmail: doc.getElementById('demo-testmail') ? {
                sichtbar: sichtbar('demo-testmail'), info: text('demo-versand-info'),
                optionen: [...doc.querySelectorAll('#demo-testadresse option')].map(o => o.value),
                knopf_gesperrt: doc.getElementById('btn-demo-testmail').disabled,
                erfolg: sichtbar('demo-testmail-erfolg') ? text('demo-testmail-erfolg') : null,
                fehler: sichtbar('demo-testmail-fehler') ? text('demo-testmail-fehler') : null} : null,
            versand_box: ergebnis('demo-link-box'),
            ohne_mail: sichtbar('ohne-mail') ? {knopf_gesperrt: doc.getElementById('btn-ohne-mail').disabled} : null,
            versand_hinweise: doc.getElementById('versand-hinweise') ? doc.getElementById('versand-hinweise').textContent.trim() : null,
            versenden_gesperrt: doc.getElementById('btn-versenden') ? doc.getElementById('btn-versenden').disabled : null,
            mail_hinweise: sichtbar('mail-hinweise') ? [...doc.querySelectorAll('#mail-hinweise p')].map(p => p.textContent) : null,
            absender_fehler: sichtbar('absender-adresse-error') ? text('absender-adresse-error') : null,
            speichern_fehler: sichtbar('save-error') ? text('save-error') : null,
            versand_fehler: sichtbar('versand-error') ? text('versand-error') : null,
            container_text: doc.querySelector('.container') ? doc.querySelector('.container').textContent.replace(/\s+/g, ' ').trim().slice(0, 200) : null,
        };
    };
    const sicht = (vor) => {
        const qr = doc.getElementById('handy-qr');
        const knopf = (id) => { const b = doc.getElementById(id); return b ? {sichtbar: !b.classList.contains('hidden'), gesperrt: b.disabled, text: b.textContent.trim()} : null; };
        return {
            schritt: aktiverSchritt(),
            meldung: sichtbar('meldung') ? text('meldung') : null,
            meldung_art: doc.getElementById('meldung') ? doc.getElementById('meldung').className : null,
            neu_laden: sichtbar('btn-neu-laden'),
            person_info: sichtbar('person-info') ? text('person-info') : null,
            label: text('zeichenfeld-label'),
            hinweis_oben: text('unterschrift-hinweis'),
            hilfe: doc.querySelector('.zeichenfeld-hilfe') ? doc.querySelector('.zeichenfeld-hilfe').textContent.trim() : null,
            fehlt: sichtbar('zeichenfeld-fehlt') && !doc.getElementById('zeichenfeld-fehlt').classList.contains('unsichtbar') ? text('zeichenfeld-fehlt') : null,
            code_gueltig: doc.getElementById('code') ? doc.getElementById('code').checkValidity() : null, code_fehler: sichtbar('code-error') ? text('code-error') : null,
            fortschritt: sichtbar('dok-fortschritt') ? [...doc.querySelectorAll('#dok-fortschritt li')].map(li => ({klasse: li.className, marke: li.querySelector('b').textContent, text: li.querySelector('span').textContent, aktuell: li.getAttribute('aria-current') === 'step'})) : null,
            dokument_titel: sichtbar('dokument-titel') ? text('dokument-titel') : null, dokument_zeile: sichtbar('dokument-zeile') ? text('dokument-zeile') : null,
            handy_dokument: sichtbar('handy-dokument') ? text('handy-dokument') : null, texte_block_sichtbar: sichtbar('texte-block'),
            dokument_fertig: sichtbar('step-dokument-fertig') ? {titel: text('dokument-fertig-titel'), text: text('dokument-fertig-text'), knopf: text('btn-naechstes-dokument')} : null,
            dokument_wahl: sichtbar('dokument-wahl') ? [...doc.querySelectorAll('#dokument-wahl-knoepfe button')].map(b => b.textContent) : null,
            downloads_mitglied: zustand.downloads.slice(), seiten_bilder: doc.querySelectorAll('#seiten-container img').length,
            hinweis_text: text('hinweis-text'), datenschutz_text: text('datenschutz-text'), schritt_seiten_sichtbar: sichtbar('step-seiten'), schritt_unterschrift_sichtbar: sichtbar('step-unterschrift'),
            testversion_im_text: /ENTWURF|PLATZHALTER|Freigabe offen|nicht freigegeben/.test(doc.body.textContent),
            fehlt_platz: sichtbar('zeichenfeld-fehlt'),                       // Element nimmt weiter Platz ein (kein display:none)
            fehler_unterschrift: sichtbar('unterschrift-error') ? text('unterschrift-error') : null,
            fehler_code: sichtbar('code-error') ? text('code-error') : null,
            zeichenfeld_sichtbar: sichtbar('zeichenfeld'),
            handy_box: sichtbar('handy-box'),
            handy_status: text('handy-status'),
            handy_rest: text('handy-rest'),
            qr: qr ? {sichtbar: !qr.classList.contains('hidden'), tag: qr.tagName, src: qr.getAttribute('src') || null} : null,
            skripte_im_dom: doc.querySelectorAll('#app script').length,
            qr_ausgefuehrt: win.__x === 1,
            haken_an: doc.getElementById('check-datenschutz') ? doc.getElementById('check-datenschutz').checked : null,
            knoepfe: {handy: knopf('btn-handy'), handy_neu: knopf('btn-handy-neu'), loeschen: knopf('btn-clear'), unterschreiben: knopf('btn-unterschreiben'), uebertragen: knopf('btn-uebertragen')},
            fertig: sichtbar('step-fertig') ? text('step-fertig') : null,
            berater: berater(),
            zwischenablage: zwischenablage.slice(),
            hash: win.location.hash,
            pfad: win.location.pathname,
            alerts: alerts.slice(),
            zeichnung: Object.assign({}, zeichnung),
            requests: aufrufe.slice(vor).map(a => ({m: a.m, p: a.p, q: a.q, body: a.body, dateien: a.dateien})),
        };
    };

    const canvas = () => doc.getElementById('unterschrift-canvas');
    const Zeiger = win.PointerEvent || win.MouseEvent;
    const zeiger = (typ, x, y) => canvas().dispatchEvent(new Zeiger(typ, {clientX: x, clientY: y, pointerId: 1, pointerType: arg.zeigerart || 'touch', button: 0, bubbles: true, cancelable: true}));
    const klick = (id) => { doc.getElementById(id).click(); };

    out = {start: sicht(0), schritte: []};
    for (const op of arg.ops || []) {
        const vor = aufrufe.length;
        const warte = op.warte === undefined ? 400 : op.warte;
        if (op.op === 'code') {
            doc.getElementById('code').value = op.wert;
            doc.getElementById(op.form || 'form-code').dispatchEvent(new win.Event('submit', {bubbles: true, cancelable: true}));
        }
        if (op.op === 'seiten_geladen') {
            for (const img of doc.querySelectorAll('#seiten-container img')) img.dispatchEvent(new win.Event('load'));
        }
        if (op.op === 'gelesen') klick('btn-gelesen');
        if (op.op === 'haken') { doc.getElementById('check-datenschutz').checked = op.an !== false; }
        if (op.op === 'zeichne') {
            const pkt = op.punkte;
            zeiger('pointerdown', pkt[0][0], pkt[0][1]);
            for (const p of pkt.slice(1)) zeiger('pointermove', p[0], p[1]);
            zeiger('pointerup', pkt[pkt.length - 1][0], pkt[pkt.length - 1][1]);
        }
        if (op.op === 'tippen') { zeiger('pointerdown', op.x, op.y); zeiger('pointerup', op.x, op.y); }
        if (op.op === 'loeschen') klick('btn-clear');
        if (op.op === 'unterschreiben') klick('btn-unterschreiben');
        if (op.op === 'handy') klick('btn-handy');
        if (op.op === 'handy_neu') klick('btn-handy-neu');
        if (op.op === 'handy_abbrechen') klick('btn-handy-abbrechen');
        if (op.op === 'uebertragen') klick('btn-uebertragen');
        if (op.op === 'neu_laden_klick') klick('btn-neu-laden');
        if (op.op === 'klick_id') klick(op.id);
        if (op.op === 'klick_text') {                                          // Knopf/Eintrag mit genau diesem Text innerhalb der Auswahl
            const ziel = [...doc.querySelectorAll(op.sel)].find(e => e.textContent.trim() === op.text);
            if (!ziel) throw new Error('klick_text: nichts gefunden: ' + op.sel + ' / ' + op.text);
            ziel.click();
        }
        if (op.op === 'dateien' || op.op === 'ablegen') {
            const liste = op.dateien.map(spec => {
                const f = new win.File(['x'.repeat(Math.min(spec.size === undefined ? 10 : spec.size, 2000))], spec.name, {type: spec.type === undefined ? 'application/pdf' : spec.type});
                if (spec.size > 2000) Object.defineProperty(f, 'size', {value: spec.size});
                return f;
            });
            if (op.op === 'dateien') {
                const eingabe = doc.getElementById('upload-datei');
                Object.defineProperty(eingabe, 'files', {configurable: true, value: liste});
                eingabe.dispatchEvent(new win.Event('change', {bubbles: true}));
            } else {
                for (const name of ['dragenter', 'dragover']) { const e = new win.Event(name, {bubbles: true, cancelable: true}); doc.getElementById('upload-feld').dispatchEvent(e); zustand.dragStandardVerhindert = e.defaultPrevented; }
                const drop = new win.Event('drop', {bubbles: true, cancelable: true});
                drop.dataTransfer = {files: liste};
                doc.getElementById('upload-feld').dispatchEvent(drop);
                zustand.dropVerhindert = drop.defaultPrevented;
            }
        }
        if (op.op === 'wert_id') { const e = doc.getElementById(op.id); e.value = op.wert; e.dispatchEvent(new win.Event('input', {bubbles: true})); e.dispatchEvent(new win.Event('change', {bubbles: true})); }
        if (op.op === 'submit_id') doc.getElementById(op.id).dispatchEvent(new win.Event('submit', {bubbles: true, cancelable: true}));
        if (op.op === 'klick_sel') doc.querySelector(op.sel).click();
        if (op.op === 'haken_id') doc.getElementById(op.id).checked = op.an !== false;
        if (op.op === 'auswahl_id') { const e = doc.getElementById(op.id); e.value = op.wert; e.dispatchEvent(new win.Event('change', {bubbles: true})); }
        await sleep(warte);
        out.schritte.push({op, zustand: sicht(vor)});
    }
    out.fehler_js = fehler.slice(0, 5);
    return out;
}

let out;
(async () => {
    let ergebnis;
    try { ergebnis = await main(); ergebnis.ok = true; } catch (e) { ergebnis = {ok: false, fehler: String((e && e.stack) || e)}; }
    console.log('RESULT ' + JSON.stringify(ergebnis));
    if (!extern) server.close();
    process.exit(0);
})();
