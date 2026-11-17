"""Auslieferung der Oberfläche (ui/): Seiten, Dateien, Sicherheitsheader, keine Pfadtricks, Schutzwächter über die UI-Dateien. Nur erfundene Daten."""
import os
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

import ui_ausliefern
from tests.test_baustein1 import Basis

UI = Path(__file__).resolve().parent.parent / "ui"
SEITEN = {"/u": "unterschrift.html", "/d": "download.html", "/pruefung": "pruefung.html", "/einstellungen": "einstellungen.html", "/vorgaenge": "vorgaenge.html",
          "/mail-zugang": "mail-zugang.html"}


class Sammler(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skripte, self.links, self.handler, self.inline_skripte, self.doppelt = [], [], [], 0, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        namen = [k for k, _ in attrs]
        self.doppelt += [(tag, k) for k in set(namen) if namen.count(k) > 1]
        for k in a:
            if k.startswith("on"):
                self.handler.append((tag, k))
        if tag == "script":
            if a.get("src"):
                self.skripte.append(a["src"])
            else:
                self.inline_skripte += 1
        if tag == "link" and a.get("href"):
            self.links.append(a["href"])


class TestAuslieferung(Basis):
    def test_jede_seite_wird_ausgeliefert(self):
        for pfad, datei in SEITEN.items():
            r = self.c.get(pfad)
            self.assertEqual(r.status_code, 200, pfad)
            self.assertTrue(r.headers["Content-Type"].startswith("text/html"), pfad)
            self.assertEqual(r.get_data(), (UI / datei).read_bytes(), pfad)
        self.assertEqual(self.c.get("/u?x=1").status_code, 200)
        self.assertEqual(self.c.get("/pruefung?id=3").status_code, 200)

    def test_wurzel_leitet_auf_die_vorgangsliste(self):
        r = self.c.get("/")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/vorgaenge"))

    def test_alle_eingebundenen_dateien_sind_erreichbar(self):
        for pfad, datei in SEITEN.items():
            p = Sammler()
            p.feed((UI / datei).read_text(encoding="utf-8"))
            for ref in p.skripte + [h for h in p.links]:
                self.assertFalse(re.match(r"^(https?:)?//", ref), ref)
                r = self.c.get("/" + ref)
                self.assertEqual(r.status_code, 200, "%s bindet %s ein" % (datei, ref))
                self.assertEqual(r.get_data(), (UI / ref).read_bytes())
        self.assertTrue(self.c.get("/app.js").headers["Content-Type"].startswith("text/javascript"))
        self.assertTrue(self.c.get("/style.css").headers["Content-Type"].startswith("text/css"))

    def test_sicherheitsheader_auf_seiten(self):
        for pfad in SEITEN:
            h = self.c.get(pfad).headers
            csp = h["Content-Security-Policy"]
            for teil in ("default-src 'none'", "script-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'none'", "connect-src 'self'"):
                self.assertIn(teil, csp, pfad)
            self.assertNotIn("unsafe-eval", csp)
            self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
            self.assertEqual(h["X-Frame-Options"], "DENY")
            self.assertIn("no-store", h["Cache-Control"])
            self.assertIn("noindex", h["X-Robots-Tag"])
            self.assertEqual(h["Referrer-Policy"], "no-referrer")
            self.assertEqual(h["X-Content-Type-Options"], "nosniff")

    def test_header_auf_dateien(self):
        for name in ("app.js", "style.css"):
            h = self.c.get("/" + name).headers
            self.assertIn("no-store", h["Cache-Control"])
            self.assertIn("noindex", h["X-Robots-Tag"])
            self.assertEqual(h["X-Content-Type-Options"], "nosniff")

    def test_nur_lesen(self):
        for pfad in list(SEITEN) + ["/", "/app.js"]:
            for methode in ("post", "put", "delete", "patch"):
                self.assertEqual(getattr(self.c, methode)(pfad).status_code, 405, (methode, pfad))
        self.assertEqual(self.c.head("/u").status_code, 200)

    def test_keine_pfadtricks_und_keine_fremden_dateien(self):
        wurzel = UI.parent
        for pfad in ("/../db.py", "/..%2fdb.py", "/%2e%2e/db.py", "/ui/app.js", "/app.js/", "/app.js/..", "/db.py", "/README.md", "/VERSION", "/config.py", "/requirements.txt",
                     "/texte", "/texte/ausstieg.txt", "/tests/pdfs.py", "/.git/config", "/Dockerfile", "/unterschrift.html", "/download.html", "/pruefung.html", "/App.js",
                     "/app.JS", "/%61pp.js%00", "/app.js%00.png", "/..", "/./app.js/x", "/__pycache__", "/a.js", "/signnext.db", "/jobs.lock"):
            r = self.c.get(pfad)
            self.assertIn(r.status_code, (404, 308, 301), pfad)
            if r.status_code in (301, 308):
                self.assertNotIn(b"def ", r.get_data())
            self.assertNotIn(b"import ", r.get_data(), pfad)
        for datei in wurzel.iterdir():
            if datei.is_file() and not ui_ausliefern.ASSET.fullmatch(datei.name):
                self.assertEqual(self.c.get("/" + datei.name).status_code, 404, datei.name)

    def test_api_und_health_bleiben_erreichbar(self):
        self.assertEqual(self.c.get("/health").status_code in (200, 503), True)
        self.assertEqual(self.c.get("/api/version").status_code, 200)
        self.assertEqual(self.c.get("/api/v1/me").status_code, 401)

    def test_asset_liste_kommt_aus_dem_ordner(self):
        for name in os.listdir(UI):
            erlaubt = bool(ui_ausliefern.ASSET.fullmatch(name))
            self.assertEqual(self.c.get("/" + name).status_code, 200 if erlaubt else 404, name)


class TestSchutzwaechterUI(unittest.TestCase):
    """Wächter über die Dateien in ui/: Wer später etwas ändert, bekommt hier Bescheid, bevor die Seite mit der strengen Richtlinie bricht oder unsicher wird."""

    def seiten(self):
        return sorted(UI.glob("*.html"))

    def test_kein_inline_skript_kein_handler_keine_fremde_quelle(self):
        self.assertTrue(self.seiten())
        for datei in self.seiten():
            text = datei.read_text(encoding="utf-8")
            p = Sammler()
            p.feed(text)
            self.assertEqual(p.inline_skripte, 0, datei.name)
            self.assertEqual(p.handler, [], datei.name)
            self.assertEqual(p.doppelt, [], "doppeltes Attribut in %s (das zweite wird vom Browser ignoriert)" % datei.name)
            for ref in p.skripte + p.links:
                self.assertFalse(re.match(r"^(https?:)?//", ref), (datei.name, ref))
            self.assertNotRegex(text, r"(?i)(src|href|action)\s*=\s*[\"']\s*(https?:)?//", datei.name)
            self.assertNotRegex(text, r"(?i)javascript:", datei.name)

    def test_js_ohne_gefaehrliche_bausteine(self):
        for datei in sorted(UI.glob("*.js")):
            text = datei.read_text(encoding="utf-8")
            for muster in (r"\beval\s*\(", r"new Function\s*\(", r"document\.write\s*\(", r"setTimeout\s*\(\s*[\"']", r"setInterval\s*\(\s*[\"']", r"\bimport\s*\(", r"https?://(?!\S*example)"):
                for m in re.finditer(muster, text):
                    zeile = text[:m.start()].count("\n") + 1
                    ausnahme = "startsWith('https://')" in text.splitlines()[zeile - 1] or "'Muss mit https://" in text.splitlines()[zeile - 1]
                    self.assertTrue(ausnahme, "%s:%d %s" % (datei.name, zeile, muster))

    def test_kein_token_in_speicher_oder_adresse(self):
        for name in ("app.js", "download.js"):
            text = (UI / name).read_text(encoding="utf-8")
            self.assertIn("history.replaceState", text, name)
            self.assertNotRegex(text, r"(?i)(localStorage|sessionStorage|document\.cookie)", name)

    def test_nie_rechtssicher(self):
        for datei in list(UI.glob("*")):
            self.assertNotIn("rechtssicher", datei.read_text(encoding="utf-8").lower(), datei.name)

    def test_eigene_innerhtml_nur_mit_festen_texten(self):
        """innerHTML nur dort, wo ausschließlich feste Texte oder Zahlen stehen (kein Wert aus Mitglieds- oder BSt-Eingaben)."""
        for datei in sorted(UI.glob("*.js")):
            for nr, zeile in enumerate(datei.read_text(encoding="utf-8").splitlines(), 1):
                if "innerHTML" in zeile and "=" in zeile:
                    self.assertNotRegex(zeile, r"\$\{[^}]*(verantwortlicher|name|mail|email|text|meldung|err\.|message)", "%s:%d" % (datei.name, nr))


class TestHandyHinweis(Basis):
    """Hinweis auf der Unterschriftsseite (catrin #19322): am Computer mit der Maus, am Smartphone oder Tablet mit dem Finger. Eigenes Element in der Seite, NICHT in texte/
    (dort hängen Kennung und Hash der Irony-Texte dran)."""
    SATZ = "Sie können am Computer mit der Maus oder am Smartphone oder Tablet mit dem Finger unterschreiben."

    def test_satz_steht_als_schlichter_absatz_vor_dem_zeichenfeld(self):
        html = self.c.get("/u").get_data(as_text=True)
        self.assertEqual(html.count(self.SATZ), 1)
        m = re.search(r'<p id="unterschrift-hinweis">([^<]*)</p>', html)
        self.assertIsNotNone(m, "schlichter Absatz ohne class, style oder hidden")
        self.assertEqual(m.group(1), self.SATZ)
        self.assertLess(html.index('<label for="unterschrift-canvas">'), m.start())
        self.assertLess(m.start(), html.index('<canvas id="unterschrift-canvas"'))               # vor dem Zeichenfeld
        self.assertLess(html.index('id="step-unterschrift"'), m.start())                         # im Schritt der Unterschrift, nicht davor
        self.assertEqual(html, (UI / "unterschrift.html").read_text(encoding="utf-8"))

    def test_satz_liegt_nicht_in_den_irony_texten_und_wird_nicht_per_skript_gesetzt(self):
        for datei in (Path(__file__).resolve().parent.parent / "texte").iterdir():
            inhalt = datei.read_text(encoding="utf-8")
            self.assertNotIn("Smartphone", inhalt, datei.name)                                   # texte/ bleibt unberührt (Kennung und Hash)
            self.assertNotIn(self.SATZ, inhalt, datei.name)
        self.assertNotIn("unterschrift-hinweis", (UI / "app.js").read_text(encoding="utf-8"))
        self.assertNotIn("unterschrift-hinweis", (UI / "style.css").read_text(encoding="utf-8").replace("#unterschrift-hinweis {\n    margin-bottom: 0.5rem;\n}", ""))


if __name__ == "__main__":
    unittest.main()
