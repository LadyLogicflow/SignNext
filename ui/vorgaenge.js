// SignNext Vorgangsliste - CSP 'self' konform
'use strict';

const ALLGEMEINER_FEHLER = 'Das hat leider nicht geklappt. Bitte versuchen Sie es noch einmal.';

document.addEventListener('DOMContentLoaded', async () => {
    if (!(await Berater.bereit())) return;
    Upload.binde({beiFertig: loadVorgaenge});
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
        let err = {};
        try { err = await res.json(); } catch (e) { err = {}; }
        throw new Error(err.meldung || ALLGEMEINER_FEHLER);                                  // nie ein Code oder das Wort "Fehler"
    }
    return res.json();
}

// ---------- Übersicht: Karten, Filter, Liste (Konzept 17.11.2026) ----------
// Die Seite für Prüfen/Senden/Status; bis die eigenen Seiten fertig sind, öffnet alles die Prüfansicht
const VORGANG_SEITE = '/pruefung';
const PILLEN = {sie: 'p-sie', mitglied: 'p-mitglied', fertig: 'p-fertig', problem: 'p-problem', sonst: 'p-grau'};
const GRUPPEN = [
    {schluessel: 'sie', text: 'Wartet auf Sie', unter: 'Prüfen und senden'},
    {schluessel: 'mitglied', text: 'Beim Mitglied', unter: 'Warten auf die Unterschrift'},
    {schluessel: 'fertig', text: 'Fertig', unter: 'Dokument kann abgeholt werden'}
];
const FILTER = [{schluessel: 'alle', text: 'Alle'}, {schluessel: 'sie', text: 'Wartet auf Sie'}, {schluessel: 'mitglied', text: 'Beim Mitglied'},
    {schluessel: 'fertig', text: 'Fertig'}, {schluessel: 'problem', text: 'Probleme'}];
// Klartext für die Beratungsstelle statt Statusnamen aus dem Code
const STATUS = {
    neu: {text: 'Bitte prüfen', gruppe: 'sie', knopf: 'Prüfen'},
    bestaetigung_noetig: {text: 'Bitte prüfen', gruppe: 'sie', knopf: 'Prüfen', zusatz: 'Angaben weichen ab'},
    bereit: {text: 'Bereit zum Senden', gruppe: 'sie', knopf: 'Senden'},
    versenden: {text: 'Wird gerade gesendet …', gruppe: 'mitglied'},
    versendet: {text: 'Wartet auf Unterschrift', gruppe: 'mitglied', knopf: 'Ansehen', zweit: true},
    versand_unklar: {text: 'Versand unklar', gruppe: 'problem', knopf: 'Ansehen', zweit: true, zusatz: 'Bitte Postfach prüfen'},
    unterschrieben: {text: 'Unterschrieben – wird fertiggestellt', gruppe: 'mitglied'},
    abschluss_laeuft: {text: 'Unterschrieben – wird fertiggestellt', gruppe: 'mitglied'},
    abschluss_fehlgeschlagen: {text: 'Fertigstellung hakt', gruppe: 'problem', zusatz: 'Wir versuchen es erneut'},
    abgeschlossen: {text: 'Fertig – abholen', gruppe: 'fertig', knopf: 'Dokument laden'}
};
const LINK_PROBLEM = {abgelaufen: 'Link abgelaufen', gesperrt: 'Link gesperrt'};
const OHNE_LOESCHEN = ['versenden', 'abschluss_laeuft'];
let alleVorgaenge = [];
let statusTexte = {};
let aktiverFilter = 'alle';
let ladeFehler = false;
const MS_TAG = 24 * 60 * 60 * 1000;

