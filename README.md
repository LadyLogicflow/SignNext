# SignNext

Unterschriften für Beratungsstellen (BST Next). Eigener Dienst, Flask + SQLite, Anmeldung über das zentrale Portal-Cookie.

**Stand 0.3.0: Baustein 1 (Portal-Anschluss), Baustein 2 (PDF-Eingang), Baustein 3 (Prüfansicht und Versand).** Unterschriftsseite, Abschluss/Jobs und Abrechnung folgen mit den Bausteinen 4 bis 6.

## Anmeldung und Mandant (Baustein 1)
- Cookie `bst_session` wird nicht selbst geprüft; SignNext fragt SortNext (`/api/v1/portal/me`, 30 s Zwischenspeicher, Ablauf der Freischaltung wirkt nach höchstens 30 s).
- Zugriff nur mit Produktfreischaltung `signnext`. Mandantenschlüssel ist deren `externe_referenz` (`mandant_ref`), nie ein Wert vom Client. Jede Abfrage auf Mandantendaten filtert danach; fremde Objekte → 404.
- Fail closed: SortNext nicht erreichbar oder Antwort unklar → 503; kein Produktzugriff, nicht eingerichtetes Konto, `super_admin`, unbekannte Rolle, mehrere signnext-Einträge, ungültige Referenz → 403.
- Rolle = `rolle` des Nutzers aus SortNext (`bst_admin` → admin; `mitarbeiter`/`bst_user` → mitarbeiter). Eine Rolle je Beratungsstelle liefert SortNext derzeit nicht.

## PDF-Eingang (Baustein 2)
`POST /api/v1/vorgaenge` (multipart, Feld `dateien`, ein oder zwei PDFs) legt einen Vorgang an.
- **Sicherheitsprüfung** (`pdf_pruefung.py`): nur `%PDF`, höchstens 15 MB und 30 Seiten, keine Verschlüsselung, keine aktiven Inhalte (JavaScript, OpenAction, Launch, eingebettete Dateien, RichMedia, XFA, Dateianhänge). Gewöhnliche Links sind erlaubt. Die Auswertung läuft in einem Kindprozess (`pdf_worker.py`) mit leerer Umgebung, 40 s Zeitgrenze, 2 GiB Speicher- und 30 s Rechenzeitgrenze.
- **Erkennung** (`pdf_analyse.py`) am Aufbau, nie am Dateinamen: B = Anschreiben (Unterschriftsblöcke mit Zeile „Ort, TT.MM.JJJJ“ über der Unterstrichzeile, weiße Zeile `Mitgliedsnummer; Vorname; Name; E-Mail` auf Seite 1), A = Steuerprotokoll (Block „Bei der Anfertigung dieser Erklärung hat mitgewirkt:“, Unterstrichzeile über „Datum, Unterschrift …“). Bild-PDFs ohne Textebene werden abgewiesen.
- Weiße Zeile: Grau, RGB, CMYK mit Toleranz. Text, der nur per Deckfläche oder Transparenz versteckt ist, gilt nicht als weiße Zeile (die Beratungsstelle gibt die Adresse dann ein).
- **Abgleich 2 von 3** (`abgleich.py`): Steuernummer, Steuer-ID(s), Mitgliedsnummer, nur wenn in beiden Dokumenten vorhanden. Bei mindestens 2 Treffern wird sofort zusammengeführt (B vorn, A dahinter), sonst `status = bestaetigung_noetig`, bis die Beratungsstelle `POST /api/v1/vorgaenge/<id>/abgleich-bestaetigen` mit `{"bestaetigt": true}` aufruft. Ein einzelnes Dokument wird nicht zusammengeführt.
- **Stellen**: je Dokument Koordinaten (PDF-Punkte, Ursprung unten links) mit Seite und Person; im zusammengeführten PDF mit Seitenversatz. Ehepaar 6, einzelne Person 3 aktive Stellen (`abgleich.aktive_stellen`). Die Personenzahl steht aus B; bei nur A trägt die Beratungsstelle sie in der Prüfansicht ein (Baustein 3).
- **Datenschutz**: Namen, Steuernummern, Steuer-IDs, Geburtsdatum und Mailadresse werden weder gespeichert noch protokolliert; abgelegt werden Dateien (Zufallsname unter `dokumente/<mandant_ref>/<vorgang_id>/`), SHA-256, Größe, Seiten, Stellen und das Abgleichergebnis (ohne Werte). Audit: `vorgang_angelegt`, `upload_abgelehnt`, `abgleich_bestaetigt`, `vorgang_gelesen`.
- Höchstens 20 Uploads je Stunde und Beratungsstelle (429).
- Hinweis zur Layoutstabilität: Die Anker sind an je einem Beispiel gemessen (Steuersoft-Protokoll mit Zeilennummern 8/20 auf Seite 1, „15720 FV NRW“). Abweichende Layouts werden abgewiesen oder bleiben leer, nie geraten.

