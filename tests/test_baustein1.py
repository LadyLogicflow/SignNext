"""Baustein 1: Portal-Anschluss, Rollen, Mandantentrennung, Audit, Löschung. Nur erfundene Daten, SortNext ist durchgehend ersetzt."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import requests

import config
import db
import portal_auth
from app import create_app


class Antwort:
    def __init__(self, status=200, daten=None, text_ok=True):
        self.status_code = status
        self._daten = daten
        self._ok = text_ok

    def json(self):
        if not self._ok:
            raise ValueError("kein JSON")
        return self._daten


STANDARD = object()


def me(email="a@test.example", rolle="bst_admin", ref="1001", produkte=STANDARD, **extra):
    if produkte is STANDARD:
        produkte = [{"produkt": "signnext", "externe_referenz": ref}]
    d = {"email": email, "rolle": rolle, "produkte": produkte, "betreiber": False, "onboarding_incomplete": False}
    d.update(extra)
    return d


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(os.path.join(self.tmp.name, "t.db"))
        self.c = self.app.test_client()
        portal_auth.cache_leeren()
        self.sortnext = {}          # cookie -> Antwort oder Exception
        self.aufrufe = []
        p = mock.patch("portal_auth.requests.get", side_effect=self._get)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(portal_auth.cache_leeren)

    def _get(self, url, cookies=None, timeout=None):
        self.aufrufe.append((url, dict(cookies or {}), timeout))
        a = self.sortnext.get((cookies or {}).get("bst_session"))
        if isinstance(a, Exception):
            raise a
        return a if a is not None else Antwort(401, {"error": "not authenticated"})

    def als(self, cookie, daten, status=200):
        self.sortnext[cookie] = Antwort(status, daten)
        self.c.set_cookie("bst_session", cookie, domain="localhost")

    def vorgang(self, ref, status="neu"):
        with db.verbindung() as c:
            return c.execute("INSERT INTO vorgang(mandant_ref, status, erstellt) VALUES (?,?,?)", (ref, status, db.jetzt())).lastrowid

    def audit_zeilen(self, aktion=None):
        with db.verbindung() as c:
            q = "SELECT * FROM audit_log" + (" WHERE aktion=?" if aktion else "")
            return [dict(z) for z in c.execute(q, (aktion,) if aktion else ())]


class TestGeruest(Basis):
    def test_health_und_version(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")
        self.assertEqual(self.c.get("/api/version").get_json()["app"], "signnext")

    def test_antwortkoepfe(self):
        r = self.c.get("/health")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    def test_abhaengigkeiten_exakt_gepinnt_und_ohne_pymupdf(self):
        pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt")
        with open(pfad, encoding="utf-8") as f:
            zeilen = [z.strip() for z in f if z.strip() and not z.startswith("#")]
        self.assertTrue(zeilen)
        for z in zeilen:
            self.assertRegex(z, r"^[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9.]*$", z)
            self.assertNotIn("mupdf", z.lower())
            self.assertNotIn("fitz", z.lower())


class TestAnmeldung(Basis):
    ROUTEN = ["/api/v1/me", "/api/v1/vorgaenge", "/api/v1/vorgaenge/1"]

    def test_ohne_cookie_401_ohne_anfrage_an_sortnext(self):
        for r in self.ROUTEN:
            self.assertEqual(self.c.get(r).status_code, 401, r)
        self.assertEqual(self.aufrufe, [])

    def test_gueltig_admin_und_mitarbeiter(self):
        for rolle, erwartet in (("bst_admin", "admin"), ("mitarbeiter", "mitarbeiter"), ("bst_user", "mitarbeiter")):
            portal_auth.cache_leeren()
            self.als("c-" + rolle, me(rolle=rolle))
            j = self.c.get("/api/v1/me").get_json()
            self.assertEqual(j["rolle"], erwartet)
            self.assertEqual(j["email"], "a@test.example")

    def test_abfrage_geht_an_sortnext_mit_cookie(self):
        self.als("tok", me())
        self.c.get("/api/v1/me")
        url, cookies, timeout = self.aufrufe[0]
        self.assertEqual(url, config.sortnext_basis_url() + "/api/v1/portal/me")
        self.assertEqual(cookies, {"bst_session": "tok"})
        self.assertLessEqual(timeout, 10)

    def test_sortnext_lehnt_ab_401(self):
        self.als("tok", {"error": "not authenticated"}, status=401)
        self.assertEqual(self.c.get("/api/v1/me").status_code, 401)

    def test_ohne_produktzugriff_403_und_audit(self):
        for produkte in ([], [{"produkt": "booknext", "externe_referenz": "7"}], [{"produkt": "sortnext", "externe_referenz": "1001"}], "signnext", None):
            portal_auth.cache_leeren()
            self.als("tok", me(produkte=produkte))
            for r in self.ROUTEN:
                a = self.c.get(r)
                self.assertEqual(a.status_code, 403, (produkte, r))
                self.assertEqual(a.get_json()["grund"], "kein_produktzugriff")
        z = self.audit_zeilen("zugriff_verweigert")
        self.assertTrue(z)
        self.assertTrue(all(x["mandant_ref"] is None and x["details"] == "kein_produktzugriff" for x in z))

    def test_rollen_ohne_mandantenzugriff(self):
        for rolle in ("super_admin", "irgendwas", None, 5):
            portal_auth.cache_leeren()
            self.als("tok", me(rolle=rolle))
            a = self.c.get("/api/v1/me")
            self.assertEqual(a.status_code, 403, rolle)
            self.assertEqual(a.get_json()["grund"], "rolle_nicht_erlaubt")

    def test_onboarding_unvollstaendig(self):
        self.als("tok", me(onboarding_incomplete=True))
        a = self.c.get("/api/v1/me")
        self.assertEqual((a.status_code, a.get_json()["grund"]), (403, "onboarding_unvollstaendig"))

    def test_mehrere_beratungsstellen_fail_closed(self):
        self.als("tok", me(produkte=[{"produkt": "signnext", "externe_referenz": "1001"}, {"produkt": "signnext", "externe_referenz": "2002"}]))
        for r in self.ROUTEN:
            a = self.c.get(r)
            self.assertEqual((a.status_code, a.get_json()["grund"]), (403, "mehrere_beratungsstellen"), r)

    def test_doppelter_gleicher_eintrag_ist_eindeutig(self):
        self.als("tok", me(produkte=[{"produkt": "signnext", "externe_referenz": "1001"}] * 2))
        self.assertEqual(self.c.get("/api/v1/me").status_code, 200)

    def test_ungueltige_referenz_403(self):
        for ref in ("", "  ", "../x", "a b", "x" * 65, 1001, None, "1001;DROP"):
            portal_auth.cache_leeren()
            self.als("tok", me(produkte=[{"produkt": "signnext", "externe_referenz": ref}]))
            a = self.c.get("/api/v1/me")
            self.assertEqual((a.status_code, a.get_json()["grund"]), (403, "referenz_ungueltig"), repr(ref))

    def test_sortnext_ausfall_503_nie_erlaubt(self):
        self.als("tok", me())
        for fehler in (requests.ConnectionError("weg"), requests.Timeout("lang"), Antwort(500, {}), Antwort(502, {}), Antwort(200, None, text_ok=False),
                       Antwort(200, ["liste"]), Antwort(200, {"produkte": []})):
            portal_auth.cache_leeren()
            self.sortnext["tok"] = fehler
            for r in self.ROUTEN:
                self.assertEqual(self.c.get(r).status_code, 503, (fehler, r))

    def test_ausfall_wird_nicht_zwischengespeichert(self):
        self.als("tok", me())
        self.sortnext["tok"] = requests.ConnectionError("weg")
        self.assertEqual(self.c.get("/api/v1/me").status_code, 503)
        self.sortnext["tok"] = Antwort(200, me())
        self.assertEqual(self.c.get("/api/v1/me").status_code, 200)

    def test_zwischenspeicher_30_sekunden(self):
        self.als("tok", me())
        t = [1000.0]
        with mock.patch("portal_auth.time.monotonic", side_effect=lambda: t[0]):
            self.assertEqual(self.c.get("/api/v1/me").status_code, 200)
            self.assertEqual(self.c.get("/api/v1/me").status_code, 200)
            self.assertEqual(len(self.aufrufe), 1)
            self.sortnext["tok"] = Antwort(200, me(produkte=[]))        # Freischaltung zurückgenommen
            t[0] += portal_auth.CACHE_SEKUNDEN - 1
            self.assertEqual(self.c.get("/api/v1/me").status_code, 200)
            t[0] += 2
            self.assertEqual(self.c.get("/api/v1/me").status_code, 403)
            self.assertEqual(len(self.aufrufe), 2)

    def test_zwischenspeicher_je_cookie(self):
        self.als("t1", me(email="eins@test.example"))
        self.als("t2", me(email="zwei@test.example", ref="2002"))
        self.c.set_cookie("bst_session", "t1", domain="localhost")
        self.assertEqual(self.c.get("/api/v1/me").get_json()["email"], "eins@test.example")
        self.c.set_cookie("bst_session", "t2", domain="localhost")
        self.assertEqual(self.c.get("/api/v1/me").get_json()["email"], "zwei@test.example")


class TestMandantentrennung(Basis):
    def setUp(self):
        super().setUp()
        self.a1 = self.vorgang("1001")
        self.a2 = self.vorgang("1001")
        self.b1 = self.vorgang("2002")

    def test_liste_nur_eigener_mandant(self):
        self.als("ta", me(ref="1001"))
        ids = [v["id"] for v in self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"]]
        self.assertEqual(sorted(ids), sorted([self.a1, self.a2]))
        self.als("tb", me(email="b@test.example", ref="2002"))
        ids = [v["id"] for v in self.c.get("/api/v1/vorgaenge").get_json()["vorgaenge"]]
        self.assertEqual(ids, [self.b1])

    def test_fremder_vorgang_404_eigener_200(self):
        self.als("ta", me(ref="1001"))
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % self.a1).status_code, 200)
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % self.b1).status_code, 404)
        self.assertEqual(self.c.get("/api/v1/vorgaenge/99999").status_code, 404)
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % self.a1).status_code, 404)
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d" % self.b1).status_code, 200)

    def test_fremder_vorgang_antwort_verraet_nichts(self):
        self.als("ta", me(ref="1001"))
        fremd = self.c.get("/api/v1/vorgaenge/%d" % self.b1)
        nie = self.c.get("/api/v1/vorgaenge/99999")
        self.assertEqual(fremd.get_json(), nie.get_json())

    def test_mandant_vom_client_wird_ignoriert(self):
        self.als("ta", me(ref="1001"))
        kopf = {"X-Mandant": "2002", "X-Mandant-Ref": "2002", "Mandant-Ref": "2002"}
        a = self.c.get("/api/v1/vorgaenge?mandant_ref=2002&externe_referenz=2002", headers=kopf)
        self.assertEqual(sorted(v["id"] for v in a.get_json()["vorgaenge"]), sorted([self.a1, self.a2]))
        self.assertEqual(self.c.get("/api/v1/vorgaenge/%d?mandant_ref=2002" % self.b1, headers=kopf).status_code, 404)

    def test_db_funktionen_filtern(self):
        self.assertIsNone(db.vorgang_lesen("1001", self.b1))
        self.assertIsNotNone(db.vorgang_lesen("2002", self.b1))
        self.assertEqual([v["id"] for v in db.vorgaenge_liste("2002")], [self.b1])
        self.assertEqual(db.vorgaenge_liste("9999"), [])

    def test_loeschung_trifft_nur_den_mandanten(self):
        self.als("ta", me(ref="1001"))
        self.c.get("/api/v1/vorgaenge/%d" % self.a1)
        self.als("tb", me(email="b@test.example", ref="2002"))
        self.c.get("/api/v1/vorgaenge/%d" % self.b1)
        zahlen = db.mandant_loeschen("1001")
        self.assertEqual(zahlen, {"vorgang": 2, "audit_log": 1})
        self.assertEqual(db.vorgaenge_liste("1001"), [])
        self.assertEqual(len(db.vorgaenge_liste("2002")), 1)
        self.assertEqual([z["mandant_ref"] for z in self.audit_zeilen()], ["2002"])

    def test_loeschung_kennt_jede_mandantentabelle(self):
        """Wächter: Wer eine Tabelle mit mandant_ref ergänzt, muss sie in db.MANDANTENTABELLEN (und damit in mandant_loeschen) aufnehmen."""
        with db.verbindung() as c:
            tabellen = [z["name"] for z in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            mit_ref = [t for t in tabellen if any(sp["name"] == "mandant_ref" for sp in c.execute("PRAGMA table_info(%s)" % t))]
        self.assertEqual(sorted(mit_ref), sorted(db.MANDANTENTABELLEN))


class TestRolle(Basis):
    def setUp(self):
        super().setUp()

        @self.app.get("/test/admin")
        @portal_auth.anmeldung_pflicht("admin")
        def _admin():
            return {"ok": True}

    def test_adminroute(self):
        self.als("tm", me(rolle="mitarbeiter"))
        a = self.c.get("/test/admin")
        self.assertEqual((a.status_code, a.get_json()["grund"]), (403, "rolle_nicht_erlaubt"))
        self.assertTrue(self.audit_zeilen("zugriff_verweigert"))
        self.als("tx", me(rolle="bst_admin"))
        self.assertEqual(self.c.get("/test/admin").status_code, 200)


class TestAudit(Basis):
    def test_lesen_wird_protokolliert_ohne_cookie_und_mit_gekuerzter_ip(self):
        v = self.vorgang("1001")
        self.als("geheimes-cookie-xyz", me(ref="1001"))
        self.c.get("/api/v1/vorgaenge/%d" % v, headers={"X-Forwarded-For": "198.51.100.77"})
        z = self.audit_zeilen("vorgang_gelesen")
        self.assertEqual(len(z), 1)
        self.assertEqual((z[0]["mandant_ref"], z[0]["nutzer"], z[0]["details"], z[0]["ip"]), ("1001", "a@test.example", "id=%d" % v, "198.51.100.0"))
        self.assertNotIn("geheimes-cookie-xyz", repr(z))

    def test_vorangestellte_fremde_adresse_wird_ignoriert(self):
        v = self.vorgang("1001")
        self.als("tok", me(ref="1001"))
        self.c.get("/api/v1/vorgaenge/%d" % v, headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.77"})
        self.assertEqual(self.audit_zeilen("vorgang_gelesen")[0]["ip"], "198.51.100.0")

    def test_ip_kuerzen(self):
        self.assertEqual(portal_auth.kuerze_ip("192.0.2.145"), "192.0.2.0")
        self.assertEqual(portal_auth.kuerze_ip("2001:db8:abcd:1234::1"), "2001:db8:abcd::")
        for kaputt in ("", "x", "1.2.3", "<script>", None):
            self.assertEqual(portal_auth.kuerze_ip(kaputt), "")

    def test_audit_ohne_mandantenzugriff_hat_keinen_mandanten(self):
        self.als("tok", me(produkte=[]))
        self.c.get("/api/v1/me")
        z = self.audit_zeilen()
        self.assertEqual([(x["mandant_ref"], x["aktion"]) for x in z], [(None, "zugriff_verweigert")])


if __name__ == "__main__":
    unittest.main()
