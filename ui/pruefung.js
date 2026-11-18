// SignNext Vorgang der Beratungsstelle: Angaben prüfen, an das Mitglied senden, Stand sehen, Dokumente abholen (eine Seite mit Schrittleiste) - CSP 'self' konform.
// Welcher Schritt erscheint, ergibt sich aus dem Stand des Vorgangs; alle Texte sind für die Beratungsstelle geschrieben, keine Statusnamen aus dem Code.
'use strict';

const ALLGEMEINER_FEHLER = 'Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.';
const ID_MUSTER = /^\d{1,9}$/;
const vorgangId = (() => {
    const roh = new URLSearchParams(window.location.search).get('id') || '';
    return ID_MUSTER.test(roh) ? roh : null;
})();

let vorgang = null;                 // GET /vorgaenge/<id>
let pruefdaten = null;              // GET /vorgaenge/<id>/pruefung (nur solange Angaben geändert werden können)
let mailZugang = null;
let istDemo = false;
let linkBox = null;                 // Link/QR aus dem Demo (Antwort von versenden)
let rueckfrageJa = null;
const DOK_TITEL = {B: 'Anschreiben', A: 'Protokoll'};
const FEHLEND_TEXT = {email: 'E-Mail', geburtsdatum: 'Geburtsdatum', personen: 'Zahl der Unterschriften'};
const KRITERIEN = {steuernummer: 'Steuernummer', steuer_id: 'Steuer-ID', mitgliedsnummer: 'Mitgliedsnummer'};
const el = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', async () => {
    if (!(await Berater.bereit())) return;
    istDemo = await Berater.istDemo();
    bindeEreignisse();
    if (!vorgangId) {
        seiteFehler('Es wurde kein Vorgang ausgewählt. Bitte öffnen Sie ihn über die Liste Ihrer Vorgänge.', false);
        return;
    }
    await laden('pruefen');
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
    return res.json();
}

// Dokumente des Vorgangs in der Reihenfolge wie beim Mitglied: erst das Anschreiben (B), dann das Protokoll (A).
// dokumente_fortschritt kommt vom Server; ohne dieses Feld (ein Dokument, älterer Stand) bleibt alles wie bisher.
function dokumenteDesVorgangs() {
    const f = vorgang && Array.isArray(vorgang.dokumente_fortschritt) ? vorgang.dokumente_fortschritt : null;
    if (f && f.length) {
        return f.map(d => ({rolle: d.rolle, titel: d.titel || DOK_TITEL[d.rolle] || String(d.rolle), erforderlich: d.erforderlich || [], unterschrieben: d.unterschrieben || [], fertig: !!d.fertig, abgeholt: d.abgeholt_am || d.dokument_abgeholt_am || null, nachweisAbgeholt: d.nachweis_abgeholt_am || null, dateiname: d.dateiname || null, nachweisDateiname: d.nachweis_dateiname || null}));
    }
    const rollen = ((vorgang && vorgang.dokumente) || []).map(d => d.rolle);
    return ['B', 'A'].filter(r => rollen.includes(r)).map(r => ({rolle: r, titel: DOK_TITEL[r], erforderlich: [], unterschrieben: [], fertig: false, abgeholt: null, nachweisAbgeholt: null, dateiname: null, nachweisDateiname: null}));
}

// Ampel des Vorgangs (rot/gelb/gruen vom Server); ohne Wert (null = noch nicht versendet) bleibt die Stelle leer
function ampelZeigen(id) {
    const stelle = el(id);
    stelle.textContent = '';
    const a = Berater.ampelBaustein(vorgang && vorgang.ampel);
    if (a) stelle.appendChild(a);
    stelle.classList.toggle('hidden', !a);
}

function abgeholtText(wann) {
    return wann ? `abgeholt am ${datumUhr(wann)}` : 'noch nicht abgeholt';
}

function mehrereDokumente() {
    return dokumenteDesVorgangs().length > 1;
}

function dokumentAdresse(rolle) {
    return `/api/v1/vorgaenge/${vorgangId}/dokument${rolle ? '?dokument=' + encodeURIComponent(rolle) : ''}`;
}

function name() {
    return vorgang && typeof vorgang.name === 'string' && vorgang.name ? vorgang.name : `Vorgang ${vorgangId}`;
}