## Prüfansicht, Angaben und Versand (Baustein 3)
- `GET /api/v1/vorgaenge/<id>/pruefung`: Name, Geburtsdatum, Mailadresse, Personenzahl je mit Herkunft (`pdf` | `manuell` | `abweichend` | `fehlt`), Abgleich, fehlende Angaben. Die Werte aus den PDFs werden bei jedem Aufruf frisch aus den abgelegten Dateien gelesen und nie gespeichert. `GET /api/v1/vorgaenge/<id>/dokument` liefert das PDF zum Ansehen (nur eigener Mandant, `no-store`, `sandbox`).
- `PUT /api/v1/vorgaenge/<id>/angaben` (`email`, `geburtsdatum` TT.MM.JJJJ, `personen` 1|2; `null` nimmt zurück): Pflicht bei nur A oder fehlender Angabe. Mailadresse nur verschlüsselt (AES-256-GCM), Geburtsdatum nur als HMAC des Codes `ttmmjj` je Beratungsstelle, nie im Klartext, nie in Log oder Audit.
- `POST /api/v1/vorgaenge/<id>/versenden` mit `{"bestaetigt": true}` (nie automatisch): Zugangslink (256 Bit, nur der SHA-256 wird gespeichert, 7 Tage gültig, einmalig) per Mail vom Postfach der Beratungsstelle, neutraler Betreff. Zustandswechsel `bereit` → `versenden` → `versendet` atomar (kein Doppelversand). Scheitert der Versand eindeutig vor der Zustellung (Anmeldung, TLS, Verbindung, Empfänger oder Nachricht abgelehnt), geht der Vorgang zurück auf `bereit`. Bricht die Verbindung nach dem Beginn der Datenübertragung ab, steht er auf `versand_unklar` und wird nie automatisch wiederholt. Höchstens 30 Versandversuche je Stunde und Beratungsstelle. Link und Token verlassen den Dienst nur in der Mail.
- Mail-Zugang (`GET`/`PUT`/`DELETE /api/v1/mail-zugang`, `POST /api/v1/mail-zugang/pruefen`): Server, Port 465 oder 587, Benutzer, Passwort (nur schreibbar), Absender, Signaturtext, Kopie an die eigene Absenderadresse (`bcc_an_absender`, Voreinstellung aus; die Kopie enthält den Zugangslink). Ändern nur für Admins. Die Testmail geht nur an die Absenderadresse der Beratungsstelle.
- **Mailserver-Verbindung** (`mailer.py`): Zertifikat und Hostname werden geprüft (`ssl.create_default_context`), Port 465 mit TLS von Anfang an, Port 587 nur mit STARTTLS, kein Rückfall auf unverschlüsselt, keine Anmeldung vor TLS. Das Ziel wird einmal aufgelöst (nur öffentliche Adressen, nur Hostnamen, nur Port 465/587), verbunden wird zu dieser Adresse, geprüft gegen den Hostnamen. Fehler tragen nur einen Code. Hinweis: `smtplib.data()` wirft bei einer Ablehnung nicht, der Rückgabecode wird deshalb ausdrücklich geprüft.
- **Rolle für „Mail-Zugang ändern“** (`portal_auth.admin_pruefen`): Voreinstellung (`SIGNNEXT_STELLE_ROLLE` nicht `an`): Rolle des Nutzers aus SortNext (`bst_admin`); hat der Nutzer laut `/me` mehr als eine Beratungsstelle, wird abgelehnt (fail closed). Grenze: `/me` fasst Produkte zusammen, ein Nutzer mit mehreren Beratungsstellen ist dort nicht in jedem Fall erkennbar. Variante `an`: nur die Rolle je Stelle zählt (Feld `stelle_rolle` in den `stellen` von `/me`, Annahme zum Feldnamen, SortNext liefert es noch nicht).
- **Schlüssel** (`schluessel.py`): `SIGNNEXT_HAUPTSCHLUESSEL` (mindestens 32 Zeichen, fail closed, kein Ersatzwert) nur als Umgebungsvariable, nie im Repo; Betriebshinweis: nicht im selben Backup wie die Datenbank. Schlüssel je Beratungsstelle und Zweck per HKDF, zufälliger Nonce je Wert, Beratungsstelle und Zweck als authentifizierte Zusatzdaten. Schlüsselwechsel: neuen Schlüssel setzen, den bisherigen als `SIGNNEXT_HAUPTSCHLUESSEL_ALT`, `python manage.py schluesselwechsel`. Der Code-HMAC des Geburtsdatums kann nicht umgerechnet werden; der alte Schlüssel muss konfiguriert bleiben, bis die betroffenen Vorgänge abgelaufen oder gelöscht sind.
- Link-Basisadresse: `SIGNNEXT_BASIS_URL` (https, kein Standardwert; ohne sie wird nichts versendet).

