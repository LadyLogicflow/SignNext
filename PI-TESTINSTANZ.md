# SignNext: Testinstanz auf dem Raspberry Pi

Zum Ausprobieren der Mitgliederseiten (Unterschrift und Download) mit erfundenen Daten. Kein Produktivbetrieb, keine echten Daten, nie aus dem Internet erreichbar machen.

## Voraussetzungen
- Pi im Heimnetz mit Docker und dem Compose-Plugin (`docker compose version` zeigt eine Version), Git, rund 1 GB frei.
- Ein Rechner oder Handy im selben Netz zum Ausprobieren.

## Starten (einmalig, einige Minuten)
1. Quelltext holen und die getestete Fassung wählen (Adresse des Repositorys von EFINITI): `git clone <Adresse> signnext && cd signnext && git checkout v0.5.4`
2. Einstellungen anlegen: `cp pi.env.beispiel .env`, dann `.env` bearbeiten: `SIGNNEXT_HAUPTSCHLUESSEL` (mindestens 32 zufällige Zeichen, z. B. Ausgabe von `openssl rand -base64 36`) und `SIGNNEXT_PI_ADRESSE` (Adresse des Pi im Heimnetz, z. B. `192.168.1.50`, ohne https://). Die Datei `.env` nicht weitergeben.
3. Bauen und starten: `docker compose -f docker-compose.pi.yaml up -d --build`
4. Prüfen: `docker compose -f docker-compose.pi.yaml ps` (beide Dienste laufen, `signnext` nach etwa einer Minute „healthy“) und `curl -k https://<Adresse>/health` (Antwort mit `"status":"ok"`).

## Ausprobieren
1. Demo-Vorgang anlegen: `docker compose -f docker-compose.pi.yaml exec signnext python manage.py demo`. Die Ausgabe zeigt den **Mitglieds-Link** (`https://<Adresse>/u#…`) und das erfundene Geburtsdatum (`01.02.60`).
2. Link im Browser öffnen. Beim ersten Mal meldet der Browser eine unbekannte Zertifizierungsstelle (Testzertifikat des Pi): bestätigen. Geburtsdatum eingeben, Dokument lesen, Unterschrift zeichnen (Ehepaar: zwei Personen nacheinander).
3. Danach Download-Link erzeugen (Nr. aus Schritt 1): `docker compose -f docker-compose.pi.yaml exec signnext python manage.py demo-download <Nr>`, Link `https://<Adresse>/d#…` öffnen, Geburtsdatum eingeben, Dokument herunterladen (höchstens 3 Abrufe).
4. Weitere Läufe: Schritt 1 wiederholen.

## Was in dieser Testinstanz nicht geht
- **Seiten der Beratungsstelle** (`/vorgaenge`, `/pruefung`, `/einstellungen`): Sie brauchen die Anmeldung über das Portal (Cookie der Domain bst-next.de). Auf dem Pi nicht möglich.
- **Mails**: Der Versand ist ausgeschaltet. Links kommen aus den Kommandos oben, nicht per Mail.
- Die Texte der Unterschriftsseite tragen „Freigabe offen“, solange catrin sie nicht freigegeben hat.

## Aufräumen
- Anhalten: `docker compose -f docker-compose.pi.yaml down` (Daten bleiben).
- Alles löschen: `docker compose -f docker-compose.pi.yaml down -v` (löscht auch die Demo-Daten).

## Wenn etwas nicht klappt
- `signnext` wird nicht „healthy“: `docker compose -f docker-compose.pi.yaml logs signnext`. Häufigste Ursache: `SIGNNEXT_HAUPTSCHLUESSEL` zu kurz oder leer.
- Browser zeigt keine Seite: Adresse in `.env` stimmt nicht mit der aufgerufenen überein, oder Port 443 ist auf dem Pi belegt.
- „Link gesperrt“: Nach 5 falschen Eingaben ist ein Link dauerhaft gesperrt. Neuen Demo-Vorgang anlegen.
