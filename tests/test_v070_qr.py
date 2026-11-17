"""v0.7.0 QR-Code als SVG: Namensraum (sonst zeichnet der Browser das SVG als <img> nicht), gültiges XML, nichts Aktives. Nur erfundene Daten."""
import unittest
import xml.etree.ElementTree as ET

import qr

SVG_NS = "http://www.w3.org/2000/svg"


class TestQr(unittest.TestCase):
    def test_svg_hat_den_namensraum_und_ist_gueltiges_xml(self):
        s = qr.svg("https://signnext.example/h#abc123")
        self.assertIn('xmlns="%s"' % SVG_NS, s.split(">", 1)[0])                                           # im Wurzelelement
        wurzel = ET.fromstring(s)
        self.assertEqual(wurzel.tag, "{%s}svg" % SVG_NS)
        self.assertTrue(int(wurzel.get("width")) > 0 and int(wurzel.get("height")) > 0)

    def test_nichts_aktives_und_keine_fremden_verweise(self):
        s = qr.svg("https://signnext.example/u#xyz")
        for verboten in ("<script", "onload", "javascript:", "<foreignObject", "href=", "<?xml"):
            self.assertNotIn(verboten, s)
        self.assertNotIn("signnext.example", s)                                                          # die Adresse steckt nur als Muster im Bild, nie als Text

    def test_ungueltige_adressen(self):
        for schlecht in ("", None, 5, "x" * 501):
            with self.assertRaises(ValueError):
                qr.svg(schlecht)


if __name__ == "__main__":
    unittest.main()
