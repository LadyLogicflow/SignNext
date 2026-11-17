"""v0.7.0 Demo-Offen-Modus (SIGNNEXT_DEMO_ANMELDUNG=offen): Beraterseiten ohne Portal-Login, nur Heimnetz/Tailnet, fail closed. Nur erfundene Daten."""
import os
import tempfile
import unittest
from unittest import mock

import config
import db
import portal_auth
from app import create_app

HAUPT = "testhauptschluessel-mindestens-32-zeichen-lang-0123456789"
BASIS = {"SIGNNEXT_DEMO": "1", "SIGNNEXT_DEMO_ANMELDUNG": "offen", "SIGNNEXT_VERSAND": "testadressen", "SIGNNEXT_BASIS_URL": "https://pi.test:8443", "SIGNNEXT_PROXIES": "1",
         "SIGNNEXT_HAUPTSCHLUESSEL": HAUPT}


def kopf(ip="192.168.1.20", host="pi.test:8443", **extra):
    k = {"X-Forwarded-For": ip, "Host": host}
    k.update(extra)
    return k


class Offen(unittest.TestCase):
    umgebung = BASIS

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = dict(self.umgebung, SIGNNEXT_DATEN=os.path.join(self.tmp.name, "daten"))
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.sortnext = mock.patch("portal_auth.requests.get", side_effect=AssertionError("im Demo darf SortNext nie gefragt werden"))
        self.sortnext.start()
        self.addCleanup(self.sortnext.stop)
        portal_auth.cache_leeren()
        self.app = create_app(os.path.join(self.tmp.name, "t.db"))
        self.c = self.app.test_client()

    def me(self, **kw):
        return self.c.get("/api/v1/me", headers=kopf(**kw))


class TestErlaubt(Offen):
    def test_heimnetz_tailnet_und_loopback_bekommen_die_demo_beratungsstelle(self):
        for ip in ("192.168.1.20", "10.1.2.3", "100.100.178.117", "fd7a:115c:a1e0::7", "fd12:3456::1", "127.0.0.1", "::1"):
            r = self.me(ip=ip)
            self.assertEqual(r.status_code, 200, ip)
            j = r.get_json()
            self.assertEqual((j["demo"], j["rolle"], j["email"]), (True, "admin", "demo-modus"), ip)
            self.assertIn("DEMO", j["demo_banner"])

    def test_host_mit_und_ohne_port_und_localhost(self):
        self.assertEqual(self.me(host="pi.test").status_code, 200)
        self.assertEqual(self.me(host="PI.TEST:8443").status_code, 200)
        self.assertEqual(self.me(host="localhost:8000").status_code, 200)

    def test_zusaetzliche_hosts_und_netze_aus_der_einstellung(self):
        with mock.patch.dict(os.environ, {"SIGNNEXT_DEMO_HOSTS": "pi.local, mein-pi.tail1234.ts.net", "SIGNNEXT_DEMO_NETZE": "172.20.0.0/16"}):
            self.assertEqual(self.me(host="mein-pi.tail1234.ts.net").status_code, 200)
            self.assertEqual(self.me(ip="172.20.5.5").status_code, 200)
            self.assertEqual(self.me(ip="172.18.0.1").status_code, 403)

    def test_die_demo_beratungsstelle_sieht_nur_ihre_vorgaenge(self):
        with db.verbindung() as c:
            c.execute("INSERT INTO vorgang(mandant_ref, status, erstellt) VALUES ('1001','neu',?)", (db.jetzt(),))
            c.execute("INSERT INTO vorgang(mandant_ref, status, erstellt) VALUES ('demo','neu',?)", (db.jetzt(),))
        r = self.c.get("/api/v1/vorgaenge", headers=kopf())
        self.assertEqual(r.status_code, 200)
        self.assertEqual({v["id"] for v in r.get_json()["vorgaenge"]}, {2})

    def test_portal_cookie_wird_im_demo_nie_ausgewertet(self):
        self.c.set_cookie("bst_session", "irgendwas", domain="localhost")
        self.assertEqual(self.me().status_code, 200)                 # SortNext-Abfrage würde den Test mit AssertionError brechen


