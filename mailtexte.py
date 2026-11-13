"""Mailtexte der Bausteine 4 und 5 (Erinnerungen, Bestätigung, Hinweise an die Beratungsstelle). ENTWURF: Wortlaut nicht freigegeben (Irony/catrin).

Regeln: Betreff neutral, nie Namen, Dokumentnamen oder Steuerbezug im Betreff; in Mails an die Beratungsstelle nur die Vorgangsnummer. Der Wortlaut der LETZTEN Erinnerung
(156 Stunden) legt catrin fest, bis dahin steht der neutrale Entwurf unten (ohne Fristen, Beträge oder Drohungen)."""

FREIGEGEBEN = False

HINWEIS_GEBURTSDATUM = ("Zum Öffnen benötigen Sie das Geburtsdatum der Person, für die das Dokument erstellt wurde, bei Ehepaaren und eingetragenen Lebenspartnerschaften "
                        "der zuerst genannten Person (sechs Ziffern: Tag, Monat, zweistelliges Jahr, ohne Punkte).")
HINWEIS_UNERWARTET = "Haben Sie diese Nachricht nicht erwartet, öffnen Sie den Link bitte nicht und rufen Sie Ihre Beratungsstelle an."
AUSSTIEG = "Wenn Sie nicht unterschreiben möchten, wenden Sie sich bitte an Ihre Beratungsstelle."

ERINNERUNG_BETREFF = "Erinnerung Ihrer Beratungsstelle: Ihre Unterschrift fehlt noch"
ERINNERUNG_TEXT = (
    "Guten Tag,\n\n"
    "Ihre Beratungsstelle hat Ihnen ein Dokument zur Unterschrift bereitgestellt. Ihre Unterschrift fehlt noch. Sie wird benötigt, damit Ihre Beratungsstelle "
    "Ihre Unterlagen an das Finanzamt weitergeben kann.\n\n"
    "Bitte öffnen Sie das Dokument über Ihren persönlichen Link (bis {ablauf} gültig, nur für Sie bestimmt, bitte nicht weiterleiten):\n{link}\n\n"
    + HINWEIS_GEBURTSDATUM + "\n\n" + HINWEIS_UNERWARTET + "\n\n" + AUSSTIEG + "\n")
ERINNERUNG_LETZTE_TEXT = (
    "Guten Tag,\n\n"
    "Letzte Erinnerung: Der Link zu Ihrem Dokument wird in etwa 12 Stunden ungültig. Ihre Unterschrift fehlt noch.\n\n"
    "Ihr persönlicher Link (bitte nicht weiterleiten):\n{link}\n\n"
    + HINWEIS_GEBURTSDATUM + "\n\n" + HINWEIS_UNERWARTET + "\n\n" + AUSSTIEG + "\n")

FERTIG_BETREFF = "Ihre Beratungsstelle: Ihre Unterschrift wurde erfasst"
FERTIG_TEXT_MIT_LINK = (
    "Guten Tag,\n\n"
    "Ihre Unterschrift wurde erfasst. Sie können das Dokument mit diesem persönlichen Link abrufen (7 Tage gültig, höchstens {max} Abrufe, bitte nicht weiterleiten):\n{link}\n\n"
    + HINWEIS_UNERWARTET + "\n\nBei Fragen wenden Sie sich bitte an Ihre Beratungsstelle.\n")
FERTIG_TEXT_OHNE_LINK = (
    "Guten Tag,\n\n"
    "Ihre Unterschrift wurde erfasst. Ihre Beratungsstelle hat das Dokument erhalten und gibt es Ihnen auf Wunsch weiter.\n\n"
    + HINWEIS_UNERWARTET + "\n\nBei Fragen wenden Sie sich bitte an Ihre Beratungsstelle.\n")


def stelle_betreff(vorgang_id: int) -> str:
    return "SignNext: Hinweis zu Vorgang Nr. %d" % vorgang_id


def stelle_text(art: str, vorgang_id: int, **kw) -> str:
    """Hinweise an die Beratungsstelle. Nur die Vorgangsnummer, nie Namen oder Dokumentnamen."""
    kopf = "Guten Tag,\n\n"
    fuss = "\nDiese Nachricht wurde automatisch von SignNext über Ihr Postfach versendet.\n"
    n = "Vorgang Nr. %d" % vorgang_id
    texte = {
        "link_gesperrt": "der Zugangslink zu %s wurde nach mehreren falschen Eingaben gesperrt. Bitte erzeugen Sie bei Bedarf einen neuen Link." % n,
        "unterschrieben": "%s wurde unterschrieben. Sie können das Dokument und das Nachweisprotokoll in SignNext (angemeldet) herunterladen. Aufbewahrung bis %s." % (n, kw.get("bis", "")),
        "abruf": "Das Dokument zu %s wurde vom Mitglied abgerufen (Abruf %s von %s)." % (n, kw.get("zaehler", ""), kw.get("max", "")),
        "vorwarnung_loeschung": "%s ist noch nicht unterschrieben. Der Link läuft in etwa 24 Stunden ab, danach wird der Vorgang gelöscht. Bei Bedarf erzeugen Sie vorher einen neuen Link." % n,
        "vorwarnung_nicht_versendet": "%s wurde nicht versendet. Er wird in etwa 24 Stunden gelöscht. Bitte senden Sie ihn bei Bedarf vorher ab." % n,
        "geloescht_nicht_unterschrieben": "%s wurde nicht unterschrieben und gelöscht. Bitte laden Sie die Unterlagen bei Bedarf neu hoch." % n,
        "geloescht_nicht_versendet": "%s wurde nicht versendet und gelöscht. Bitte laden Sie die Unterlagen bei Bedarf neu hoch." % n,
        "vorwarnung_aufbewahrung": "das unterschriebene Dokument und das Nachweisprotokoll zu %s werden am %s gelöscht. Bitte laden Sie beides vorher herunter und legen Sie es ab." % (n, kw.get("bis", "")),
        "geloescht_aufbewahrung": "das unterschriebene Dokument und das Nachweisprotokoll zu %s wurden nach Ablauf der Aufbewahrungsfrist gelöscht." % n,
    }
    return kopf + texte[art][0].upper() + texte[art][1:] + "\n" + fuss