function datumTag(iso) {
    const d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleDateString('de-DE', {day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Europe/Berlin'});
}

function datumUhr(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return `${datumTag(iso)}, ${d.toLocaleTimeString('de-DE', {hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin'})}`;
}

function zeigeFehler(id, text) {
    const box = el(id);
    box.textContent = text || '';
    box.classList.toggle('hidden', !text);
}

function meldung(text) {
    const box = el('meldung');
    box.textContent = text || '';
    box.classList.toggle('hidden', !text);
}

function seiteFehler(text, nochmal) {
    el('loading').classList.add('hidden');
    el('vorgang').classList.add('hidden');
    el('seite-fehler').classList.remove('hidden');
    el('seite-fehler-text').textContent = text;
    el('btn-neu-laden').classList.toggle('hidden', !nochmal);
    el('seitentitel').textContent = 'Vorgang';
}

// ---------- Laden und Schrittwahl ----------

async function laden(wunsch) {
    meldung('');
    el('loading').classList.remove('hidden');
    try {
        vorgang = await api('GET', `/vorgaenge/${vorgangId}`);
        pruefdaten = ['bereit', 'bestaetigung_noetig'].includes(vorgang.status) ? await api('GET', `/vorgaenge/${vorgangId}/pruefung`) : null;
    } catch (err) {
        if (err.status === 404) seiteFehler('Dieser Vorgang wurde nicht gefunden. Vielleicht wurde er gelöscht.', false);
        else seiteFehler(err.message, true);
        return;
    }
    el('seite-fehler').classList.add('hidden');
    el('loading').classList.add('hidden');
    el('vorgang').classList.remove('hidden');
    await zeigeSchritt(wunsch);
}

function schrittleiste(aktuell) {
    document.querySelectorAll('#schrittleiste li').forEach(li => {
        const nr = Number(li.dataset.schritt);
        li.className = nr < aktuell ? 'fertig' : (nr === aktuell ? 'jetzt' : '');
        li.querySelector('b').textContent = nr < aktuell ? '✓' : String(nr);
        if (nr === aktuell) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
    });
}

function panel(welches, titel, schritt) {
    ['pruefen', 'senden', 'status', 'abholen', 'geloescht'].forEach(p => el('panel-' + p).classList.toggle('hidden', p !== welches));
    el('seitentitel').textContent = titel;
    document.title = `${titel} - SignNext`;
    schrittleiste(schritt);
    el('schrittleiste').classList.toggle('hidden', welches === 'geloescht');
    zeigeFehler('status-fehler', '');
    zeigeFehler('abholen-fehler', '');
    el('aktion-rueckfrage').classList.add('hidden');
    el('abholen-rueckfrage').classList.add('hidden');
}

async function zeigeSchritt(wunsch) {
    const status = vorgang.status;
    if (status === 'geloescht') {
        zeigeGeloescht('Dieser Vorgang wurde gelöscht.');
    } else if (['bereit', 'bestaetigung_noetig'].includes(status)) {
        if (wunsch === 'senden' && status === 'bereit' && pruefdaten && pruefdaten.fehlend.length === 0) await zeigeSenden();
        else zeigePruefen();
    } else if (status === 'abgeschlossen') {
        zeigeAbholen();
    } else {
        zeigeStatus();
    }
}

// ---------- Schritt 2: Angaben prüfen ----------

const HERKUNFT_TEXT = {
    email: {pdf: 'Aus dem Anschreiben übernommen.', manuell: 'Von Ihnen eingetragen.', abweichend: 'Anschreiben und Protokoll nennen unterschiedliche Adressen – bitte prüfen.', fehlt: 'Fehlt noch – bitte eintragen.'},
    personen: {pdf: 'Aus dem Steuerprotokoll erkannt.', manuell: 'Von Ihnen festgelegt.', abweichend: 'Bitte prüfen.', fehlt: 'Bitte wählen.'}
};

function zeigePruefen() {
    panel('pruefen', 'Angaben prüfen', 2);
    const d = pruefdaten;
    vorschauZeigen();
    el('name-wert').textContent = d.name && d.name.wert ? d.name.wert : 'Der Name konnte nicht gelesen werden.';
    el('name-hinweis').textContent = `Vorgang: ${name()}`;
    el('personen').value = d.personen && d.personen.wert ? String(d.personen.wert) : '';
    el('pers-hinweis').textContent = HERKUNFT_TEXT.personen[d.personen && d.personen.herkunft] || '';
    el('email').value = d.email && d.email.wert ? d.email.wert : '';
    el('email-hinweis').textContent = HERKUNFT_TEXT.email[d.email && d.email.herkunft] || (d.email && d.email.wert ? '' : HERKUNFT_TEXT.email.fehlt);
    const g = d.geburtsdatum || {};
    el('geburtsdatum').value = g.wert || '';
    el('geburtsdatum').placeholder = g.gesetzt && !g.wert ? 'eingetragen – zum Ändern neu eingeben' : 'TT.MM.JJJJ';
    el('geb-hinweis').textContent = g.gesetzt
        ? (g.wert ? 'Aus dem Protokoll übernommen.' : 'Von Ihnen eingetragen (aus Datenschutzgründen nicht angezeigt). Zum Ändern neu eintragen.')
        : 'Fehlt noch – bitte eintragen.';
    ['pers-error', 'email-error', 'geb-error', 'haken-error'].forEach(i => zeigeFehler(i, ''));
    zeigeFehler('pruefen-fehler', '');
    el('angaben-stimmen').checked = false;
    const abweichend = vorgang.status === 'bestaetigung_noetig';
    el('abweichung').classList.toggle('hidden', !abweichend);
    el('angaben-stimmen-text').textContent = abweichend ? 'Ja, die Angaben stimmen und beide Dokumente gehören zu diesem Mitglied.' : 'Ja, die Angaben stimmen.';
    if (abweichend) {
        const liste = ((d.abgleich && d.abgleich.abweichend) || []).map(k => KRITERIEN[k]).filter(Boolean);
        el('abweichung-text').textContent = liste.length
            ? `Bitte kurz prüfen: Anschreiben und Steuerprotokoll weichen bei „${liste.join('“ und „')}“ voneinander ab. Gehören beide zu demselben Mitglied?`
            : 'Bitte kurz prüfen: Anschreiben und Steuerprotokoll lassen sich nicht sicher demselben Mitglied zuordnen. Gehören beide zusammen?';
    }
    el('btn-weiter').disabled = false;
}

// Bei mehreren Dokumenten gibt es für jedes Dokument eine eigene Vorschau (Reiter); sonst eine Vorschau wie bisher
function vorschauZeigen(rolle) {
    const dok = dokumenteDesVorgangs();
    const reiter = el('dokument-reiter');
    reiter.textContent = '';
    if (dok.length < 2) {
        reiter.classList.add('hidden');
        el('dokument-hinweis').textContent = 'Vorn steht das Anschreiben, dahinter das Steuerprotokoll mit den Unterschriftsstellen.';
        const adresse = dokumentAdresse(null);
        el('pdf-viewer').src = adresse;
        el('dokument-neues-fenster').href = adresse;
        el('dokument-datei').href = adresse;
        return;
    }
    const gewaehlt = rolle || dok[0].rolle;
    el('dokument-hinweis').textContent = 'Jedes Dokument wird einzeln unterschrieben. Wählen Sie oben das Dokument, das Sie ansehen möchten.';
    dok.forEach(d => {
        const knopf = document.createElement('button');
        knopf.type = 'button';
        knopf.textContent = d.titel;
        knopf.dataset.rolle = d.rolle;
        knopf.setAttribute('aria-pressed', String(d.rolle === gewaehlt));
        knopf.addEventListener('click', () => vorschauZeigen(d.rolle));
        reiter.appendChild(knopf);
    });
    reiter.classList.remove('hidden');
    const adresse = dokumentAdresse(gewaehlt);
    el('pdf-viewer').src = adresse;
    el('dokument-neues-fenster').href = adresse;
    el('dokument-datei').href = adresse;
}

function pruefeEingabe() {
    let ok = true;
    const d = pruefdaten;
    const fehler = (id, text) => { zeigeFehler(id, text); ok = false; };
    ['pers-error', 'email-error', 'geb-error', 'haken-error'].forEach(i => zeigeFehler(i, ''));
    if (!['1', '2'].includes(el('personen').value)) fehler('pers-error', 'Bitte wählen Sie, wer unterschreibt.');
    const mail = el('email').value.trim();
    if (!mail) fehler('email-error', 'Bitte tragen Sie die E-Mail-Adresse des Mitglieds ein.');
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(mail)) fehler('email-error', 'Das ist keine gültige E-Mail-Adresse.');
    const geb = el('geburtsdatum').value.trim();
    if (!geb && !(d.geburtsdatum && d.geburtsdatum.gesetzt)) fehler('geb-error', 'Bitte tragen Sie das Geburtsdatum ein (TT.MM.JJJJ).');
    else if (geb && !/^\d{2}\.\d{2}\.\d{4}$/.test(geb)) fehler('geb-error', 'Bitte im Format TT.MM.JJJJ eingeben, zum Beispiel 01.02.1960.');
    if (!el('angaben-stimmen').checked) fehler('haken-error', 'Bitte bestätigen Sie, dass die Angaben stimmen.');
    return ok;
}

function geaenderteAngaben() {
    const d = pruefdaten;
    const body = {};
    const mail = el('email').value.trim();
    if (mail !== ((d.email && d.email.wert) || '')) body.email = mail;
    const geb = el('geburtsdatum').value.trim();
    if (geb && geb !== ((d.geburtsdatum && d.geburtsdatum.wert) || '')) body.geburtsdatum = geb;
    const personen = Number(el('personen').value);
    if (personen !== ((d.personen && d.personen.wert) || 0)) body.personen = personen;
    return body;
}

async function angabenWeiter(e) {
    e.preventDefault();
    zeigeFehler('pruefen-fehler', '');
    if (!pruefeEingabe()) return;
    const knopf = el('btn-weiter');
    knopf.disabled = true;
    try {
        const aenderung = geaenderteAngaben();
        if (Object.keys(aenderung).length) await api('PUT', `/vorgaenge/${vorgangId}/angaben`, aenderung);
        if (vorgang.status === 'bestaetigung_noetig') await api('POST', `/vorgaenge/${vorgangId}/abgleich-bestaetigen`, {bestaetigt: true});
        vorgang = await api('GET', `/vorgaenge/${vorgangId}`);
        pruefdaten = ['bereit', 'bestaetigung_noetig'].includes(vorgang.status) ? await api('GET', `/vorgaenge/${vorgangId}/pruefung`) : null;
        if (pruefdaten && pruefdaten.fehlend.length) {
            zeigePruefen();
            zeigeFehler('pruefen-fehler', 'Es fehlt noch: ' + pruefdaten.fehlend.map(f => FEHLEND_TEXT[f] || f).join(', ') + '.');
            return;
        }
        await zeigeSchritt('senden');
    } catch (err) {
        const feld = {email: 'email-error', geburtsdatum: 'geb-error', personen: 'pers-error'}[err.daten && err.daten.feld];
        if (feld) zeigeFehler(feld, err.message);
        else zeigeFehler('pruefen-fehler', err.message);
    } finally {
        knopf.disabled = false;
    }
}

// ---------- Schritt 3: Senden ----------

async function zeigeSenden() {
    panel('senden', 'An das Mitglied senden', 3);
    el('senden-an').textContent = (pruefdaten.email && pruefdaten.email.wert) || '';
    zeigeFehler('versand-error', '');
    zeigeFehler('vor-ort-fehler', '');
    el('ohne-mail').classList.add('hidden');
    el('vor-ort-karte').classList.toggle('hidden', !istDemo);
    try {
        mailZugang = await api('GET', '/mail-zugang');
    } catch (err) {
        mailZugang = null;
    }
    let hinweis = '';
    if (mailZugang && mailZugang.konfiguriert === false) hinweis = 'Ihr Mail-Zugang fehlt noch. Richten Sie ihn ein, dann können Sie den Link per E-Mail senden.';
    else if (mailZugang && mailZugang.konfiguriert === true && !mailZugang.geprueft_am) hinweis = 'Ihr Mail-Zugang ist noch nicht geprüft. Senden Sie zuerst eine Testmail an sich selbst.';
    el('senden-mailhinweis-text').textContent = hinweis;
    el('senden-mailhinweis').classList.toggle('hidden', !hinweis);
    el('btn-versenden').disabled = !!hinweis;
}

async function nachVersand(text, antwort) {
    linkBox = antwort && antwort.demo ? antwort.demo : null;
    await laden('status');
    meldung(text);
}

async function versenden() {
    zeigeFehler('versand-error', '');
    el('ohne-mail').classList.add('hidden');
    const knopf = el('btn-versenden');
    knopf.disabled = true;
    try {
        const r = await api('POST', `/vorgaenge/${vorgangId}/versenden`, {bestaetigt: true});
        const an = (pruefdaten && pruefdaten.email && pruefdaten.email.wert) || 'das Mitglied';
        await nachVersand(`Der Link wurde per E-Mail an ${an} gesendet.`, r);
    } catch (err) {
        zeigeFehler('versand-error', err.message);
        el('ohne-mail').classList.toggle('hidden', !(err.daten && err.daten.fallback === 'ohne_mail'));
        knopf.disabled = false;
    }
}

async function ohneMail(fehlerId, knopfId) {
    zeigeFehler(fehlerId, '');
    const knopf = el(knopfId);
    knopf.disabled = true;
    try {
        const r = await api('POST', `/vorgaenge/${vorgangId}/versenden`, {bestaetigt: true, ohne_mail: true});
        await nachVersand('Der Vorgang ist freigegeben, ohne E-Mail. Bitte geben Sie den Link unten selbst an das Mitglied weiter.', r);
    } catch (err) {
        zeigeFehler(fehlerId, err.message);
        knopf.disabled = false;
    }
}

// ---------- Schritt 3 danach: Stand ----------

const STATUS_TITEL = {
    versenden: 'Wird gesendet', versendet: 'Wartet auf Unterschrift', versand_unklar: 'Versand unklar', unterschrieben: 'Unterschrieben', abschluss_laeuft: 'Unterschrieben',
    abschluss_fehlgeschlagen: 'Fertigstellung hakt', neu: 'Wird ausgewertet'
};

function teilweiseUnterschrieben() {
    const dok = dokumenteDesVorgangs();
    return dok.length > 1 && dok.some(d => d.fertig) && !dok.every(d => d.fertig);
}

function statusHinweis() {
    const s = vorgang.status;
    const l = vorgang.link_status;
    if (s === 'versendet' && l === 'abgelaufen') return {art: 'gelb', aktion: 'Neuen Link senden', text: 'Der Link ist abgelaufen. Das Mitglied hat nicht rechtzeitig unterschrieben. Mit einem neuen Link kann es noch einmal versuchen – der alte funktioniert dann nicht mehr.'};
    if (s === 'versendet' && l === 'gesperrt') return {art: 'gelb', aktion: 'Neuen Link senden', text: 'Der Link ist gesperrt, weil das Geburtsdatum zu oft falsch eingegeben wurde. Mit einem neuen Link kann das Mitglied es noch einmal versuchen.'};
    if (s === 'versand_unklar') return {art: 'rot', aktion: 'Neuen Link senden', text: 'Wir wissen nicht, ob die E-Mail angekommen ist. Bitte schauen Sie in Ihrem Postfach unter „Gesendet“ nach. Erst wenn sie dort fehlt, senden Sie einen neuen Link.'};
    if (s === 'versendet' && teilweiseUnterschrieben()) {
        const dok = dokumenteDesVorgangs();
        const fertig = dok.filter(d => d.fertig).map(d => d.titel).join(' und ');
        const offen = dok.filter(d => !d.fertig).map(d => d.titel).join(' und ');
        return {art: 'blau', text: `${fertig} ${dok.filter(d => d.fertig).length > 1 ? 'sind' : 'ist'} unterschrieben, ${offen} fehlt noch. Das Mitglied kann mit demselben Link weitermachen.`};
    }
    if (s === 'unterschrieben' || s === 'abschluss_laeuft') return {art: 'blau', text: 'Die Unterschrift ist eingegangen. Das Dokument wird gerade fertiggestellt – das dauert meist nur einen Moment. Mit „Stand aktualisieren“ sehen Sie, ob es fertig ist.'};
    if (s === 'abschluss_fehlgeschlagen') return {art: 'rot', text: 'Die Fertigstellung hat nicht geklappt. Das System versucht es automatisch noch einmal. Bitte schauen Sie später wieder vorbei.'};
    if (s === 'versenden') return {art: 'blau', text: 'Der Link wird gerade gesendet …'};
    if (s === 'neu') return {art: 'blau', text: 'Der Vorgang wird noch ausgewertet. Bitte aktualisieren Sie in einem Moment.'};
    return null;
}

function zeigeStatus() {
    const s = vorgang.status;
    panel('status', `${name()} · ${s === 'versendet' && teilweiseUnterschrieben() ? 'Teilweise unterschrieben' : (STATUS_TITEL[s] || String(s).replace(/_/g, ' '))}`, 3);
    const h = statusHinweis();
    const box = el('status-hinweis');
    box.classList.toggle('hidden', !h);
    box.className = 'hinweis' + (h ? ' hinweis-' + h.art : '') + (h ? '' : ' hidden');
    if (h) {
        el('status-hinweis-text').textContent = h.text;
        const knopf = el('btn-status-aktion');
        knopf.classList.toggle('hidden', !h.aktion);
        knopf.textContent = h.aktion || '';
    }
    // Link und QR aus dem Demo (nur direkt nach dem Versand)
    const lb = el('demo-link-box');
    if (linkBox) {
        Berater.linkBoxZeigen(lb, {titel: linkBox.ohne_mail ? 'Zugangslink am Bildschirm (ohne E-Mail)' : 'Zugangslink am Bildschirm', link: linkBox.link, qr_svg: linkBox.qr_svg, hinweis: linkBox.geburtsdatum_hinweis});
        lb.classList.remove('hidden');
    } else {
        lb.classList.add('hidden');
        lb.textContent = '';
    }
    ampelZeigen('status-ampel');
    zeichneZeitstrahl();
    dokumenteKarteZeigen();
    const nachfassen = s === 'versendet';
    el('btn-neuer-link').classList.toggle('hidden', !(nachfassen && !(h && h.aktion)));
    el('erinnerungen-zeile').classList.toggle('hidden', !nachfassen);
    el('erinnerungen-aktiv').checked = !vorgang.erinnerungen_aus;
    el('btn-loeschen').classList.toggle('hidden', ['versenden', 'abschluss_laeuft'].includes(s));
    zeigeFehler('status-fehler', '');
}

// "noch N Tage", sobald der Server die Ablaufzeit mitliefert; sonst nur "gültig" (nichts raten)
function linkGueltig() {
    const ab = vorgang.link_laeuft_ab ? new Date(vorgang.link_laeuft_ab).getTime() : NaN;
    if (isNaN(ab)) return 'Der Link ist gültig.';
    const tage = Math.ceil((ab - Date.now()) / (24 * 60 * 60 * 1000));
    if (tage > 1) return `Der Link ist noch ${tage} Tage gültig.`;
    return tage === 1 ? 'Der Link ist noch 1 Tag gültig.' : 'Der Link läuft heute ab.';
}

// Karte "Dokumente": Stand je Dokument (nur bei mehreren Dokumenten)
function dokumenteKarteZeigen() {
    const dok = dokumenteDesVorgangs();
    const karte = el('status-dokumente-karte');
    const liste = el('status-dokumente');
    liste.textContent = '';
    if (dok.length < 2) {
        karte.classList.add('hidden');
        return;
    }
    dok.forEach(d => {
        const li = document.createElement('li');
        const titel = document.createElement('strong');
        titel.textContent = d.titel;
        const stand = document.createElement('span');
        const noetig = d.erforderlich.length;
        const klammer = noetig ? ` (${d.unterschrieben.length} von ${noetig} ${noetig === 1 ? 'Unterschrift' : 'Unterschriften'})` : '';
        stand.textContent = (d.fertig ? '✓ unterschrieben' : (d.unterschrieben.length ? 'wartet auf weitere Unterschrift' : 'wartet auf Unterschrift')) + klammer;
        li.append(titel, stand);
        liste.appendChild(li);
    });
    karte.classList.remove('hidden');
}

function zeichneZeitstrahl() {
    const liste = el('zeitstrahl');
    liste.textContent = '';
    const punkt = (text, zeit, offen) => {
        const li = document.createElement('li');
        if (offen) li.className = 'offen';
        const t = document.createElement(offen ? 'strong' : 'span');
        t.textContent = text;
        li.appendChild(t);
        if (zeit) {
            const small = document.createElement('small');
            small.textContent = zeit;
            li.appendChild(small);
        }
        liste.appendChild(li);
    };
    punkt('Hochgeladen', datumUhr(vorgang.erstellt));
    if (vorgang.bestaetigt_am) punkt('Angaben bestätigt', datumUhr(vorgang.bestaetigt_am));
    if (vorgang.versendet_am) punkt('Link an das Mitglied gesendet', datumUhr(vorgang.versendet_am));
    if (vorgang.unterschrieben_am) punkt(vorgang.personen === 2 ? 'Beide haben unterschrieben' : 'Unterschrieben', datumUhr(vorgang.unterschrieben_am));
    if (vorgang.abgeschlossen_am) punkt('Fertiggestellt', datumUhr(vorgang.abgeschlossen_am));
    const s = vorgang.status;
    const l = vorgang.link_status;
    if (s === 'versendet') punkt('Wartet auf die Unterschrift', l === 'aktiv' ? linkGueltig() : (l === 'abgelaufen' ? 'Der Link ist abgelaufen.' : (l === 'gesperrt' ? 'Der Link ist gesperrt.' : '')), true);
    else if (s === 'versand_unklar') punkt('Versand unklar', 'Bitte im Postfach nachsehen.', true);
    else if (s === 'unterschrieben' || s === 'abschluss_laeuft') punkt('Wird fertiggestellt', '', true);
    else if (s === 'abschluss_fehlgeschlagen') punkt('Fertigstellung hakt', 'Das System versucht es erneut.', true);
}

function frage(text, jaText, jaFunktion, gefahr, ziel) {
    const box = el(ziel === 'abholen' ? 'abholen-rueckfrage' : 'aktion-rueckfrage');
    el(ziel === 'abholen' ? 'abholen-rueckfrage-text' : 'aktion-rueckfrage-text').textContent = text;
    const ja = el(ziel === 'abholen' ? 'btn-abholen-ja' : 'btn-rueckfrage-ja');
    ja.textContent = jaText;
    ja.className = 'btn btn-klein ' + (gefahr ? 'btn-gefahr' : 'btn-primary');
    ja.disabled = false;
    rueckfrageJa = jaFunktion;
    box.classList.remove('hidden');
}

async function neuerLink() {
    try {
        await api('POST', `/vorgaenge/${vorgangId}/neuer-link`, {bestaetigt: true});
        linkBox = null;
        await laden('status');
        meldung('Ein neuer Link wurde per E-Mail an das Mitglied gesendet.');
    } catch (err) {
        el('aktion-rueckfrage').classList.add('hidden');
        zeigeFehler('status-fehler', err.message);
    }
}

async function loeschen(ziel) {
    try {
        await api('DELETE', `/vorgaenge/${vorgangId}`, {bestaetigt: true});
        zeigeGeloescht(`„${name()}“ wurde gelöscht. Die Dokumente sind entfernt.`);
    } catch (err) {
        el(ziel === 'abholen' ? 'abholen-rueckfrage' : 'aktion-rueckfrage').classList.add('hidden');
        zeigeFehler(ziel === 'abholen' ? 'abholen-fehler' : 'status-fehler', err.message);
    }
}

function zeigeGeloescht(text) {
    panel('geloescht', 'Vorgang gelöscht', 3);
    el('geloescht-text').textContent = text;
    meldung('');
}

async function erinnerungenUmschalten() {
    const an = el('erinnerungen-aktiv').checked;
    try {
        await api('PUT', `/vorgaenge/${vorgangId}/erinnerungen`, {an});
        vorgang.erinnerungen_aus = !an;
        zeigeFehler('status-fehler', '');
    } catch (err) {
        el('erinnerungen-aktiv').checked = !an;
        zeigeFehler('status-fehler', err.message);
    }
}

// ---------- Schritt 4: Abholen ----------

function zeigeAbholen() {
    panel('abholen', `${name()} · Fertig`, 4);
    const zwei = vorgang.personen === 2;
    const wann = vorgang.unterschrieben_am ? ` (${datumUhr(vorgang.unterschrieben_am)})` : '';
    const mehrere = mehrereDokumente();
    el('abholen-hinweis-text').textContent = `${zwei ? 'Beide Personen haben unterschrieben' : 'Die Unterschrift ist eingegangen'}${wann}. ${mehrere ? 'Alle Dokumente sind fertig.' : 'Das Dokument ist fertig.'}`;
    const frist = vorgang.aufbewahrung_bis ? datumTag(vorgang.aufbewahrung_bis) : '';
    el('abholen-frist').textContent = frist
        ? `${mehrere ? 'Alle Dateien' : 'Beide Dateien'} bleiben bis ${frist} hier abrufbar und werden danach automatisch gelöscht.`
        : `${mehrere ? 'Alle Dateien' : 'Beide Dateien'} bleiben hier abrufbar und werden nach Ablauf der Aufbewahrungsfrist automatisch gelöscht.`;
    zeigeFehler('abholen-fehler', '');
    alleFortschritt('');
    ampelZeigen('abholen-ampel');
    abholenBloecke();
}

// Bei mehreren Dokumenten ein Block je Dokument mit Dokument und Nachweis; bei einem Dokument die beiden Karten wie bisher
function abholenBloecke() {
    const dok = dokumenteDesVorgangs();
    const mehrere = dok.length > 1;
    el('abholen-einzeln').classList.toggle('hidden', mehrere);
    if (!mehrere) {
        const d = dok[0] || {};
        el('abgeholt-dokument').textContent = `Dokument: ${abgeholtText(d.abgeholt || vorgang.abgeholt_am || vorgang.dokument_abgeholt_am)}`;
        el('abgeholt-nachweis').textContent = `Nachweis: ${abgeholtText(d.nachweisAbgeholt || vorgang.nachweis_abgeholt_am)}`;
    }
    const box = el('abholen-mehrere');
    box.classList.toggle('hidden', !mehrere);
    box.textContent = '';
    if (!mehrere) return;
    dok.forEach(d => {
        const karte = document.createElement('section');
        karte.className = 'card abholen-block';
        karte.dataset.rolle = d.rolle;
        const h = document.createElement('h2');
        h.textContent = d.titel;
        const p = document.createElement('p');
        p.className = 'upload-regeln';
        p.textContent = `Das unterschriebene ${d.titel} und der Nachweis, wer wann unterschrieben hat.`;
        const abgeholt = document.createElement('p');
        abgeholt.className = 'upload-regeln abgeholt-zeile';
        abgeholt.dataset.abgeholt = d.rolle;
        abgeholt.textContent = `Dokument: ${abgeholtText(d.abgeholt)} · Nachweis: ${abgeholtText(d.nachweisAbgeholt)}`;
        const knoepfe = document.createElement('div');
        knoepfe.className = 'abholen-knoepfe';
        [['dokument', `${d.titel} herunterladen`], ['nachweis', `Nachweis zum ${d.titel} herunterladen`]].forEach(([art, text]) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'btn btn-primary';
            b.dataset.art = art;
            b.textContent = text;
            b.addEventListener('click', () => herunterladen(art, d.rolle, d.titel));
            knoepfe.appendChild(b);
        });
        karte.append(h, p, abgeholt, knoepfe);
        box.appendChild(karte);
    });
}

