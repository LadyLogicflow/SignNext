# SignNext

Unterschriften für Beratungsstellen (BST Next). Eigener Dienst, Flask + SQLite, Anmeldung über das zentrale Portal-Cookie.

**Stand 0.1.0: Baustein 1 (Portal-Anschluss).** PDF-Eingang, Versand, Unterschriftsseite, Abschluss und Abrechnung folgen mit den Bausteinen 2 bis 6.

## Anmeldung und Mandant
- Cookie `bst_session` wird nicht selbst geprüft; SignNext fragt SortNext (`/api/v1/portal/me`, 30 s Zwischenspeicher, Ablauf der Freischaltung wirkt nach höchstens 30 s).
- Zugriff nur mit Produktfreischaltung `signnext`. Mandantenschlüssel ist deren `externe_referenz` (`mandant_ref`), nie ein Wert vom Client. Jede Abfrage auf Mandantendaten filtert danach; fremde Objekte → 404.
- Fail closed: SortNext nicht erreichbar oder Antwort unklar → 503; kein Produktzugriff, nicht eingerichtetes Konto, `super_admin`, unbekannte Rolle, mehrere signnext-Einträge, ungültige Referenz → 403.
- Rolle = `rolle` des Nutzers aus SortNext (`bst_admin` → admin; `mitarbeiter`/`bst_user` → mitarbeiter). Eine Rolle je Beratungsstelle liefert SortNext derzeit nicht.

## Routen
`GET /health`, `GET /api/version`, `GET /api/v1/me`, `GET /api/v1/vorgaenge`, `GET /api/v1/vorgaenge/<id>`.

## Audit und Löschung
`audit_log` (Zeit, Mandant, Nutzer-E-Mail, Aktion, IP gekürzt auf /24 bzw. /48, keine Dokumentinhalte). `db.mandant_loeschen(mandant_ref)` entfernt alle Daten einer Beratungsstelle und liefert Zahlen fürs Löschprotokoll; ein Test stellt sicher, dass neue Mandantentabellen dort aufgenommen werden.

## Konfiguration
`SIGNNEXT_DATEN` (Standard `/data`), `SORTNEXT_BASE_URL`, `SIGNNEXT_PROXIES` (Standard 1). Alle Abhängigkeiten exakt gepinnt, kein PyMuPDF.

## Tests
`python -m unittest tests.test_baustein1`