function datumUhr(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const tag = d.toLocaleDateString('de-DE', {day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Europe/Berlin'});
    const uhr = d.toLocaleTimeString('de-DE', {hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin'});
    return `${tag}, ${uhr}`;
}

// Ein Eintrag der Liste in die Anzeige: Text der Marke, Gruppe, Hauptknopf, Zusatzzeile (immer verständlicher Text, nie ein Code)
function beschreibe(v) {
    const grund = STATUS[v.status];
    const info = {gruppe: 'sonst', text: '', zusatz: '', knopf: null, zweit: false, neuerLink: false};
    if (grund) {
        Object.assign(info, grund);
    } else {
        info.text = statusTexte[v.status] || String(v.status || 'unbekannt').replace(/_/g, ' ');       // unbekannter Wert: lesbar anzeigen, nichts raten
    }
    const gesamt = Number(v.dokumente_gesamt) || 0;
    const fertig = Number(v.dokumente_fertig) || 0;
    if (v.status === 'abgeschlossen' && gesamt > 1) info.knopf = 'Abholen';                          // mehrere Dokumente: die Seite zum Abholen statt eines einzelnen Downloads
    if (v.status === 'versendet' && gesamt > 1 && fertig > 0 && fertig < gesamt && !LINK_PROBLEM[v.link_status]) {
        info.text = 'Teilweise unterschrieben';
        info.zusatz = `${fertig} von ${gesamt} Dokumenten unterschrieben`;
    }
    if (v.status === 'versendet' && LINK_PROBLEM[v.link_status]) {
        info.text = LINK_PROBLEM[v.link_status];
        info.gruppe = 'problem';
        info.knopf = 'Neuen Link senden';
        info.zweit = false;
        info.neuerLink = true;
        info.zusatz = '';
    } else if (v.status === 'versendet' && v.link_status === 'aktiv' && v.link_laeuft_ab) {
        const tage = Math.ceil((new Date(v.link_laeuft_ab).getTime() - Date.now()) / MS_TAG);
        info.zusatz = tage > 1 ? `noch ${tage} Tage gültig` : (tage === 1 ? 'noch 1 Tag gültig' : 'läuft heute ab');
    }
    return info;
}

function zaehle(liste) {
    const z = {alle: liste.length, sie: 0, mitglied: 0, fertig: 0, problem: 0};
    liste.forEach(v => { const g = beschreibe(v).gruppe; if (z[g] !== undefined) z[g] += 1; });
    return z;
}

function zeigeListenMeldung(text, art) {
    const box = document.getElementById('liste-meldung');
    box.textContent = text || '';
    box.className = 'liste-meldung' + (text ? '' : ' hidden') + (art === 'fehler' ? ' error' : '');
    box.setAttribute('role', art === 'fehler' ? 'alert' : 'status');
}

async function loadVorgaenge() {
    try {
        const data = await api('GET', '/vorgaenge');
        alleVorgaenge = Array.isArray(data.vorgaenge) ? data.vorgaenge : [];
        statusTexte = data.status_texte || {};
        ladeFehler = false;
    } catch (err) {
        alleVorgaenge = [];
        ladeFehler = true;
        zeigeListenMeldung('Die Vorgänge konnten nicht geladen werden. ' + err.message, 'fehler');
    }
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('uebersicht').classList.remove('hidden');
    if (!ladeFehler) zeigeListenMeldung('');
    zeichneUebersicht();
}

function zeichneUebersicht() {
    const z = zaehle(alleVorgaenge);
    const karten = document.getElementById('karten');
    karten.textContent = '';
    GRUPPEN.forEach(g => {
        const k = document.createElement('button');
        k.type = 'button';
        k.className = 'karte-zahl';
        k.dataset.filter = g.schluessel;
        k.setAttribute('aria-pressed', String(aktiverFilter === g.schluessel));
        const titel = document.createElement('span');
        titel.className = 'karte-titel';
        titel.textContent = g.text;
        const zahl = document.createElement('span');
        zahl.className = 'karte-wert';
        zahl.textContent = String(z[g.schluessel]);
        const unter = document.createElement('span');
        unter.className = 'karte-unter';
        unter.textContent = g.unter;
        k.append(titel, zahl, unter);
        k.addEventListener('click', () => setzeFilter(aktiverFilter === g.schluessel ? 'alle' : g.schluessel));
        karten.appendChild(k);
    });
    const filter = document.getElementById('filter');
    filter.textContent = '';
    FILTER.forEach(f => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'filter-knopf';
        b.dataset.filter = f.schluessel;
        b.setAttribute('aria-pressed', String(aktiverFilter === f.schluessel));
        b.textContent = `${f.text} (${z[f.schluessel]})`;
        b.addEventListener('click', () => setzeFilter(f.schluessel));
        filter.appendChild(b);
    });
    const aktualisieren = document.createElement('button');
    aktualisieren.type = 'button';
    aktualisieren.id = 'btn-aktualisieren';
    aktualisieren.className = 'filter-knopf filter-rechts';
    aktualisieren.textContent = '↻ Aktualisieren';
    aktualisieren.addEventListener('click', loadVorgaenge);
    filter.appendChild(aktualisieren);
    zeichneListe();
}

function setzeFilter(schluessel) {
    aktiverFilter = schluessel;
    zeichneUebersicht();
}

function zeichneListe() {
    const liste = document.getElementById('vorgaenge-liste');
    liste.textContent = '';
    const sichtbar = alleVorgaenge.filter(v => aktiverFilter === 'alle' || beschreibe(v).gruppe === aktiverFilter);
    if (alleVorgaenge.length === 0 && !ladeFehler) {
        document.getElementById('leer-hinweis').classList.remove('hidden');
        document.getElementById('listen-karte').classList.add('hidden');
        return;
    }
    document.getElementById('leer-hinweis').classList.add('hidden');
    document.getElementById('listen-karte').classList.remove('hidden');
    if (sichtbar.length === 0 && !ladeFehler) {
        const leer = document.createElement('p');
        leer.className = 'liste-leer';
        leer.textContent = 'Keine Vorgänge in dieser Ansicht.';
        liste.appendChild(leer);
    }
    sichtbar.forEach(v => liste.appendChild(zeile(v)));
}

function aktionsKnopf(text, klasse, beiKlick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = klasse;
    b.textContent = text;
    b.addEventListener('click', beiKlick);
    return b;
}

function vorgangsName(v) {
    return typeof v.name === 'string' && v.name ? v.name : `Vorgang ${v.id}`;
}

function zeile(v) {
    const info = beschreibe(v);
    const z = document.createElement('div');
    z.className = 'zeile';
    z.dataset.id = String(v.id);
    z.setAttribute('role', 'listitem');
    const wer = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'zeile-name';
    name.textContent = vorgangsName(v);
    const sub = document.createElement('div');
    sub.className = 'zeile-sub';
    sub.textContent = `Vorgang ${v.id} · angelegt ${datumUhr(v.erstellt)}`;
    wer.append(name, sub);
    const status = document.createElement('div');
    const pille = document.createElement('span');
    pille.className = 'pille ' + PILLEN[info.gruppe];
    pille.textContent = info.text;
    status.appendChild(pille);
    if (info.zusatz) {
        const zusatz = document.createElement('div');
        zusatz.className = 'zeile-sub';
        zusatz.textContent = info.zusatz;
        status.appendChild(zusatz);
    }
    const personen = document.createElement('div');
    personen.className = 'zeile-sub';
    personen.textContent = v.personen === 1 ? '1 Person' : (v.personen === 2 ? '2 Personen' : '');
    const aktion = document.createElement('div');
    aktion.className = 'zeile-aktion';
    z.append(wer, status, personen, aktion);
    zeichneAktion(z, aktion, v, info);
    return z;
}

function zeichneAktion(z, aktion, v, info) {
    aktion.textContent = '';
    const alteFrage = z.querySelector('.rueckfrage');
    if (alteFrage) alteFrage.remove();
    if (info.knopf) {
        const klasse = 'btn btn-klein ' + (info.zweit ? 'btn-secondary' : 'btn-primary');
        if (info.neuerLink) aktion.appendChild(aktionsKnopf(info.knopf, klasse, () => frage(z, aktion, v, info, neuerLinkFrage(v), 'Ja, neuen Link senden', () => neuerLink(v, z))));
        else if (v.status === 'abgeschlossen' && !(Number(v.dokumente_gesamt) > 1)) aktion.appendChild(aktionsKnopf(info.knopf, klasse, () => downloadVorgang(v.id, 'dokument', z)));
        else {
            const a = document.createElement('a');
            a.className = klasse;
            a.href = `${VORGANG_SEITE}?id=${encodeURIComponent(String(v.id))}`;
            a.textContent = info.knopf;
            aktion.appendChild(a);
        }
    }
    const eintraege = [];
    if (v.status === 'abgeschlossen' && !(Number(v.dokumente_gesamt) > 1)) eintraege.push(['Nachweis herunterladen', () => downloadVorgang(v.id, 'nachweis', z)]);
    if (['versendet', 'versand_unklar'].includes(v.status) && !info.neuerLink) eintraege.push(['Neuen Link senden …', () => frage(z, aktion, v, info, neuerLinkFrage(v), 'Ja, neuen Link senden', () => neuerLink(v, z))]);
    if (!OHNE_LOESCHEN.includes(v.status)) eintraege.push(['Löschen …', () => frage(z, aktion, v, info, loeschFrage(v), 'Ja, löschen', () => loeschen(v, z), true)]);
    if (eintraege.length) {
        const mehr = document.createElement('details');
        mehr.className = 'mehr';
        const sum = document.createElement('summary');
        sum.textContent = '⋯';
        sum.setAttribute('aria-label', `Weitere Aktionen für ${vorgangsName(v)}`);
        mehr.appendChild(sum);
        const menue = document.createElement('div');
        menue.className = 'mehr-menue';
        eintraege.forEach(([text, beiKlick]) => {
            const b = aktionsKnopf(text, 'mehr-eintrag' + (text.startsWith('Löschen') ? ' mehr-gefahr' : ''), () => { mehr.open = false; beiKlick(); });
            menue.appendChild(b);
        });
        mehr.appendChild(menue);
        aktion.appendChild(mehr);
    }
}

const loeschFrage = (v) => `„${vorgangsName(v)}“ endgültig löschen? Die Dokumente und der Vorgang werden entfernt. Das kann nicht rückgängig gemacht werden.`;
const neuerLinkFrage = (v) => `Für „${vorgangsName(v)}“ einen neuen Link per E-Mail senden? Der bisherige Link funktioniert dann nicht mehr.`;

// Rückfrage unter der Zeile (über die volle Breite, nicht in der Aktionsspalte): erst nach "Ja" passiert etwas, "Abbrechen" stellt die Zeile wieder her
function frage(z, aktion, v, info, text, jaText, aktionJa, gefahr) {
    zeichneAktion(z, aktion, v, info);
    const fehler = z.querySelector('.zeile-fehler');
    if (fehler) fehler.remove();
    const box = document.createElement('div');
    box.className = 'rueckfrage';
    box.setAttribute('role', 'alert');
    const t = document.createElement('p');
    t.textContent = text;
    const knoepfe = document.createElement('div');
    knoepfe.className = 'knopf-zeile';
    const ja = aktionsKnopf(jaText, gefahr ? 'btn btn-klein btn-gefahr' : 'btn btn-klein btn-primary', async () => {
        ja.disabled = true;
        nein.disabled = true;
        await aktionJa();
    });
    const nein = aktionsKnopf('Abbrechen', 'btn btn-klein btn-secondary', () => zeichneAktion(z, aktion, v, info));
    knoepfe.append(ja, nein);
    box.append(t, knoepfe);
    z.appendChild(box);
}

function zeigeZeilenFehler(z, text) {
    let f = z.querySelector('.zeile-fehler');
    if (!f) {
        f = document.createElement('div');
        f.className = 'zeile-fehler error';
        f.setAttribute('role', 'alert');
        z.appendChild(f);
    }
    f.textContent = text;
}

async function loeschen(v, z) {
    try {
        await api('DELETE', `/vorgaenge/${encodeURIComponent(String(v.id))}`, {bestaetigt: true});
        alleVorgaenge = alleVorgaenge.filter(x => x.id !== v.id);
        zeigeListenMeldung(`„${vorgangsName(v)}“ wurde gelöscht.`);
        zeichneUebersicht();
    } catch (err) {
        const aktion = z.querySelector('.zeile-aktion');
        zeichneAktion(z, aktion, v, beschreibe(v));
        zeigeZeilenFehler(z, err.message);
    }
}

async function neuerLink(v, z) {
    try {
        await api('POST', `/vorgaenge/${encodeURIComponent(String(v.id))}/neuer-link`, {bestaetigt: true});
        await loadVorgaenge();
        zeigeListenMeldung(`Ein neuer Link für „${vorgangsName(v)}“ wurde per E-Mail gesendet.`);                 // nach dem Neuladen, sonst wird sie sofort wieder gelöscht
    } catch (err) {
        const aktion = z.querySelector('.zeile-aktion');
        zeichneAktion(z, aktion, v, beschreibe(v));
        zeigeZeilenFehler(z, err.message);
    }
}

async function downloadVorgang(id, type, z) {
    try {
        const res = await fetch(`/api/v1/vorgaenge/${encodeURIComponent(String(id))}/download/${type}`, {credentials: 'same-origin'});
        if (!res.ok) {
            let daten = {};
            try { daten = await res.json(); } catch (err) { daten = {}; }
            throw new Error(daten.meldung || 'Der Download hat nicht geklappt. Bitte versuchen Sie es noch einmal.');
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${type}_${id}.pdf`;
        a.click();
        URL.revokeObjectURL(url);
    } catch (err) {
        if (z) zeigeZeilenFehler(z, err.message);
        else zeigeListenMeldung(err.message, 'fehler');
    }
}
