"""Kleiner SMTP-Testserver (nur für Tests, 127.0.0.1): TLS von Anfang an oder STARTTLS, AUTH PLAIN/LOGIN, einstellbare Ablehnungen und Abbrüche.
Dazu Test-Zertifikate (eigene Test-CA), erzeugt mit cryptography."""
import base64
import datetime
import socket
import socketserver
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def zertifikate(verzeichnis: str, hostnamen=("mail.test.example",), name="server") -> tuple:
    """(ca_pfad, cert_pfad, key_pfad): Test-CA und ein von ihr signiertes Serverzertifikat für ``hostnamen``."""
    jetzt = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SignNext Test-CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(jetzt - datetime.timedelta(days=1)).not_valid_after(jetzt + datetime.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnamen[0])])).issuer_name(ca_name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(jetzt - datetime.timedelta(days=1)).not_valid_after(jetzt + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hostnamen]), critical=False).sign(ca_key, hashes.SHA256()))
    pfade = ("%s/ca-%s.pem" % (verzeichnis, name), "%s/cert-%s.pem" % (verzeichnis, name), "%s/key-%s.pem" % (verzeichnis, name))
    with open(pfade[0], "wb") as f:
        f.write(ca.public_bytes(serialization.Encoding.PEM))
    with open(pfade[1], "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(pfade[2], "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return pfade


class _Leser:
    def __init__(self, conn):
        self.conn = conn
        self.puffer = b""

    def zeile(self):
        while b"\r\n" not in self.puffer:
            try:
                teil = self.conn.recv(4096)
            except (OSError, ssl.SSLError):
                return b""
            if not teil:
                return b""
            self.puffer += teil
        z, _, self.puffer = self.puffer.partition(b"\r\n")
        return z + b"\r\n"


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.verbindung = self.request
        try:
            self._bearbeiten()
        finally:
            for k in (self.verbindung, self.request):
                try:
                    k.close()
                except OSError:
                    pass

    def _bearbeiten(self):
        srv = self.server.cfg
        conn = self.request
        conn.settimeout(10)
        tls_aktiv = False
        try:
            if srv.tls == "implicit":
                conn = srv.ctx.wrap_socket(conn, server_side=True)
                self.verbindung = conn
                tls_aktiv = True
        except (ssl.SSLError, OSError):
            return
        lese = _Leser(conn)
        conn.sendall(b"220 test ESMTP\r\n")
        mail_from, rcpts = None, []
        while True:
            zeile = lese.zeile()
            if not zeile:
                return
            teile = zeile.decode("utf-8", "replace").strip().split(" ", 1)
            verb = teile[0].upper()
            srv.befehle.append(verb)
            if verb in ("EHLO", "HELO"):
                antwort = b"250-test\r\n"
                if tls_aktiv or srv.tls is None or srv.auth_ohne_tls:
                    antwort += b"250-AUTH PLAIN LOGIN\r\n"
                if srv.starttls_angeboten and srv.tls == "starttls" and not tls_aktiv:
                    antwort += b"250-STARTTLS\r\n"
                conn.sendall(antwort + b"250 8BITMIME\r\n")
            elif verb == "STARTTLS":
                conn.sendall(b"220 Ready\r\n")
                try:
                    conn = srv.ctx.wrap_socket(conn, server_side=True)
                    self.verbindung = conn
                except (ssl.SSLError, OSError):
                    return
                lese = _Leser(conn)
                tls_aktiv = True
            elif verb == "AUTH":
                art, _, rest = (teile[1] if len(teile) > 1 else "").partition(" ")
                if art.upper() == "PLAIN":
                    if not rest:
                        conn.sendall(b"334 \r\n")
                        rest = lese.zeile().decode().strip()
                    _, benutzer, pw = base64.b64decode(rest).decode("utf-8").split("\0")
                else:
                    conn.sendall(b"334 " + base64.b64encode(b"Username:") + b"\r\n")
                    benutzer = base64.b64decode(lese.zeile().strip()).decode()
                    conn.sendall(b"334 " + base64.b64encode(b"Password:") + b"\r\n")
                    pw = base64.b64decode(lese.zeile().strip()).decode()
                srv.anmeldungen.append((benutzer, pw))
                if (benutzer, pw) == (srv.benutzer, srv.passwort):
                    srv.angemeldet = True
                    conn.sendall(b"235 2.7.0 Authentication successful\r\n")
                else:
                    conn.sendall(b"535 5.7.8 Authentication failed\r\n")
            elif verb == "MAIL":
                if srv.mail_ablehnen:
                    conn.sendall(b"550 5.7.1 sender rejected\r\n")
                    continue
                mail_from, rcpts = teile[1], []
                conn.sendall(b"250 ok\r\n")
            elif verb == "RCPT":
                adresse = teile[1].split(":", 1)[1].strip().strip("<>").lower()
                if adresse in srv.rcpt_ablehnen:
                    conn.sendall(b"550 5.1.1 no such user\r\n")
                else:
                    rcpts.append(adresse)
                    conn.sendall(b"250 ok\r\n")
            elif verb == "DATA":
                conn.sendall(b"354 go\r\n")
                daten = b""
                while True:
                    z = lese.zeile()
                    if not z:
                        return
                    if z == b".\r\n":
                        break
                    daten += z[1:] if z.startswith(b"..") else z
                if srv.nach_data_abbrechen:
                    conn.close()
                    return
                conn.sendall(srv.data_antwort.encode() + b"\r\n")
                if srv.data_antwort.startswith("250"):
                    srv.nachrichten.append({"von": mail_from, "an": list(rcpts), "daten": daten, "angemeldet": srv.angemeldet, "tls": tls_aktiv})
            elif verb == "QUIT":
                conn.sendall(b"221 bye\r\n")
                return
            else:
                conn.sendall(b"250 ok\r\n")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeSmtp:
    def __init__(self, tls=None, zert=None, benutzer="bst@example.org", passwort="Test-Passwort-ohne-Bedeutung-1", starttls_angeboten=True,
                 rcpt_ablehnen=(), data_antwort="250 queued", nach_data_abbrechen=False, auth_ohne_tls=False, mail_ablehnen=False):
        self.tls, self.benutzer, self.passwort = tls, benutzer, passwort
        self.starttls_angeboten, self.rcpt_ablehnen = starttls_angeboten, tuple(a.lower() for a in rcpt_ablehnen)
        self.data_antwort, self.nach_data_abbrechen, self.auth_ohne_tls = data_antwort, nach_data_abbrechen, auth_ohne_tls
        self.mail_ablehnen = mail_ablehnen
        self.nachrichten, self.anmeldungen, self.befehle, self.angemeldet = [], [], [], False
        self.ctx = None
        if tls:
            self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.ctx.load_cert_chain(zert[1], zert[2])
        self._srv = _Server(("127.0.0.1", 0), _Handler)
        self._srv.cfg = self
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()