// Dateiname: den Namen liefert der Server je Dokument (dateiname, nachweis_dateiname); fehlt er, bleibt der bisherige Name
function dateiName(art, rolle, titel) {
    const dok = dokumenteDesVorgangs().find(d => !rolle || d.rolle === rolle);
    const vomServer = art === 'nachweis'
        ? ((dok && dok.nachweisDateiname) || (!rolle && vorgang.nachweis_dateiname))
        : ((dok && dok.dateiname) || (!rolle && vorgang.dateiname));
    if (typeof vomServer === 'string' && vomServer.trim()) return vomServer.trim();
    return rolle ? `${art}_${String(titel || rolle).replace(/[^A-Za-z0-9_-]+/g, '_')}_${vorgangId}.pdf` : `${art}_${vorgangId}.pdf`;
}

// Holt eine Datei als Blob (ein Abruf; der Server merkt sich "abgeholt am"); Fehler mit der Meldung des Servers
async function holeDatei(art, rolle, titel) {
    const res = await fetch(`/api/v1/vorgaenge/${vorgangId}/download/${art}${rolle ? '?dokument=' + encodeURIComponent(rolle) : ''}`, {credentials: 'same-origin'});
    if (!res.ok) {
        let daten = {};
        try { daten = await res.json(); } catch (err) { daten = {}; }
        throw new Error(daten.meldung || 'Der Download hat nicht geklappt. Bitte versuchen Sie es noch einmal.');
    }
    return {blob: await res.blob(), name: dateiName(art, rolle, titel)};
}

