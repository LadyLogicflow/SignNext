"""v0.7.0 Schriften (woff2) aus ui/fonts/: nur diese Dateien, font-src 'self' und sonst keine Lockerung der Content-Security-Policy. Nur erfundene Daten."""
import tempfile
from pathlib import Path
from unittest import mock

import ui_ausliefern
from tests.test_baustein1 import Basis

CSP_VORHER = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-src 'self'; "
              "form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'")


class TestSchriften(Basis):
    def setUp(self):
        super().setUp()
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        ordner = Path(self.t.name)
        (ordner / "fonts").mkdir()
        (ordner / "fonts" / "Inter-Regular.woff2").write_bytes(b"wOF2-demo")
        (ordner / "fonts" / "lizenz.txt").write_text("OFL", encoding="utf-8")
        (ordner / "fonts" / "boese.js").write_text("x", encoding="utf-8")
        (ordner / "unterschrift.html").write_text("<!DOCTYPE html><title>x</title>", encoding="utf-8")
        (ordner / "geheim.woff2").write_bytes(b"nicht in fonts")
        p = mock.patch.object(ui_ausliefern, "ORDNER", ordner)
        p.start()
        self.addCleanup(p.stop)

    def test_schrift_wird_mit_richtigem_typ_und_headern_ausgeliefert(self):
        r = self.c.get("/fonts/Inter-Regular.woff2")
        self.assertEqual((r.status_code, r.get_data(), r.headers["Content-Type"]), (200, b"wOF2-demo", "font/woff2"))
        self.assertIn("no-store", r.headers["Cache-Control"])
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    def test_nur_woff2_aus_dem_schriftenordner(self):
        for pfad in ("/fonts/lizenz.txt", "/fonts/boese.js", "/fonts/", "/fonts", "/fonts/..%2fgeheim.woff2", "/fonts/../geheim.woff2", "/fonts/%2e%2e/geheim.woff2",
                     "/fonts/Inter-Regular.WOFF2", "/fonts/gibt-es-nicht.woff2", "/fonts/Inter-Regular.woff2/", "/geheim.woff2", "/Inter-Regular.woff2", "/fonts/a/b.woff2"):
            self.assertEqual(self.c.get(pfad).status_code in (404, 308, 301), True, pfad)
            self.assertNotIn(b"nicht in fonts", self.c.get(pfad).get_data(), pfad)
        for methode in ("post", "put", "delete", "patch"):
            self.assertEqual(getattr(self.c, methode)("/fonts/Inter-Regular.woff2").status_code, 405, methode)

    def test_csp_nur_um_font_src_erweitert(self):
        csp = ui_ausliefern.CSP
        self.assertIn("font-src 'self'", csp)
        self.assertEqual(csp.replace(" font-src 'self';", ""), CSP_VORHER)                                # alles andere unverändert
        self.assertEqual(self.c.get("/u").headers["Content-Security-Policy"], csp)
