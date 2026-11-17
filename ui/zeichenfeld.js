// SignNext Zeichenfeld, gemeinsam für die Unterschriftsseite (/u) und die Handy-Seite (/h) - CSP 'self' konform, keine Inline-Skripte.
// Verhalten wie in BookNext (Strich 3 px, in der PDF-Stelle sonst zu dünn): Pointer Events (Maus, Stift, Finger), roter Hinweis "Bitte hier unterschreiben." bis gezeichnet, "löschen",
// leeres Antippen zählt nicht. Es wird nur das Bild weitergegeben (PNG), nie Druck, Geschwindigkeit oder Strichverlauf.
'use strict';

(function () {
    const STRICHFARBE = '#0b2545';
    const STRICHBREITE = 3;

    // wurzel enthält .zeichenfeld-canvas, .zeichenfeld-leeren und .zeichenfeld-fehlt (siehe unterschrift.html und handy.html)
    function binde(wurzel, optionen) {
        const o = optionen || {};
        const canvas = wurzel.querySelector('.zeichenfeld-canvas');
        const leerenKnopf = wurzel.querySelector('.zeichenfeld-leeren');
        const fehlt = wurzel.querySelector('.zeichenfeld-fehlt');
        const label = wurzel.querySelector('.zeichenfeld-label');
        const ctx = canvas.getContext('2d');
        let zeichnet = false;
        let letzteX = 0;
        let letzteY = 0;
        let inhalt = false;
        let gesperrt = false;

        const melden = () => {
            if (fehlt) fehlt.classList.toggle('hidden', inhalt);
            if (typeof o.onAenderung === 'function') o.onAenderung(inhalt);
        };

        const position = (e) => {
            const rect = canvas.getBoundingClientRect();
            return {
                x: ((e.clientX - rect.left) / (rect.width || canvas.width)) * canvas.width,
                y: ((e.clientY - rect.top) / (rect.height || canvas.height)) * canvas.height,
            };
        };

        const anfang = (e) => {
            if (gesperrt) return;
            if (e.pointerType === 'mouse' && e.button !== 0) return;
            e.preventDefault();
            const p = position(e);
            zeichnet = true;
            letzteX = p.x;
            letzteY = p.y;
            if (canvas.setPointerCapture && e.pointerId !== undefined) {
                try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* ältere Browser: ohne Capture weiter */ }
            }
        };

        const bewegung = (e) => {
            if (!zeichnet) return;
            e.preventDefault();
            const p = position(e);
            if (p.x === letzteX && p.y === letzteY) return;
            ctx.lineWidth = STRICHBREITE;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.strokeStyle = STRICHFARBE;
            ctx.beginPath();
            ctx.moveTo(letzteX, letzteY);
            ctx.lineTo(p.x, p.y);
            ctx.stroke();
            letzteX = p.x;
            letzteY = p.y;
            if (!inhalt) {
                inhalt = true;                              // erst ein gezogener Strich zählt, ein bloßes Antippen nicht
                melden();
            }
        };

        const ende = () => {
            zeichnet = false;
        };

        canvas.addEventListener('pointerdown', anfang);
        canvas.addEventListener('pointermove', bewegung);
        canvas.addEventListener('pointerup', ende);
        canvas.addEventListener('pointercancel', ende);
        canvas.addEventListener('lostpointercapture', ende);

        const leeren = () => {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            zeichnet = false;
            inhalt = false;
            melden();
        };
        if (leerenKnopf) leerenKnopf.addEventListener('click', () => { if (!gesperrt) leeren(); });
        melden();

        return {
            hatInhalt: () => inhalt,
            leeren,
            png: () => canvas.toDataURL('image/png'),
            // Bild vom Handy: wie eine selbst gezeichnete Unterschrift im Feld anzeigen (Vorschau), danach wie gewohnt senden
            setzeBild(dataUri) {
                return new Promise((resolve, reject) => {
                    const bild = new Image();
                    bild.onload = () => {
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                        ctx.drawImage(bild, 0, 0, canvas.width, canvas.height);
                        inhalt = true;
                        melden();
                        resolve();
                    };
                    bild.onerror = () => reject(new Error('Das Bild konnte nicht angezeigt werden.'));
                    bild.src = dataUri;
                });
            },
            setzeBeschriftung(text) {
                if (label) label.textContent = text;
            },
            sperren(wert) {
                gesperrt = !!wert;
                canvas.classList.toggle('gesperrt', gesperrt);
            },
        };
    }

    window.Zeichenfeld = {binde};
})();
