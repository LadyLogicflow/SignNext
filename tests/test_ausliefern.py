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

    def test_mitgelieferte_schriften_werden_ausgeliefert_und_haben_eine_lizenz(self):
        for name in ("Inter", "ArchivoBlack", "JetBrainsMono"):
            r = self.c.get("/fonts/%s.woff2" % name)
            self.assertEqual((r.status_code, r.headers["Content-Type"]), (200, "font/woff2"), name)
            self.assertEqual(r.get_data(), (UI / "fonts" / ("%s.woff2" % name)).read_bytes())
            text = (UI / "fonts" / ("LIZENZ-%s-OFL.txt" % name)).read_text(encoding="utf-8")
            self.assertIn("SIL OPEN FONT LICENSE Version 1.1", text, name)
        for name in ("LIES-MICH.txt", "LIZENZ-Inter-OFL.txt"):
            self.assertEqual(self.c.get("/fonts/" + name).status_code, 404, name)                   # Lizenztexte liegen bei den Dateien, werden aber nicht ausgeliefert

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
                    # SVG-Namensraum (Kennung, kein Abruf): nur in diesen beiden Dateien und nur, wenn auf der Zeile keine andere Adresse steht
                    adressen = re.findall(r"https?://[^\s'\"\\]+", text.splitlines()[zeile - 1])
                    ausnahme = ausnahme or (datei.name in ("qrbild.js", "wortmarke.js") and bool(adressen) and all(a == "http://www.w3.org/2000/svg" for a in adressen))
                    self.assertTrue(ausnahme, "%s:%d %s" % (datei.name, zeile, muster))

    def test_kein_token_in_speicher_oder_adresse(self):
        for name in ("app.js", "download.js", "handy.js"):
            text = (UI / name).read_text(encoding="utf-8")
            self.assertIn("history.replaceState", text, name)
            self.assertNotRegex(text, r"(?i)(localStorage|sessionStorage|document\.cookie)", name)

    def test_nie_rechtssicher(self):
        for datei in [d for d in UI.glob("*") if d.is_file()]:                                      # Schriften liegen im Unterordner fonts/
            self.assertNotIn("rechtssicher", datei.read_text(encoding="utf-8").lower(), datei.name)

    def test_eigene_innerhtml_nur_mit_festen_texten(self):
        """innerHTML nur dort, wo ausschließlich feste Texte oder Zahlen stehen (kein Wert aus Mitglieds- oder BSt-Eingaben)."""
        for datei in sorted(UI.glob("*.js")):
            for nr, zeile in enumerate(datei.read_text(encoding="utf-8").splitlines(), 1):
                if "innerHTML" in zeile and "=" in zeile:
                    self.assertNotRegex(zeile, r"\$\{[^}]*(verantwortlicher|name|mail|email|text|meldung|err\.|message)", "%s:%d" % (datei.name, nr))


class TestTippflaechen(unittest.TestCase):
    """Auf dem Handy müssen Knöpfe und Textknöpfe mindestens 44 px hoch sein (Touch-Probe 17.11.2026)."""

    def test_mindestgroesse_44px(self):
        import re
        css = (Path(__file__).resolve().parent.parent / "ui" / "style.css").read_text(encoding="utf-8")

        def regel(selektor):
            m = re.search(re.escape(selektor) + r"\s*\{([^}]*)\}", css)
            self.assertIsNotNone(m, selektor)
            return m.group(1)
        for sel in (".zeichenfeld-leeren, .link-knopf", ".btn"):
            self.assertRegex(regel(sel), r"min-height:\s*44px", sel)
        self.assertRegex(regel(".zeichenfeld-leeren, .link-knopf"), r"min-width:\s*44px")

    def test_hinweis_unter_dem_zeichenfeld_behaelt_seinen_platz(self):
        css = (Path(__file__).resolve().parent.parent / "ui" / "style.css").read_text(encoding="utf-8")
        js = (Path(__file__).resolve().parent.parent / "ui" / "zeichenfeld.js").read_text(encoding="utf-8")
        self.assertIn("visibility: hidden", css.split(".zeichenfeld-fehlt.unsichtbar", 1)[1].split("}", 1)[0])
        self.assertNotIn("fehlt.classList.toggle('hidden'", js)                                   # display:none würde das Feld unter dem Finger springen lassen


class TestBeraterRahmenStatik(unittest.TestCase):
    BERATER = ("vorgaenge", "pruefung", "einstellungen", "mail-zugang")

    def test_jede_berater_seite_hat_den_gemeinsamen_rahmen(self):
        for name in self.BERATER:
            text = (UI / (name + ".html")).read_text(encoding="utf-8")
            self.assertIn('<body class="berater">', text, name)
            for teil in ('href="berater.css"', 'src="wortmarke.js"', 'src="qrbild.js"', 'src="berater.js"', 'id="berater-nav"', 'id="hinweisleiste"', 'id="wortmarke"'):
                self.assertIn(teil, text, (name, teil))
            self.assertRegex(text, r"<h1[ >]", name)
            self.assertEqual(text.count('aria-current="page"'), 1, name)
            self.assertLess(text.index('src="berater.js"'), text.index('src="%s.js"' % name), name)      # Rahmen vor dem Seitenskript

    def test_knopfreihen_brechen_um_statt_ueberzulaufen(self):
        css = (UI / "berater.css").read_text(encoding="utf-8")
        regel = re.search(r"body\.berater \.knopf-zeile\s*\{([^}]*)\}", css)
        self.assertIsNotNone(regel)
        self.assertRegex(regel.group(1), r"flex-wrap:\s*wrap")
        for name in self.BERATER:
            self.assertNotIn('style="display: flex', (UI / (name + ".html")).read_text(encoding="utf-8"), name)    # Knopfreihen über .knopf-zeile (umbrechend)

    def test_lange_namen_verbreitern_die_seite_nicht(self):
        css = (UI / "berater.css").read_text(encoding="utf-8")
        regel = re.search(r"body\.berater h1\s*\{([^}]*)\}", css)
        self.assertIsNotNone(regel)
        self.assertRegex(regel.group(1), r"overflow-wrap:\s*anywhere")                                  # Vorgangsname ohne Leerzeichen in der Überschrift (Chromium-Probe, Handy)

    def test_karten_bleiben_am_handy_nebeneinander(self):
        css = (UI / "berater.css").read_text(encoding="utf-8")
        schmal = css.split("@media (max-width: 700px)", 1)[1]
        self.assertRegex(schmal, r"\.karten-raster\s*\{[^}]*repeat\(3, 1fr\)")                       # drei Karten in einer Zeile, nicht untereinander
        self.assertNotRegex(schmal, r"\.karten-raster\s*,")

    def test_mitglieder_seiten_bleiben_ohne_berater_stil(self):
        for name in ("unterschrift", "download", "handy"):
            text = (UI / (name + ".html")).read_text(encoding="utf-8")
            self.assertNotIn("berater", text, name)

    def test_jede_schrift_im_stylesheet_gibt_es_als_datei_und_nur_von_hier(self):
        css = (UI / "berater.css").read_text(encoding="utf-8")
        urls = re.findall(r"url\(['\"]?([^'\")]+)", css)
        self.assertEqual(len(urls), 3)
        for u in urls:
            self.assertTrue(re.fullmatch(r"/fonts/[A-Za-z0-9]+\.woff2", u), u)                        # eigene Adresse, kein CDN
            self.assertTrue((UI / u.lstrip("/")).is_file(), u)
        self.assertNotRegex(css, r"(?i)https?://|@import")


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
        self.assertLess(html.index('id="zeichenfeld-label"'), m.start())
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
