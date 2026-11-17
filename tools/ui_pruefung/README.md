# UI-Prüfung der SignNext-Oberfläche (jsdom)

Treibt die echten Dateien aus `ui/` (Unterschriftsseite `/u`, Handy-Seite `/h`, Berater-Seiten im Demo-Modus) in jsdom. Ein kleiner Testserver in `drive_sn.js` liefert `ui/` aus und
spielt die Schnittstelle nach der Schnittstellenbeschreibung nach (Zugang, Handy-Übergabe, Handy-Seite, Demo). Die Prüfung ist damit unabhängig vom Stand des Backends und
fängt Vertragsfehler zwischen Oberfläche und Beschreibung, ersetzt aber keinen Test im echten Browser (Zeichnen mit dem Finger, Layout, Kamera).

Einmalig: `cd tools/ui_pruefung && npm install jsdom@24 --no-save` (Node 20). Dann im Repository-Wurzelordner:
`python -m unittest discover -s tools/ui_pruefung -p "test_ui_*.py"` (`test_ui_unterschrift.py`: Mitglied und Handy, `test_ui_berater.py`: Demo-Modus der Berater-Seiten). Nicht Teil des Gates (braucht Node und jsdom); ohne jsdom werden die Tests übersprungen.

Das Zeichenfeld wird in jsdom durch einen Zähler ersetzt (kein echtes Canvas): geprüft wird, WAS gezeichnet und gesendet wird (Striche, Stil, leeres Antippen, Bild), nicht wie es aussieht.