class TestVerweigert(Offen):
    def test_oeffentliche_und_docker_adressen_werden_abgelehnt(self):
        for ip in ("8.8.8.8", "203.0.113.5", "2001:db8::1", "172.18.0.1", "172.17.0.2", "169.254.1.1", "0.0.0.0", "unsinn", ""):
            r = self.me(ip=ip) if ip else self.c.get("/api/v1/me", headers={"Host": "pi.test:8443"})
            self.assertEqual(r.status_code, 403, ip)
            self.assertEqual(r.get_json(), {"error": "forbidden", "grund": "demo_nicht_erlaubt"}, ip)

    def test_ohne_proxy_kopf_wird_abgelehnt(self):
        r = self.c.get("/api/v1/me", headers={"Host": "pi.test:8443"})
        self.assertEqual(r.status_code, 403)

    def test_vom_client_vorangestellte_adresse_zaehlt_nicht(self):
        self.assertEqual(self.me(ip="192.168.1.5, 8.8.8.8").status_code, 403)                  # zuletzt hängt der Vorsatz die echte Adresse an
        self.assertEqual(self.me(ip="8.8.8.8, 192.168.1.5").status_code, 200)

    def test_fremder_host_kopf_dns_rebinding(self):
        for host in ("boese.example", "boese.example:8443", "pi.test.boese.example", ""):
            self.assertEqual(self.me(host=host).status_code, 403, host)

    def test_fremder_origin_bei_aendernden_aufrufen(self):
        r = self.c.post("/api/v1/vorgaenge/1/versenden", json={"bestaetigt": True}, headers=kopf(Origin="https://boese.example"))
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/v1/vorgaenge/1/versenden", json={"bestaetigt": True}, headers=kopf(Origin="null"))
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/v1/vorgaenge/1/versenden", json={"bestaetigt": True}, headers=kopf(Origin="https://pi.test:8443"))
        self.assertNotEqual(r.status_code, 403)                                                # gleicher Ursprung: weiter zur Fachprüfung (404 ohne Vorgang)

    def test_ablehnung_steht_im_audit_ohne_adresse(self):
        self.me(ip="8.8.8.8")
        with db.verbindung() as c:
            z = [dict(r) for r in c.execute("SELECT * FROM audit_log WHERE aktion='demo_verweigert'")]
        self.assertEqual(len(z), 1)
        self.assertEqual(z[0]["details"], "adresse_nicht_erlaubt")
        self.assertNotIn("8.8.8.8", str(z[0]))                                                  # gekürzt (/24) statt voll


class TestStartPruefung(unittest.TestCase):
    def start(self, **abweichung):
        env = dict(BASIS)
        env.update(abweichung)
        env = {k: v for k, v in env.items() if v is not None}
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, dict(env, SIGNNEXT_DATEN=os.path.join(t, "d"))):
            for k in set(BASIS) - set(env):
                os.environ.pop(k, None)
            return create_app(os.path.join(t, "t.db"))

    def test_gueltige_einstellung_startet(self):
        self.assertIsNotNone(self.start())
        self.assertIsNotNone(self.start(SIGNNEXT_VERSAND="aus"))

    def test_start_wird_verweigert_wenn_eine_bedingung_fehlt(self):
        faelle = [dict(SIGNNEXT_DEMO=None), dict(SIGNNEXT_DEMO="0"), dict(SIGNNEXT_VERSAND="an"), dict(SIGNNEXT_PROXIES="0"), dict(SIGNNEXT_PROXIES="2"),
                  dict(SIGNNEXT_BASIS_URL=None), dict(SIGNNEXT_BASIS_URL="http://pi.test")]
        for f in faelle:
            with self.assertRaises(RuntimeError, msg=str(f)):
                self.start(**f)

    def test_mit_hosts_statt_basis_adresse_startet_es(self):
        self.assertIsNotNone(self.start(SIGNNEXT_BASIS_URL=None, SIGNNEXT_DEMO_HOSTS="pi.local"))

    def test_ohne_offen_einstellung_bleibt_alles_wie_bisher(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {"SIGNNEXT_DATEN": os.path.join(t, "d"), "SIGNNEXT_DEMO": "1"}):
            os.environ.pop("SIGNNEXT_DEMO_ANMELDUNG", None)
            c = create_app(os.path.join(t, "t.db")).test_client()
            r = c.get("/api/v1/me", headers=kopf())
            self.assertEqual(r.status_code, 401)                  # Portal-Cookie bleibt Pflicht, auch aus dem Heimnetz und im Demo


if __name__ == "__main__":
    unittest.main()
