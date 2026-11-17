// Treibt Webys SignNext-Oberfläche (ui/) in jsdom. Die echten Dateien aus ui/ werden von einem kleinen Testserver ausgeliefert; die Schnittstelle (/api/v1/...)
// spielt dieselbe Datei nach der Schnittstellenbeschreibung nach (Zugang, Handy-Übergabe, Handy-Seite, Demo). Eingabe: JSON-Argument, Ausgabe: eine Zeile RESULT {...}
const {JSDOM, VirtualConsole} = require('jsdom');
const http = require('http');
const fs = require('fs');
const path = require('path');

const arg = JSON.parse(process.argv[2]);
const UI = path.resolve(__dirname, '..', '..', 'ui');
const SEITEN = {'/u': 'unterschrift.html', '/h': 'handy.html', '/vorgaenge': 'vorgaenge.html', '/einstellungen': 'einstellungen.html', '/pruefung': 'pruefung.html', '/mail-zugang': 'mail-zugang.html'};
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const QR_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#000"/></svg>';
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
const zustand = {person: 1, unterschrieben: [], handoffAbfragen: 0, abgeholt: false, handoffNummer: 0, vorgaenge: [{id: 1, status: 'bereit', erstellt: '2026-11-17T10:00:00Z'}]};
const aufrufe = [];

function senden(res, status, obj, typ) {
    const roh = typ ? obj : JSON.stringify(obj);
    res.writeHead(status, {'Content-Type': typ || 'application/json'});
    res.end(roh);
}

function api(req, res, pfad, body) {
    const m = req.method;
    const fehler = (status, error, meldung, extra) => senden(res, status, Object.assign({error, meldung}, extra || {}));
    if (cfg.sitzungFehlt && pfad === cfg.sitzungFehlt) return fehler(401, 'sitzung_ungueltig', 'Sitzung ungültig');
    // Mitglied am Computer (Cookie sn_zugang)
    if (pfad === '/api/v1/zugang/hinweis') return senden(res, 200, {kennung: 'Kennung-1', zugangshinweis: 'Bitte Geburtsdatum eingeben.', freigegeben: true});
    if (pfad === '/api/v1/zugang/pruefen') return body.code === '010260' ? senden(res, 200, {ok: true}) : fehler(401, 'angaben_stimmen_nicht', 'falsch', {versuch: 1, von: 5, gesperrt: false});
    if (pfad === '/api/v1/zugang/status') {
        return senden(res, 200, {schritt: 'unterschreiben', seiten: 2, geliefert: 2, personen: cfg.personen, unterschrieben: zustand.unterschrieben,
            naechste_person: zustand.unterschrieben.length < cfg.personen ? zustand.unterschrieben.length + 1 : null});
    }
    if (pfad.startsWith('/api/v1/zugang/seite/')) {
        if (cfg.seitenFehler) return fehler(500, 'fehler', 'Serverfehler');
        res.writeHead(200, {'Content-Type': 'image/png'});
        return res.end(Buffer.from(PNG.split(',')[1], 'base64'));
    }
    if (pfad === '/api/v1/zugang/gelesen') return senden(res, 200, {ok: true});
    if (pfad === '/api/v1/zugang/texte') return senden(res, 200, {hash: 'h1', freigegeben: true, texte: {datenschutz: 'Datenschutz', unterschriftssatz: 'Satz', haken: 'Ich habe gelesen'}});
    if (pfad === '/api/v1/zugang/unterschrift') {
        if (cfg.unterschriftFehler) return fehler(cfg.unterschriftFehler.status, cfg.unterschriftFehler.error, cfg.unterschriftFehler.meldung);
        zustand.unterschrieben.push(body.person);
        return body.person >= cfg.personen ? senden(res, 200, {fertig: true}) : senden(res, 200, {fertig: false, naechste_person: body.person + 1});
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
    if (pfad === '/api/v1/vorgaenge' && m === 'GET') return senden(res, 200, {vorgaenge: zustand.vorgaenge});
    if (pfad === '/api/v1/demo' && m === 'GET') return senden(res, 200, cfg.demoInfo);
    if (pfad === '/api/v1/demo/vorgang' && m === 'POST') {
        const id = 7 + zustand.vorgaenge.length;
        zustand.vorgaenge.push({id, status: 'bereit', erstellt: '2026-11-17T11:00:00Z'});
        return senden(res, 200, {vorgang_id: id, link: 'https://sn.example/u#demo' + id, geburtsdatum: '01.02.1960', code: '010260', personen: 2, qr_svg: cfg.qrSvg});
    }
    if (pfad === '/api/v1/demo/testmail' && m === 'POST') {
        if (cfg.testmailFehler) return fehler(cfg.testmailFehler.status, cfg.testmailFehler.error, cfg.testmailFehler.meldung);
        return senden(res, 200, {ergebnis: 'gesendet'});
    }
    if (pfad === '/api/v1/einstellungen') return senden(res, 200, {verantwortlicher: 'Beratungsstelle Beispiel, Musterstr. 1', datenschutz_link: 'https://beispiel.example/datenschutz', aufbewahrung_tage: 90, mitglied_download_link: false});
    if (pfad === '/api/v1/mail-zugang' && m === 'GET') return senden(res, 200, cfg.mailZugang);
    if (pfad === '/api/v1/mail-zugang' && m === 'PUT') return senden(res, cfg.mailPut.status, cfg.mailPut.body);
    if (/^\/api\/v1\/vorgaenge\/\d+\/pruefung$/.test(pfad)) {
        return senden(res, 200, {status: 'bereit', name: {wert: 'Erika Muster', herkunft: 'protokoll'}, geburtsdatum: {gesetzt: true, wert: '01.02.60', herkunft: 'gesetzt'},
            email: {wert: 'erika@example.org', herkunft: 'manuell'}, personen: {wert: 1, herkunft: 'protokoll'}, versandbereit_pdf: true, fehlend: [], erinnerungen_aus: false});
    }
    if (/^\/api\/v1\/vorgaenge\/\d+\/dokument$/.test(pfad)) { res.writeHead(200, {'Content-Type': 'application/pdf'}); return res.end('%PDF-1.4'); }
    if (/^\/api\/v1\/vorgaenge\/\d+\/versenden$/.test(pfad) && m === 'POST') { const v = body.ohne_mail ? cfg.versendenOhneMail : cfg.versenden; return senden(res, v.status, v.body); }
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
            aufrufe.push({m: req.method, p: pfad, body});
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
        const karten = doc.querySelectorAll('#vorgaenge-liste .card').length;
        return {
            banner: banner ? banner.textContent : null,
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
            fehlt: sichtbar('zeichenfeld-fehlt') ? text('zeichenfeld-fehlt') : null,
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
            requests: aufrufe.slice(vor).map(a => ({m: a.m, p: a.p, body: a.body})),
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
            doc.getElementById('form-code').dispatchEvent(new win.Event('submit', {bubbles: true, cancelable: true}));
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
