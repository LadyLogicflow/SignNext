# SignNext-Demo auf dem Pi (v0.7.0)

Ziel: Das Tool komplett vorführen können: PDFs hochladen, prüfen, versenden (echte Mail), Mitglied unterschreibt, Beratungsstelle lädt herunter. **Nur erfundene Daten, nur im Heimnetz/Tailnet, nie ins Internet.**

## Starten
1. Code holen: `git fetch origin && git checkout sorty/v070 && git pull` (im Ordner mit docker-compose.pi.yaml).
2. `.env` ergänzen (neben den vorhandenen Zeilen SIGNNEXT_HAUPTSCHLUESSEL und SIGNNEXT_PI_ADRESSE; PI_ADRESSE = Adresse OHNE Port, z. B. die Tailscale-Adresse):
   `SIGNNEXT_VERSAND_TESTADRESSEN=erlaubte.adresse@example.org` (Komma-Liste; nur an diese Adressen geht eine Mail)
   `SIGNNEXT_DEMO_HOSTS=<weitere Adresse/Name des Pi, z. B. LAN-IP>` (nur nötig, wenn die Demo auch unter einer zweiten Adresse aufgerufen wird)
3. `docker compose -f docker-compose.pi.yaml -f docker-compose.pi.demo.yml up -d --build`
4. Aufruf im Browser: `https://<PI_ADRESSE>:8443/vorgaenge` (Zertifikatswarnung einmal bestätigen). Es gibt kein Portal-Login; die Seite ist die feste Demo-Beratungsstelle.

Kommt „Diese Demo ist nur im Heimnetz erreichbar“ (403 demo_nicht_erlaubt): Die Client-Adresse liegt nicht in 10/8, 192.168/16, Tailscale oder Loopback, oder der Host-Name stimmt nicht (`SIGNNEXT_DEMO_HOSTS`), oder das LAN liegt in 172.16/12 (`SIGNNEXT_DEMO_NETZE=172.16.0.0/12`). Die Ablehnung steht im Audit (Aktion demo_verweigert, Grund).

## Mail-Zugang (einmalig)
Seite `/mail-zugang`: Server, Port 465, Benutzer = volle Absenderadresse, Absendername, Passwort (nur dort eintragen, nie in .env oder Chat), „Speichern“, dann „Prüfen“. Die Absenderadresse muss zur Domain des Mail-Servers gehören (strenges DMARC der Domain): Domain des Benutzers und der Absenderadresse müssen übereinstimmen, sonst lehnt SignNext den Zugang ab.
Beweis der Zustellung: Testmail an die externe Testadresse (Knopf in der Demo oder `POST /api/v1/demo/testmail {"an": "<adresse>"}`) und in der angekommenen Mail die Original-Header prüfen (`Authentication-Results`: spf=pass, dkim=pass, dmarc=pass).

## Weitere Empfänger ohne Neustart
Datei `testadressen.txt` im Datenverzeichnis des Containers (`/data/testadressen.txt`), eine Adresse je Zeile, `#` beginnt einen Kommentar. Beispiel:
`docker compose -f docker-compose.pi.yaml -f docker-compose.pi.demo.yml exec signnext sh -c 'echo "name@example.org" >> /data/testadressen.txt'`
Die Datei wird bei jedem Versand neu gelesen.

## Sicherheit
- Der Offen-Modus startet nur mit SIGNNEXT_DEMO=1, genau einem Proxy (dem Caddy-Vorsatz) und SIGNNEXT_VERSAND=testadressen oder aus; sonst bricht der Start ab (kein Start statt offener Beraterseite).
- Echtversand (SIGNNEXT_VERSAND=an) ist mit dem Offen-Modus nicht erlaubt.
- Eine Mail geht nur an die Allowlist. Schlägt die Mail fehl oder ist die Adresse nicht freigegeben, bietet die Oberfläche „Ohne Mail fortfahren“: Link und QR erscheinen am Bildschirm.
