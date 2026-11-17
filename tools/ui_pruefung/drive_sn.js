// Treibt Webys SignNext-Oberfläche (ui/) in jsdom. Die echten Dateien aus ui/ werden von einem kleinen Testserver ausgeliefert; die Schnittstelle (/api/v1/...)
// spielt dieselbe Datei nach der Schnittstellenbeschreibung nach (Zugang, Handy-Übergabe, Handy-Seite, Demo). Eingabe: JSON-Argument, Ausgabe: eine Zeile RESULT {...}
const {JSDOM, VirtualConsole} = require('jsdom');
const http = require('http');
const fs = require('fs');
const path = require('path');

const arg = JSON.parse(process.argv[2]);
const UI = path.resolve(__dirname, '..', '..', 'ui');
const SEITEN = {'/u': 'unterschrift.html', '/h': 'handy.html'};
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const QR_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#000"/></svg>';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

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
}, arg.api || {});
const zustand = {person: 1, unterschrieben: [], handoffAbfragen: 0, abgeholt: false, handoffNummer: 0};
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
    await new Promise(r => server.listen(0, '127.0.0.1', r));
    const BASE = `http://127.0.0.1:${server.address().port}`;
    const alerts = [];
    const zeichnung = {striche: 0, geleert: 0, bilder: 0, stil: null, capture: 0};
    const fehler = [];
    const vc = new VirtualConsole();
    vc.on('jsdomError', (e) => fehler.push('jsdomError: ' + ((e.detail && e.detail.message) || e.message)));
    const dom = await JSDOM.fromURL(BASE + arg.seite + (arg.fragment || ''), {
        runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
        beforeParse(win) {
            win.fetch = async (u, o = {}) => {
                const url = new URL(u, BASE);
                return fetch(url, {method: o.method || 'GET', headers: o.headers, body: o.body});
            };
            win.alert = (t) => { alerts.push(String(t)); };
            win.confirm = () => true;
            const echt = win.setTimeout.bind(win);
            win.setTimeout = (f, ms, ...rest) => echt(f, ms >= 1000 ? 25 : ms, ...rest);              // die Handy-Abfrage (2 s) läuft im Test schnell
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
    server.close();
    process.exit(0);
})();
