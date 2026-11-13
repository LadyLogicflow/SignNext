"""Mailversand über das Postfach der Beratungsstelle.

Pflicht in SignNext: Das Serverzertifikat UND der Hostname werden geprüft (ssl.create_default_context). Port 465 = TLS von Anfang an, Port 587 nur mit
STARTTLS; wird STARTTLS nicht angeboten oder schlägt die Prüfung fehl, bricht der Versand ab (kein Rückfall auf unverschlüsselt, keine Anmeldung vorher).
Das Ziel wird einmal aufgelöst und geprüft (nur öffentliche Adressen, nur Hostnamen, nur Port 465/587); verbunden wird zu dieser aufgelösten Adresse,
die TLS-Prüfung läuft gegen den Hostnamen (kein DNS-Rebinding zwischen Prüfung und Verbindung).

Fehler tragen nur einen Code, nie Text vom Server und nie Zugangsdaten. ``definitiv`` = es wurde nichts zugestellt, ein neuer Versuch ist gefahrlos;
``unklar`` = die Verbindung brach nach dem Beginn der Datenübertragung ab, ob die Mail den Server erreichte, ist offen (nie automatisch wiederholen)."""
import email.utils
import ipaddress
import re
import smtplib
import socket
import ssl
from email.message import EmailMessage