## Routen
`GET /health`, `GET /api/version`, `GET /api/v1/me`, `GET|POST /api/v1/vorgaenge`, `GET /api/v1/vorgaenge/<id>`, `POST …/abgleich-bestaetigen`, `GET …/pruefung`, `GET …/dokument`, `PUT …/angaben`, `POST …/versenden`, `GET|PUT|DELETE /api/v1/mail-zugang`, `POST /api/v1/mail-zugang/pruefen`.

## Audit und Löschung
`audit_log` (Zeit, Mandant, Nutzer-E-Mail, Aktion, IP gekürzt auf /24 bzw. /48, keine Dokumentinhalte). `db.mandant_loeschen(mandant_ref)` entfernt Dateien und alle Daten einer Beratungsstelle und liefert Zahlen fürs Löschprotokoll; ein Test stellt sicher, dass neue Mandantentabellen dort aufgenommen werden.

## Konfiguration
`SIGNNEXT_DATEN` (Standard `/data`), `SORTNEXT_BASE_URL`, `SIGNNEXT_PROXIES` (Standard 1), `SIGNNEXT_HAUPTSCHLUESSEL`, `SIGNNEXT_HAUPTSCHLUESSEL_ALT`, `SIGNNEXT_BASIS_URL`, `SIGNNEXT_STELLE_ROLLE`. Alle Abhängigkeiten exakt gepinnt (`requirements.txt`, Tests zusätzlich `requirements-dev.txt` mit reportlab), kein PyMuPDF. Lizenzen laut Paketangaben: pdfplumber und pdfminer.six MIT, pypdf BSD-3, pypdfium2 BSD/Apache, pillow MIT-CMU, cryptography Apache/BSD, reportlab BSD (nur Tests).

## Tests
`python -m unittest tests.test_baustein1 tests.test_baustein2 tests.test_baustein3` (die zweite Gruppe startet für jede Auswertung einen Kindprozess, rund eine Minute). Der Test `TestEchteTestdateien` läuft nur, wenn die echten Beispieldateien aus dem Sync vorhanden sind; sie gehören nicht ins Repo.
