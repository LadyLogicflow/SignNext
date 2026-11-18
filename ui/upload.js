// SignNext Upload der Beratungsstelle: ein Vorgang aus 1 bis 2 PDFs (Anschreiben und Steuerprotokoll) - CSP 'self' konform.
// Ablegen oder auswählen, Prüfung im Browser (nur PDF, Größe, höchstens 2), Fortschritt, Fehler im Text. Der Typ der Dokumente wird vom Server aus dem Inhalt erkannt,
// nie aus dem Dateinamen. Aufruf: Upload.binde({beiFertig: () => ...}) nach dem Laden der Seite.
'use strict';

(function () {
    const MAX_BYTES = 15 * 1024 * 1024;
    const MAX_DATEIEN = 2;
    const TIMEOUT_MS = 120000;
    const ALLGEMEINER_FEHLER = 'Der Upload hat nicht geklappt. Bitte prüfen Sie Ihre Verbindung und versuchen Sie es noch einmal.';
    // Falls der Server keinen lesbaren Text mitschickt (z. B. zu große Anfrage), steht hier ein verständlicher Satz zu seinem Code
    const FEHLERTEXT = {
        zu_gross: 'Die Datei ist zu groß. Erlaubt sind höchstens 15 MB je Datei.',
        zu_viele_uploads: 'Sie haben in der letzten Stunde sehr viele Vorgänge angelegt. Bitte versuchen Sie es in einer Stunde noch einmal.',
        kein_pdf: 'Das ist keine PDF-Datei.',
        keine_textebene: 'Die PDF-Datei enthält keinen Text (vermutlich ein Scan). Bitte erzeugen Sie die Datei direkt aus der Steuersoft bzw. Textverarbeitung als PDF.'
    };
    // Typ aus dem Backend (pdf_analyse): B = Vollständigkeitserklärung, A = Freizeichnungsprotokoll (Steuersoft). Anzeige immer zuerst die Vollständigkeitserklärung.
    const ROLLEN = {B: 'Vollständigkeitserklärung', A: 'Freizeichnungsprotokoll'};
    const ROLLEN_REIHE = ['B', 'A'];

    let auswahl = [];
    let laeuft = false;
    let optionen = {};
    const el = (id) => document.getElementById(id);

    function istPdf(datei) {
        return datei.type === 'application/pdf' || /\.pdf$/i.test(datei.name);
    }

    function pruefeDatei(datei) {
        if (!istPdf(datei)) return `„${datei.name}“ ist keine PDF-Datei. Bitte legen Sie nur PDF-Dateien ab.`;
        if (datei.size === 0) return `„${datei.name}“ ist leer.`;
        if (datei.size > MAX_BYTES) return `„${datei.name}“ ist zu groß. Erlaubt sind höchstens 15 MB je Datei.`;
        return null;
    }

    function groesse(bytes) {
        return bytes >= 1024 * 1024 ? (bytes / 1024 / 1024).toFixed(1).replace('.', ',') + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
    }

    function fehlerZeigen(text) {
        const box = el('upload-fehler');
        box.textContent = text || '';
        box.classList.toggle('hidden', !text);
    }

    function ergebnisVerbergen() {
        el('upload-ergebnis').classList.add('hidden');
        el('upload-ergebnis').textContent = '';
    }

    function zeichneAuswahl() {
        const box = el('upload-auswahlbox');
        const liste = el('upload-dateien');
        liste.textContent = '';
        auswahl.forEach((datei, i) => {
            const li = document.createElement('li');
            const name = document.createElement('span');
            name.textContent = `${datei.name} · ${groesse(datei.size)}`;
            li.appendChild(name);
            const weg = document.createElement('button');
            weg.type = 'button';
            weg.className = 'link-knopf';
            weg.textContent = 'Entfernen';
            weg.setAttribute('aria-label', `${datei.name} entfernen`);
            weg.addEventListener('click', () => {
                if (laeuft) return;
                auswahl.splice(i, 1);
                fehlerZeigen('');
                zeichneAuswahl();
            });
            li.appendChild(weg);
            liste.appendChild(li);
        });
        el('upload-hinweis').textContent = auswahl.length === 1 ? 'Sie können noch ein zweites PDF ablegen (z. B. Vollständigkeitserklärung und Freizeichnungsprotokoll) oder jetzt nur dieses hochladen.' : '';
        box.classList.toggle('hidden', auswahl.length === 0 || laeuft);
    }

    function sperren(an) {
        laeuft = an;
        el('upload-datei').disabled = an;
        el('upload-feld').classList.toggle('gesperrt', an);
    }

    // Neue Dateien aus Auswahl oder Ablegen: erst prüfen, dann übernehmen. Zu viele Dateien: keine übernehmen, nichts raten.
    function hinzufuegen(neu) {
        if (laeuft) return;
        fehlerZeigen('');
        ergebnisVerbergen();
        const liste = Array.from(neu || []);
        if (liste.length === 0) return;
        for (const datei of liste) {
            const problem = pruefeDatei(datei);
            if (problem) {
                fehlerZeigen(problem);
                return;
            }
        }
        const frisch = liste.filter(d => !auswahl.some(a => a.name === d.name && a.size === d.size));
        if (auswahl.length + frisch.length > MAX_DATEIEN) {
            fehlerZeigen('Pro Vorgang sind höchstens 2 PDF-Dateien möglich (Vollständigkeitserklärung und Freizeichnungsprotokoll). Bitte legen Sie die Dateien für weitere Vorgänge nacheinander ab.');
            return;
        }
        auswahl = auswahl.concat(frisch);
        zeichneAuswahl();
        if (auswahl.length === MAX_DATEIEN) hochladen();                          // beide Dokumente da: sofort anlegen
    }

    function fortschritt(text, wert) {
        el('upload-laeuft').classList.remove('hidden');
        el('upload-laeuft-text').textContent = text;
        const balken = el('upload-fortschritt');
        if (wert === null) balken.removeAttribute('value');
        else balken.value = wert;
    }

    function laufEnde() {
        el('upload-laeuft').classList.add('hidden');
        sperren(false);
    }

    function fehlerText(status, daten) {
        if (daten && typeof daten.meldung === 'string' && daten.meldung) return daten.meldung;
        if (daten && FEHLERTEXT[daten.error]) return FEHLERTEXT[daten.error];
        if (status === 413) return FEHLERTEXT.zu_gross;
        if (status === 401 || status === 403) return 'Ihre Anmeldung ist abgelaufen. Bitte laden Sie die Seite neu und melden Sie sich erneut an.';
        return ALLGEMEINER_FEHLER;
    }

    function erfolgZeigen(antwort) {
        const box = el('upload-ergebnis');
        box.textContent = '';
        const titel = document.createElement('strong');
        const name = typeof antwort.name === 'string' && antwort.name ? `„${antwort.name}“` : `Vorgang ${antwort.vorgang_id}`;
        titel.textContent = `${name} wurde angelegt.`;
        box.appendChild(titel);
        const vorhanden = (antwort.dokumente || []).map(d => d.rolle);
        const rollen = ROLLEN_REIHE.filter(r => vorhanden.includes(r)).map(r => ROLLEN[r]);
        const zeile = document.createElement('p');
        const teile = [];
        if (rollen.length) teile.push('Erkannt: ' + rollen.map(r => r + ' ✓').join(' · '));
        if (antwort.personen === 1 || antwort.personen === 2) teile.push(antwort.personen === 1 ? '1 Unterschrift' : '2 Unterschriften');
        zeile.textContent = teile.join(' · ');
        if (teile.length) box.appendChild(zeile);
        if (antwort.status === 'bestaetigung_noetig') {
            const hinweis = document.createElement('p');
            hinweis.textContent = 'Bitte prüfen Sie die Angaben: Vollständigkeitserklärung und Freizeichnungsprotokoll weichen an einer Stelle voneinander ab.';
            box.appendChild(hinweis);
        }
        const aktionen = document.createElement('div');
        aktionen.className = 'upload-aktionen';
        const weiter = document.createElement('a');
        weiter.className = 'btn btn-primary';
        weiter.id = 'upload-weiter';
        weiter.href = '/pruefung?id=' + encodeURIComponent(String(antwort.vorgang_id));
        weiter.textContent = 'Weiter: Angaben prüfen';
        aktionen.appendChild(weiter);
        box.appendChild(aktionen);
        box.classList.remove('hidden');
    }

    function hochladen() {
        if (laeuft || auswahl.length === 0) return;
        fehlerZeigen('');
        ergebnisVerbergen();
        sperren(true);
        el('upload-auswahlbox').classList.add('hidden');
        fortschritt('Wird hochgeladen … 0 %', 0);
        const daten = new FormData();
        auswahl.forEach(datei => daten.append('dateien', datei, datei.name));
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/v1/vorgaenge');
        xhr.timeout = TIMEOUT_MS;
        xhr.upload.onprogress = (e) => {
            if (!e.lengthComputable) return;
            const prozent = Math.min(100, Math.round((e.loaded / e.total) * 100));
            if (prozent >= 100) fortschritt('Die Dokumente werden geprüft … das kann einen Moment dauern.', null);
            else fortschritt(`Wird hochgeladen … ${prozent} %`, prozent);
        };
        const fehlschlag = (text) => {
            laufEnde();
            auswahl = [];
            el('upload-datei').value = '';
            zeichneAuswahl();
            fehlerZeigen(text);
        };
        xhr.onload = () => {
            let antwort = null;
            try {
                antwort = JSON.parse(xhr.responseText);
            } catch (err) {
                antwort = null;
            }
            if (xhr.status === 201 && antwort && antwort.vorgang_id) {
                laufEnde();
                auswahl = [];
                el('upload-datei').value = '';
                zeichneAuswahl();
                erfolgZeigen(antwort);
                if (typeof optionen.beiFertig === 'function') optionen.beiFertig(antwort);
            } else {
                fehlschlag(fehlerText(xhr.status, antwort));
            }
        };
        xhr.onerror = () => fehlschlag(ALLGEMEINER_FEHLER);
        xhr.ontimeout = () => fehlschlag('Die Auswertung dauert ungewöhnlich lange. Bitte prüfen Sie in der Liste, ob der Vorgang angelegt wurde, und versuchen Sie es sonst noch einmal.');
        xhr.send(daten);
    }

    function binde(opt) {
        optionen = opt || {};
        const feld = el('upload-feld');
        const eingabe = el('upload-datei');
        eingabe.addEventListener('change', () => {
            hinzufuegen(eingabe.files);
            if (!laeuft) eingabe.value = '';
        });
        ['dragenter', 'dragover'].forEach(name => feld.addEventListener(name, (e) => {
            e.preventDefault();
            if (!laeuft) feld.classList.add('ziehen');
        }));
        ['dragleave', 'dragend'].forEach(name => feld.addEventListener(name, () => feld.classList.remove('ziehen')));
        feld.addEventListener('drop', (e) => {
            e.preventDefault();
            feld.classList.remove('ziehen');
            hinzufuegen(e.dataTransfer ? e.dataTransfer.files : []);
        });
        el('btn-upload').addEventListener('click', hochladen);
        el('btn-upload-leeren').addEventListener('click', () => {
            if (laeuft) return;
            auswahl = [];
            fehlerZeigen('');
            zeichneAuswahl();
        });
        // Eine Datei, die außerhalb des Feldes losgelassen wird, soll nicht die Seite verlassen (der Browser würde das PDF öffnen)
        ['dragover', 'drop'].forEach(name => window.addEventListener(name, (e) => { if (!feld.contains(e.target)) e.preventDefault(); }));
    }

    window.Upload = {binde};
})();