// Löst den Download aus; die Adresse bleibt kurz bestehen (bei mehreren Dateien nacheinander), dann wird sie freigegeben
function speichereDatei(datei, freigebenNach) {
    const url = URL.createObjectURL(datei.blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = datei.name;
    a.click();
    if (freigebenNach) setTimeout(() => URL.revokeObjectURL(url), freigebenNach);
    else URL.revokeObjectURL(url);
}

async function herunterladen(art, rolle, titel) {
    zeigeFehler('abholen-fehler', '');
    try {
        speichereDatei(await holeDatei(art, rolle, titel));
        await abholStandAktualisieren();
    } catch (err) {
        zeigeFehler('abholen-fehler', err.message);
    }
}

// ---------- "Alles herunterladen": je Dokument PDF + Nachweis nacheinander (kein ZIP) ----------

const PAUSE_ZWISCHEN_DOWNLOADS_MS = 400;                 // kurzer Abstand, damit der Browser jeden Download als eigenen Klick annimmt
const URL_FREIGABE_MS = 30000;

const pause = (ms) => new Promise(ok => setTimeout(ok, ms));

// Alle Dateien des Vorgangs in der Reihenfolge Anschreiben (Dokument, Nachweis), Protokoll (Dokument, Nachweis); bei einem Dokument wie die beiden Einzelknöpfe
function dateienDesVorgangs() {
    const dok = dokumenteDesVorgangs();
    if (dok.length > 1) {
        return dok.flatMap(d => [{art: 'dokument', rolle: d.rolle, titel: d.titel, text: d.titel}, {art: 'nachweis', rolle: d.rolle, titel: d.titel, text: `Nachweis zum ${d.titel}`}]);
    }
    return [{art: 'dokument', rolle: null, titel: null, text: 'Dokument'}, {art: 'nachweis', rolle: null, titel: null, text: 'Nachweis'}];
}

function alleFortschritt(text) {
    const p = el('alles-fortschritt');
    p.textContent = text;
    p.classList.toggle('hidden', !text);
}

async function alleHerunterladen() {
    const knopf = el('btn-alles-dl');
    knopf.disabled = true;
    zeigeFehler('abholen-fehler', '');
    const dateien = dateienDesVorgangs();
    const n = dateien.length;
    const geholt = [];
    const fehlgeschlagen = [];
    try {
        for (let i = 0; i < n; i++) {
            alleFortschritt(`Datei ${i + 1} von ${n}: ${dateien[i].text} wird geholt …`);
            try {
                geholt.push({datei: dateien[i], inhalt: await holeDatei(dateien[i].art, dateien[i].rolle, dateien[i].titel)});
            } catch (err) {
                fehlgeschlagen.push(`${dateien[i].text}: ${err.message}`);
            }
        }
        for (let k = 0; k < geholt.length; k++) {
            if (k > 0) await pause(PAUSE_ZWISCHEN_DOWNLOADS_MS);
            alleFortschritt(`Datei ${k + 1} von ${geholt.length}: ${geholt[k].datei.text} wird gespeichert …`);
            speichereDatei(geholt[k].inhalt, URL_FREIGABE_MS);
        }
        if (fehlgeschlagen.length) zeigeFehler('abholen-fehler', `Nicht alle Dateien konnten geholt werden. ${fehlgeschlagen.join(' · ')} Bitte laden Sie diese unten einzeln herunter.`);
        alleFortschritt(geholt.length
            ? `${geholt.length} von ${n} Dateien wurden an Ihren Browser übergeben. Fehlt eine Datei (manche Browser fragen, ob mehrere Downloads erlaubt sind), laden Sie sie unten einzeln herunter.`
            : 'Es wurde keine Datei heruntergeladen.');
        await abholStandAktualisieren();
    } finally {
        knopf.disabled = false;
    }
}

// Nach einem Abruf meldet der Server den neuen Stand (abgeholt am, Ampel); nur diese Stellen werden neu gezeichnet
async function abholStandAktualisieren() {
    try {
        vorgang = await api('GET', `/vorgaenge/${vorgangId}`);
        ampelZeigen('abholen-ampel');
        abholenBloecke();
    } catch (err) {
        // der Download selbst hat geklappt; die Anzeige holt sich beim nächsten Aktualisieren den Stand
    }
}

// ---------- Ereignisse ----------

function bindeEreignisse() {
    el('form-angaben').addEventListener('submit', angabenWeiter);
    el('btn-versenden').addEventListener('click', versenden);
    el('btn-ohne-mail').addEventListener('click', () => ohneMail('versand-error', 'btn-ohne-mail'));
    el('btn-vor-ort').addEventListener('click', () => ohneMail('vor-ort-fehler', 'btn-vor-ort'));
    el('btn-zurueck-pruefen').addEventListener('click', () => { if (pruefdaten) zeigePruefen(); });
    el('btn-neu-laden').addEventListener('click', () => laden('pruefen'));
    el('btn-aktualisieren').addEventListener('click', async () => { linkBox = null; await laden('status'); });
    el('btn-status-aktion').addEventListener('click', () => frage('Einen neuen Link per E-Mail an das Mitglied senden? Der bisherige Link funktioniert dann nicht mehr.', 'Ja, neuen Link senden', neuerLink, false));
    el('btn-neuer-link').addEventListener('click', () => frage('Einen neuen Link per E-Mail an das Mitglied senden? Der bisherige Link funktioniert dann nicht mehr.', 'Ja, neuen Link senden', neuerLink, false));
    el('btn-loeschen').addEventListener('click', () => frage(`„${name()}“ endgültig löschen? Die Dokumente und der Vorgang werden entfernt. Das kann nicht rückgängig gemacht werden.`, 'Ja, löschen', () => loeschen('status'), true));
    el('btn-rueckfrage-ja').addEventListener('click', async () => { el('btn-rueckfrage-ja').disabled = true; if (rueckfrageJa) await rueckfrageJa(); });
    el('btn-rueckfrage-nein').addEventListener('click', () => el('aktion-rueckfrage').classList.add('hidden'));
    el('erinnerungen-aktiv').addEventListener('change', erinnerungenUmschalten);
    el('btn-dl-dokument').addEventListener('click', () => herunterladen('dokument'));
    el('btn-dl-nachweis').addEventListener('click', () => herunterladen('nachweis'));
    el('btn-alles-dl').addEventListener('click', alleHerunterladen);
    el('btn-abholen-loeschen').addEventListener('click', () => frage(`„${name()}“ endgültig löschen? Die Dokumente und der Nachweis werden entfernt. Das kann nicht rückgängig gemacht werden.`, 'Ja, löschen', () => loeschen('abholen'), true, 'abholen'));
    el('btn-abholen-ja').addEventListener('click', async () => { el('btn-abholen-ja').disabled = true; if (rueckfrageJa) await rueckfrageJa(); });
    el('btn-abholen-nein').addEventListener('click', () => el('abholen-rueckfrage').classList.add('hidden'));
}
