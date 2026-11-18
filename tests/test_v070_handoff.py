"""v0.7.0 Handy-Übergabe der Unterschrift: Ablauf, Bindung an Vorgang/Person/Sitzung, Lebensdauer, einmalige Abholung, Verschlüsselung, Begrenzungen, einheitliche Fehlerantwort. Nur erfundene Daten."""
import base64
from datetime import datetime, timedelta, timezone
from unittest import mock

import db
import handoff
from tests.test_baustein4 import BASIS, ORIGIN, ZugangBasis, png_bytes, uri

LINK_UNGUELTIG = "link_ungueltig"


class HandoffBasis(ZugangBasis):
    def setUp(self):
        super().setUp()
        handoff._ip_zeiten.clear()
        self.addCleanup(handoff._ip_zeiten.clear)
        self.handy_client = self.app.test_client()

    def bereit_zum_zeichnen(self):
        self.hash = self.bis_zum_unterschreiben()

    def erzeugen(self, person=1, dokument="B", **kw):
        body = {"person": person}
        if dokument is not False:
            body["dokument"] = dokument
        return self.post("/handoff", body, **kw)

    def neu(self, person=1, dokument="B"):
        r = self.erzeugen(person, dokument)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        return j["handoff_id"], j["url"].split("#", 1)[1], j

    def handy(self, pfad, daten, kopf=None):
        h = dict(ORIGIN)
        h.update(kopf or {})
        return self.handy_client.post("/api/v1/handy" + pfad, json=daten, headers=h, base_url=BASIS)

    def hstatus(self, hid):
        return self.hole("/handoff/" + hid).get_json()

    def abholen(self, hid):
        return self.post("/handoff/%s/abholen" % hid)

    def zeilen(self):
        with db.verbindung() as c:
            return [dict(z) for z in c.execute("SELECT * FROM handoff")]

    def in_der_zukunft(self, **delta):
        return mock.patch("handoff._jetzt", return_value=datetime.now(timezone.utc) + timedelta(**delta))


class TestErzeugen(HandoffBasis):
    def test_antwort_und_nur_der_hash_steht_in_der_datenbank(self):
        self.bereit_zum_zeichnen()
        hid, token, j = self.neu()
        self.assertTrue(j["url"].startswith(BASIS + "/h#"))
        self.assertTrue(j["qr_svg"].startswith("<svg"))
        self.assertNotIn("<script", j["qr_svg"])
        self.assertEqual(j["sekunden"], 600)
        self.assertGreaterEqual(len(token), 40)
        self.assertNotIn(token, self.intern())                                                          # weder Datenbank noch Log noch Audit enthalten den Token
        (z,) = self.zeilen()
        self.assertEqual((z["status"], z["person"], z["bild_enc"]), ("wartet", 1, None))

    def test_nur_im_schritt_unterschrift_und_nur_fuer_die_naechste_person(self):
        self.starten()
        self.einloggen()
        self.assertEqual(self.erzeugen().get_json()["error"], "schritt_falsch")                         # Seiten noch nicht gelesen
        self.lesen()
        self.assertEqual(self.erzeugen().get_json()["error"], "schritt_falsch")                         # Texte noch nicht angezeigt
        self.texte_holen()
        self.assertEqual(self.erzeugen(2).get_json()["error"], "schritt_falsch")                        # Person 1 kommt zuerst
        self.assertEqual(self.erzeugen(1).status_code, 200)
        self.assertEqual(len(self.zeilen()), 1)

    def test_eingabe_wird_streng_geprueft(self):
        self.bereit_zum_zeichnen()
        for body in ({"person": 0}, {"person": 3}, {"person": True}, {"person": "1"}, {"person": None}, {"person": 1.5}):
            r = self.post("/handoff", body)
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "person_ungueltig"), body)
        for body in ({}, {"person": 1, "x": 1}, [], "x", None):
            self.assertEqual(self.post("/handoff", body).status_code, 400, body)
        self.assertEqual(self.zeilen(), [])

    def test_ohne_sitzung_und_ohne_origin_geht_nichts(self):
        self.bereit_zum_zeichnen()
        fremd = self.app.test_client()
        r = fremd.post("/api/v1/zugang/handoff", json={"person": 1}, headers=ORIGIN, base_url=BASIS)
        self.assertEqual(r.status_code, 401)
        r = self.m.post("/api/v1/zugang/handoff", json={"person": 1}, headers=self.ua, base_url=BASIS)        # Cookie vorhanden, aber kein Origin
        self.assertNotEqual(r.status_code, 200)
        r = self.m.post("/api/v1/zugang/handoff", json={"person": 1}, headers=dict(self.ua, Origin="https://boese.example"), base_url=BASIS)
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(self.zeilen(), [])

    def test_neue_uebergabe_ersetzt_die_alte(self):
        self.bereit_zum_zeichnen()
        h1, t1, _ = self.neu()
        h2, t2, _ = self.neu()
        self.assertEqual(len(self.zeilen()), 1)
        self.assertEqual(self.handy("/info", {"token": t1}).get_json()["error"], LINK_UNGUELTIG)
        self.assertEqual(self.handy("/info", {"token": t2}).status_code, 200)
        self.assertEqual(self.hstatus(h1)["status"], "abgelaufen")

    def test_hoechstens_zehn_je_stunde(self):
        self.bereit_zum_zeichnen()
        for _ in range(10):
            self.assertEqual(self.erzeugen().status_code, 200)
        r = self.erzeugen()
        self.assertEqual((r.status_code, r.get_json()["error"]), (429, "zu_viele_anfragen"))
        with self.in_der_zukunft(hours=2):
            self.assertEqual(self.erzeugen().status_code, 200)