ERLAUBTE_PORTS = (465, 587)
TIMEOUT = 20
_HOSTNAME = re.compile(r"^(?=.{1,253}\Z)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
_ADRESSE = re.compile(r"^[^@\s<>\"',;:()\[\]\\]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\Z")


class MailFehler(Exception):
    """code: konfiguration | tls_zertifikat | tls_nicht_angeboten | tls_fehler | verbindung | anmeldung | absender_abgelehnt | empfaenger_abgelehnt | abgelehnt | unklar."""

    def __init__(self, code: str, definitiv: bool = True):
        super().__init__(code)
        self.code = code
        self.definitiv = definitiv


def adresse_gueltig(adresse: str) -> bool:
    return bool(adresse) and len(adresse) <= 254 and bool(_ADRESSE.match(adresse))


def _oeffentlich(ip_text: str) -> bool:
    ip = ipaddress.ip_address(ip_text.split("%")[0])
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _aufloesen(host: str, port: int) -> list:
    return sorted({a[4][0] for a in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})


def _ssl_kontext() -> ssl.SSLContext:
    """Zertifikat und Hostname werden geprüft. (Nur diese Stelle erzeugt den Kontext; Tests ersetzen sie durch eine Prüfung gegen eine Test-CA.)"""
    return ssl.create_default_context()


def ziel_pruefen(host: str, port) -> tuple[str, int, str]:
    """(Hostname, Port, aufgelöste Adresse). Wirft ValueError mit einem Anzeigetext, der keine Zugangsdaten enthalten kann."""
    host = str(host or "").strip().lower().rstrip(".")
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("Der Port muss 465 oder 587 sein.")
    if port not in ERLAUBTE_PORTS:
        raise ValueError("Der Port muss 465 (TLS) oder 587 (STARTTLS) sein.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Bitte den Mailserver als Namen angeben (z. B. mail.beispiel.de), nicht als IP-Adresse.")
    if not _HOSTNAME.match(host):
        raise ValueError("Der Name des Mailservers ist ungültig.")
    try:
        adressen = _aufloesen(host, port)
    except OSError:
        raise ValueError("Der Mailserver wurde nicht gefunden.")
    if not adressen or not all(_oeffentlich(a) for a in adressen):
        raise ValueError("Dieser Mailserver ist nicht zulässig.")
    return host, port, adressen[0]


class _Verbindung:
    """Gemeinsame Verbindungslogik: zur geprüften Adresse verbinden, TLS gegen den Hostnamen prüfen."""
    _ziel_ip: str

    def _verbinden(self, port, timeout):
        return socket.create_connection((self._ziel_ip, port), timeout, self.source_address)


class _SSL(_Verbindung, smtplib.SMTP_SSL):
    def __init__(self, host, port, ziel_ip, kontext, timeout):
        self._ziel_ip = ziel_ip
        super().__init__(host, port, timeout=timeout, context=kontext)

    def _get_socket(self, host, port, timeout):
        return self.context.wrap_socket(self._verbinden(port, timeout), server_hostname=self._host)


class _Klar(_Verbindung, smtplib.SMTP):
    def __init__(self, host, port, ziel_ip, timeout):
        self._ziel_ip = ziel_ip
        super().__init__(host, port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        return self._verbinden(port, timeout)


def _implizit_tls(port: int) -> bool:
    """465 = TLS von Anfang an, 587 = STARTTLS."""
    return port == 465


def _verbindung_aufbauen(host: str, port: int, ip: str):
    kontext = _ssl_kontext()
    try:
        if _implizit_tls(port):
            return _SSL(host, port, ip, kontext, TIMEOUT)
        srv = _Klar(host, port, ip, TIMEOUT)
        try:
            srv.ehlo()
            if not srv.has_extn("starttls"):
                raise MailFehler("tls_nicht_angeboten")
            srv.starttls(context=kontext)
            srv.ehlo()
        except BaseException:
            try:
                srv.close()
            except Exception:
                pass
            raise
        return srv
    except MailFehler:
        raise
    except (ssl.SSLCertVerificationError, ssl.CertificateError):
        raise MailFehler("tls_zertifikat")
    except ssl.SSLError:
        raise MailFehler("tls_fehler")
    except (smtplib.SMTPException, OSError):
        raise MailFehler("verbindung")


def nachricht_bauen(absender_adresse: str, absender_name: str, an: str, betreff: str, text: str) -> EmailMessage:
    """Reine Textmail. EmailMessage weist Zeilenumbrüche in Kopfzeilen zurück (kein Header-Injection)."""
    m = EmailMessage()
    m["From"] = email.utils.formataddr((absender_name, absender_adresse)) if absender_name else absender_adresse
    m["To"] = an
    m["Subject"] = betreff
    m["Date"] = email.utils.formatdate(localtime=False)
    m["Message-ID"] = email.utils.make_msgid(domain=absender_adresse.split("@")[-1])
    m.set_content(text)
    return m


def verbindungsinfo(srv) -> dict:
    """TLS-Version, Verfahren und Eckdaten des geprüften Serverzertifikats (für den Betriebsbeleg; keine Geheimnisse)."""
    sock = getattr(srv, "sock", None)
    if not isinstance(sock, ssl.SSLSocket):
        return {}
    info = {"tls_version": sock.version(), "cipher": (sock.cipher() or ("",))[0]}
    try:
        cert = sock.getpeercert() or {}
        aussteller = dict(x[0] for x in cert.get("issuer", ()))
        info["zertifikat_gueltig_bis"] = cert.get("notAfter", "")
        info["aussteller"] = aussteller.get("commonName") or aussteller.get("organizationName") or ""
    except (ValueError, OSError):
        pass
    return info


def senden(zugang: dict, empfaenger: list, nachricht: EmailMessage, info: dict | None = None) -> str:
    """Sendet ``nachricht`` an die Liste ``empfaenger`` (Umschlag; Bcc steht nicht in den Kopfzeilen). Liefert die Message-ID. Wirft MailFehler.
    ``info`` (optional) wird mit den Verbindungsdaten gefüllt (verbindungsinfo).

    zugang: host, port, benutzer, passwort, absender_adresse."""
    try:
        host, port, ip = ziel_pruefen(zugang["host"], zugang["port"])
    except ValueError:
        raise MailFehler("konfiguration")
    srv = _verbindung_aufbauen(host, port, ip)
    try:
        if info is not None:
            info.update(verbindungsinfo(srv))
        try:
            srv.login(zugang["benutzer"], zugang["passwort"])
        except smtplib.SMTPNotSupportedError:
            raise MailFehler("anmeldung")
        except smtplib.SMTPAuthenticationError:
            raise MailFehler("anmeldung")
        except smtplib.SMTPException:
            raise MailFehler("verbindung")
        except OSError:
            raise MailFehler("verbindung")
        try:
            code, _ = srv.mail(zugang["absender_adresse"])
            if code != 250:
                raise MailFehler("absender_abgelehnt")
            for r in empfaenger:
                code, _ = srv.rcpt(r)
                if code not in (250, 251):
                    raise MailFehler("empfaenger_abgelehnt")
        except MailFehler:
            raise
        except smtplib.SMTPSenderRefused:
            raise MailFehler("absender_abgelehnt")
        except smtplib.SMTPRecipientsRefused:
            raise MailFehler("empfaenger_abgelehnt")
        except (smtplib.SMTPException, OSError):
            raise MailFehler("verbindung")
        try:
            # smtplib.data() wirft bei einer Ablehnung NICHT, es liefert nur den Code: erst 250 heißt "vom Server angenommen".
            code, _ = srv.data(nachricht.as_bytes())
        except smtplib.SMTPDataError:
            raise MailFehler("abgelehnt")
        except (smtplib.SMTPException, OSError):
            raise MailFehler("unklar", definitiv=False)
        if code != 250:
            raise MailFehler("abgelehnt")
        try:
            srv.quit()
        except Exception:
            pass
    finally:
        try:
            srv.close()
        except Exception:
            pass
    return nachricht["Message-ID"]