class TestAblauf(HandoffBasis):
    def test_vom_qr_bis_zur_unterschrift(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        self.assertEqual(self.hstatus(hid)["status"], "wartet")
        r = self.handy("/info", {"token": token})
        self.assertEqual((r.status_code, r.get_json()), (200, {"person": 1, "personen": 2, "dokument": {"rolle": "B", "titel": "Anschreiben", "nr": 1, "von": 2}, "status": "verbunden"}))
        self.assertEqual(self.hstatus(hid)["status"], "verbunden")
        self.assertEqual(self.abholen(hid).get_json()["error"], "noch_nicht_gezeichnet")
        r = self.handy("/unterschrift", {"token": token, "bild": uri(png_bytes())})
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        self.assertEqual(self.hstatus(hid)["status"], "gezeichnet")
        r = self.abholen(hid)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        bild = r.get_json()["bild"]
        self.assertTrue(bild.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(bild.split(",", 1)[1])[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.zeilen(), [])                                                             # sofort gelöscht
        r = self.abholen(hid)
        self.assertEqual((r.status_code, r.get_json()["error"]), (410, "schon_abgeholt"))
        self.assertEqual(self.handy("/info", {"token": token}).get_json()["error"], LINK_UNGUELTIG)     # Link ist verbraucht
        r = self.sign(1, self.hash, bild=bild)                                                          # am Computer wie gewohnt bestätigt
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_bild_liegt_nur_verschluesselt_in_der_datenbank(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        png = png_bytes()
        self.assertEqual(self.handy("/unterschrift", {"token": token, "bild": uri(png)}).status_code, 200)
        (z,) = self.zeilen()
        self.assertIsNotNone(z["bild_enc"])
        self.assertFalse(z["bild_enc"].startswith("data:image"))
        self.assertNotIn(base64.b64encode(png)[:60].decode(), self.intern())
        self.assertNotIn(b"IHDR", self.db_bytes())

    def test_zweites_zeichnen_wird_abgelehnt(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        self.assertEqual(self.handy("/unterschrift", {"token": token, "bild": uri(png_bytes())}).status_code, 200)
        r = self.handy("/unterschrift", {"token": token, "bild": uri(png_bytes(300, 120))})
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "schon_gezeichnet"))
        self.assertEqual(self.handy("/info", {"token": token}).get_json()["error"], "schon_gezeichnet")

    def test_schlechte_bilder_werden_wie_am_computer_abgelehnt(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        faelle = [(5, "bild_format"), ("data:image/jpeg;base64,AAAA", "bild_format"), (uri(b"kein png"), "bild_ungueltig"),
                  (uri(png_bytes(tinte=False)), "bild_leer"), (uri(png_bytes(2000, 800)), "bild_zu_gross"), (uri(png_bytes(apng=True)), "bild_ungueltig")]
        for bild, code in faelle:
            r = self.handy("/unterschrift", {"token": token, "bild": bild})
            self.assertEqual((r.status_code, r.get_json()["error"]), (422, code), code)
        self.assertEqual(self.hstatus(hid)["status"], "wartet")                                          # nichts gespeichert
        self.assertIsNone(self.zeilen()[0]["bild_enc"])

    def test_abbrechen_macht_den_link_ungueltig(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        r = self.m.delete("/api/v1/zugang/handoff/" + hid, headers=dict(self.ua, **ORIGIN), base_url=BASIS)
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        self.assertEqual(self.zeilen(), [])
        self.assertEqual(self.handy("/info", {"token": token}).get_json()["error"], LINK_UNGUELTIG)

    def test_audit_und_logs_ohne_inhalt(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        png = png_bytes()
        self.handy("/unterschrift", {"token": token, "bild": uri(png)})
        self.abholen(hid)
        text = self.intern()
        for aktion in ("handoff_erzeugt", "handoff_gezeichnet", "handoff_abgeholt"):
            self.assertIn(aktion, text)
        self.assertNotIn(token, text)
        self.assertNotIn(hid + "x", text)


class TestBindung(HandoffBasis):
    def test_unterschiedliche_fehler_sehen_gleich_aus(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        gleich = self.handy("/info", {"token": "A" * 43})
        self.assertEqual(gleich.status_code, 404)
        antworten = [gleich.get_json()]
        for body in ({"token": "kurz"}, {"token": 5}, {"token": token, "x": 1}, {}, {"token": None}):
            r = self.handy("/info", body)
            self.assertEqual(r.status_code, 404, body)
            antworten.append(r.get_json())
        with self.in_der_zukunft(minutes=11):                                                           # abgelaufen
            r = self.handy("/info", {"token": token})
            self.assertEqual(r.status_code, 404)
            antworten.append(r.get_json())
        for a in antworten:
            self.assertEqual(a, antworten[0])

    def test_status_und_abholen_nur_mit_der_eigenen_sitzung(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        self.handy("/unterschrift", {"token": token, "bild": uri(png_bytes())})
        fremd = self.app.test_client()
        self.assertEqual(fremd.get("/api/v1/zugang/handoff/" + hid, headers=self.ua, base_url=BASIS).status_code, 401)
        r = fremd.post("/api/v1/zugang/handoff/%s/abholen" % hid, json=None, headers=dict(self.ua, **ORIGIN), base_url=BASIS)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(len(self.zeilen()), 1)                                                         # das Bild ist noch da
        self.assertEqual(self.hstatus("0" * 24)["status"], "abgelaufen")                                 # fremde/unbekannte Kennung verrät nichts
        self.assertEqual(self.abholen(hid).status_code, 200)

    def test_zweite_gueltige_sitzung_desselben_vorgangs_sieht_die_uebergabe_nicht(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        self.handy("/unterschrift", {"token": token, "bild": uri(png_bytes())})
        c2 = self.app.test_client()
        self.assertEqual(self.pruefen(client=c2).status_code, 200)                                      # zweite Anmeldung mit gleichem Link und Code
        self.assertEqual(self.hole("/handoff/" + hid, client=c2).get_json()["status"], "abgelaufen")
        r = self.post("/handoff/%s/abholen" % hid, client=c2)
        self.assertEqual((r.status_code, r.get_json()["error"]), (410, "schon_abgeholt"))
        self.assertEqual(len(self.zeilen()), 1)

    def test_nach_der_unterschrift_am_computer_gilt_der_link_nicht_mehr(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        self.assertEqual(self.sign(1, self.hash).status_code, 200)                                      # Person 1 hat am Computer unterschrieben, nächste ist Person 2
        self.assertEqual(self.handy("/info", {"token": token}).get_json()["error"], LINK_UNGUELTIG)
        r = self.handy("/unterschrift", {"token": token, "bild": uri(png_bytes())})
        self.assertEqual((r.status_code, r.get_json()["error"]), (404, LINK_UNGUELTIG))

    def test_gesperrter_zugang_macht_den_link_ungueltig(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        with db.verbindung() as c:
            c.execute("UPDATE link SET gesperrt=1")
        self.assertEqual(self.handy("/info", {"token": token}).get_json()["error"], LINK_UNGUELTIG)

    def test_abgelaufen_wird_aufgeraeumt_und_gemeldet(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        self.handy("/unterschrift", {"token": token, "bild": uri(png_bytes())})
        with self.in_der_zukunft(minutes=11):
            self.assertEqual(handoff.aufraeumen(), 1)
        self.assertEqual(self.zeilen(), [])
        self.assertEqual(self.hstatus(hid)["status"], "abgelaufen")
        self.assertEqual(self.abholen(hid).get_json()["error"], "schon_abgeholt")

    def test_handy_braucht_gleichen_ursprung_und_ist_begrenzt(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu()
        r = self.handy_client.post("/api/v1/handy/info", json={"token": token}, headers={"Origin": "https://boese.example"}, base_url=BASIS)
        self.assertNotEqual(r.status_code, 200)
        with mock.patch("handoff.HANDY_LIMIT", 3):
            codes = [self.handy("/info", {"token": "B" * 43}).status_code for _ in range(5)]
        self.assertEqual(codes, [404, 404, 404, 429, 429])


class TestJeDokument(HandoffBasis):
    """Einzel-Signatur: Die Übergabe gilt für genau ein Dokument und die nächste Person darin."""

    def test_dokument_ist_pflicht_und_muss_dran_sein(self):
        self.bereit_zum_zeichnen()
        for dokument in (False, "X", "M", None, 5):
            r = self.erzeugen(1, dokument)
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "dokument_ungueltig"), dokument)
        self.lesen(dokument="A")                                                                        # Protokoll gelesen, aber das Anschreiben ist noch dran
        r = self.erzeugen(1, "A")
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "schritt_falsch"))
        self.assertEqual(self.zeilen(), [])
        self.assertEqual(self.erzeugen(1, "B").status_code, 200)
        self.assertEqual(self.zeilen()[0]["dokument_rolle"], "B")

    def test_handoff_fuer_das_zweite_dokument(self):
        self.bereit_zum_zeichnen()
        self.assertEqual(self.sign(1, self.hash).status_code, 200)                                      # Anschreiben: Person 1 und 2 am Computer
        self.assertEqual(self.sign(2, self.texte_holen()["hash"]).get_json()["naechste"], {"dokument": "A", "person": 1})
        r = self.erzeugen(1, "A")                                                                       # Protokoll noch nicht gelesen
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "schritt_falsch"))
        self.lesen()
        hid, token, _ = self.neu(1, "A")
        r = self.handy("/info", {"token": token})
        self.assertEqual(r.get_json()["dokument"], {"rolle": "A", "titel": "Protokoll", "nr": 2, "von": 2})
        self.assertEqual(self.handy("/unterschrift", {"token": token, "bild": uri(png_bytes())}).status_code, 200)
        bild = self.abholen(hid).get_json()["bild"]
        r = self.sign(1, "", bild=bild)                                                                 # am Computer bestätigt: Zustimmung stammt aus dem Anschreiben
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["naechste"], {"dokument": "A", "person": 2})

    def test_link_fuer_ein_dokument_gilt_nicht_mehr_wenn_es_nicht_dran_ist(self):
        self.bereit_zum_zeichnen()
        hid, token, _ = self.neu(1, "B")
        self.assertEqual(self.handy("/info", {"token": token}).status_code, 200)
        with db.verbindung() as c:                                                                      # Zustand verfälscht: Übergabe nennt ein anderes Dokument
            c.execute("UPDATE handoff SET dokument_rolle='A'")
        r = self.handy("/info", {"token": token})
        self.assertEqual((r.status_code, r.get_json()["error"]), (404, LINK_UNGUELTIG))


if __name__ == "__main__":
    import unittest
    unittest.main()
