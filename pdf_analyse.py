"""Auswertung der beiden Dokumenttypen (läuft im Kindprozess, siehe pdf_worker.py).

B = Anschreiben/Vollständigkeitserklärung: weiße Zeile ``Mitgliedsnummer; Vorname; Name; E-Mail`` auf Seite 1, Unterschriftsblöcke
   (Zeile ``Ort, TT.MM.JJJJ``, darunter eine Zeile aus Unterstrichen, darunter die Namen).
A = Elster-/Steuersoft-Protokoll: eine Unterschriftsstelle (Unterstrichzeile, darunter ``Datum, Unterschrift, …``), Mitgliedsnummer nach der Zeile
   einzelne 7-stellige Zeile direkt vor ``Die Steuererklärung wurde unter Mitwirkung`` im Block ``Bei der Anfertigung dieser Erklärung hat mitgewirkt:``.
Erkannt wird am Aufbau (Textanker), nie am Dateinamen. Alles Mehrdeutige oder Fehlende bleibt leer (kein Raten).
Koordinaten: PDF-Punkte, Ursprung unten links, je Stelle ``seite`` (ab 1), ``x0, y0, x1, y1``, ``person`` (1 oder 2)."""
import io
import re

import pdfplumber

from pdf_pruefung import Abgelehnt, pruefe

MITWIRKUNG = "bei der anfertigung dieser erklärung hat mitgewirkt"
SIG_DATUM = re.compile(r"^.{1,60},\s*\d{2}\.\d{2}\.\d{4}\s*$")
MAIL = re.compile(r"^[^@\s;,<>]{1,64}@[^@\s;,<>]{1,190}\.[^@\s;,<>]{2,24}$")
STEUERNUMMER = re.compile(r"\d{2,3}/\d{3,4}/\d{4,5}")
ID11 = re.compile(r"(?<!\d)\d{11}(?!\d)")
HOEHE_STELLE = 40.0
MAX_BLOECKE_B = 4


def ist_weiss(farbe) -> bool:
    """Grau, RGB oder CMYK 'weiß' (mit kleiner Toleranz). Muster-/Namensfarben zählen nicht."""
    if farbe is None:
        return False
    if isinstance(farbe, (int, float)):
        farbe = (farbe,)
    try:
        w = [float(x) for x in farbe]
    except (TypeError, ValueError):
        return False
    if len(w) in (1, 3):
        return all(x >= 0.98 for x in w)
    if len(w) == 4:
        return all(x <= 0.02 for x in w)
    return False


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _zeilen(seite) -> list:
    return [z for z in seite.extract_text_lines(return_chars=True, strip=True) if z["text"].strip()]


def _ziffern(t: str) -> str:
    return re.sub(r"\D", "", t)


def _weisse_zeilen(seite) -> list:
    """Texte aller weißen Zeilen der Seite. Leerzeichen innerhalb der Zeile bleiben erhalten (Namen mit Leerzeichen), auch wenn sie selbst keine Farbe tragen."""
    weiss = [c for c in seite.chars if ist_weiss(c.get("non_stroking_color")) and c.get("text", "").strip() != ""]
    zeilen: dict[int, list] = {}
    for c in weiss:
        zeilen.setdefault(round(c["top"] / 3), []).append(c)
    ergebnis = []
    for _, z in sorted(zeilen.items()):
        oben = min(c["top"] for c in z)
        von, bis = min(c["x0"] for c in z), max(c["x1"] for c in z)
        teile = [c for c in seite.chars if abs(c["top"] - oben) < 3 and von - 0.5 <= c["x0"] <= bis + 0.5
                 and (c["text"].isspace() or ist_weiss(c.get("non_stroking_color")))]
        ergebnis.append("".join(c["text"] for c in sorted(teile, key=lambda c: c["x0"])))
    return ergebnis


def _weisse_zeile_lesen(zeilen: list) -> dict:
    """Aus den weißen Zeilen von Seite 1: genau eine Zeile mit 4 Feldern, sonst nichts übernehmen."""
    kandidaten = [z for z in zeilen if z.count(";") == 3]
    if len(kandidaten) != 1 or len(zeilen) != 1:
        return {}
    teile = [t.strip() for t in kandidaten[0].split(";")]
    daten = {}
    if re.fullmatch(r"\d{7}", teile[0]):
        daten["mitgliedsnr"] = teile[0]
    if teile[1]:
        daten["vorname"] = teile[1]
    if teile[2]:
        daten["name"] = teile[2]
    if MAIL.match(teile[3]) and len(teile[3]) <= 254:
        daten["mail"] = teile[3].lower()
    return daten


def _gruppen(zeile: dict) -> list:
    """Unterstrichgruppen einer Zeile: [(x0, x1)], getrennt durch Lücken."""
    us = sorted((c for c in zeile["chars"] if c["text"] == "_"), key=lambda c: c["x0"])
    gruppen = []
    for c in us:
        if gruppen and c["x0"] - gruppen[-1][1] <= 2.0:
            gruppen[-1][1] = max(gruppen[-1][1], c["x1"])
        else:
            gruppen.append([c["x0"], c["x1"]])
    return [(a, b) for a, b in gruppen if b - a >= 20]


def _ist_unterstrichzeile(t: str) -> bool:
    return bool(re.fullmatch(r"[_\s]+", t)) and "________" in t


def _bloecke(seiten: list) -> list:
    """Alle Unterschriftsblöcke: art 'B' (Zeile 'Ort, Datum' davor) oder 'A' (Zeile 'Datum, Unterschrift' danach)."""
    ergebnis = []
    for nr, (seite, zeilen) in enumerate(seiten, 1):
        for i, z in enumerate(zeilen):
            if not _ist_unterstrichzeile(z["text"]):
                continue
            gruppen = _gruppen(z)
            if not gruppen:
                continue
            davor = zeilen[i - 1] if i > 0 else None
            danach = zeilen[i + 1] if i + 1 < len(zeilen) else None
            art = None
            if danach is not None and _norm(danach["text"]).lower().startswith("datum, unterschrift"):
                art = "A"
            elif davor is not None and SIG_DATUM.match(_norm(davor["text"])):
                art = "B"
            if art is None:
                continue
            oben = max(z["bottom"] - 1 - HOEHE_STELLE, (davor["bottom"] + 2) if davor is not None else 0.0)
            ergebnis.append({"art": art, "seite": nr, "hoehe": float(seite.height), "gruppen": gruppen, "unten": z["bottom"] - 1, "oben": oben})
    return ergebnis


def _stellen(bloecke: list) -> list:
    stellen = []
    for b in bloecke:
        for person, (x0, x1) in enumerate(sorted(b["gruppen"]), 1):
            stellen.append({"seite": b["seite"], "person": person, "x0": round(x0, 1), "x1": round(x1, 1),
                            "y0": round(b["hoehe"] - b["unten"], 1), "y1": round(b["hoehe"] - b["oben"], 1)})
    return stellen


def _eindeutig(treffer: list):
    """Genau ein verschiedener Wert, sonst None."""
    w = set(treffer)
    return w.pop() if len(w) == 1 else None


def _werte_b(zeilen1: list, weiss: dict, hinweise: list) -> dict:
    werte = dict(weiss)
    text_mnr = []
    stnr = []
    ids = []
    for z in zeilen1:
        t = _norm(z["text"])
        m = re.match(r"^Mitglieds-?Nr\.?:?\s*(\d{7})(?!\d)", t, re.I)
        if m:
            text_mnr.append(m.group(1))
        if re.match(r"^Steuernummer\b", t, re.I):
            stnr.extend(_ziffern(x) for x in STEUERNUMMER.findall(t))
        if re.match(r"^Steuer-?\s?Id", t, re.I):
            ids.extend(ID11.findall(t))
    mnr = _eindeutig(text_mnr) if text_mnr else werte.get("mitgliedsnr")
    if text_mnr and "mitgliedsnr" in weiss and weiss["mitgliedsnr"] != mnr:
        mnr = None
        hinweise.append("mitgliedsnr_abweichend_in_b")
    werte["mitgliedsnr"] = mnr
    werte["steuernummern"] = sorted(set(stnr))
    werte["ids"] = sorted(set(ids))
    return {k: v for k, v in werte.items() if v not in (None, [], "")}


def _a_zeilenwert(zeilen1: list, nr: int, label: str, muster: str):
    treffer = []
    for z in zeilen1:
        m = re.match(r"^%d\s+%s\s+(.+)$" % (nr, label), _norm(z["text"]))
        if m and re.fullmatch(muster, m.group(1).strip()):
            treffer.append(m.group(1).strip())
    return _eindeutig(treffer) if treffer else None


def _werte_a(seiten: list, hinweise: list) -> dict:
    zeilen1 = seiten[0][1]
    werte = {}
    # Steuernummer: Kopfzeile des Hauptvordrucks (Seite 1)
    kopf = {_ziffern(m) for z in zeilen1 if z["top"] < 80 and re.search(r"Steuernummer", z["text"]) for m in STEUERNUMMER.findall(z["text"])[:1]}
    if len(kopf) == 1:
        werte["steuernummern"] = sorted(kopf)
    ids = [x for x in (_a_zeilenwert(zeilen1, 8, "Identifikationsnummer", r"\d{11}"), _a_zeilenwert(zeilen1, 20, "Identifikationsnummer", r"\d{11}")) if x]
    if ids:
        werte["ids"] = sorted(set(ids))
    dob = _a_zeilenwert(zeilen1, 8, "Geburtsdatum", r"\d{2}\.\d{2}\.\d{4}")
    if dob:
        werte["geburtsdatum"] = dob
    name = _a_zeilenwert(zeilen1, 9, "Name", r".{1,80}")
    vorname = _a_zeilenwert(zeilen1, 10, "Vorname", r".{1,80}")
    if name:
        werte["name"] = name
    if vorname:
        werte["vorname"] = vorname
    # Mitgliedsnummer nur über die Lage: Im Block "Bei der Anfertigung dieser Erklärung hat mitgewirkt:" steht genau EINE Zeile aus 7 Ziffern, und sie steht
    # direkt vor dem Satz "Die Steuererklärung wurde unter Mitwirkung ...". Die Zeile davor (Ehepaar-Beispiel: "15720 FV NRW", Einzelperson-Beispiel: zwei Zahlen)
    # ist nicht maßgeblich. Mehrere 7-stellige Zeilen im Block, eine andere Zeile dazwischen oder keine solche Zeile: leer (nichts raten).
    kandidaten = []
    for _, zeilen in seiten:
        for i, z in enumerate(zeilen):
            if MITWIRKUNG not in _norm(z["text"]).lower():
                continue
            ende = next((k for k in range(i + 1, min(i + 20, len(zeilen))) if _norm(zeilen[k]["text"]).startswith("Die Steuererklärung wurde unter Mitwirkung")), None)
            if ende is None or ende - i < 2:
                kandidaten.append(None)
                continue
            sieben = [k for k in range(i + 1, ende) if re.fullmatch(r"\d{7}", _norm(zeilen[k]["text"]))]
            kandidaten.append(_norm(zeilen[sieben[0]]["text"]) if len(sieben) == 1 and sieben[0] == ende - 1 else None)
    if len(kandidaten) == 1 and kandidaten[0]:
        werte["mitgliedsnr"] = kandidaten[0]
    elif len(kandidaten) > 1:
        hinweise.append("mitgliedsnr_mehrdeutig_in_a")
    return werte


def analysiere(daten: bytes) -> dict:
    """Wirft Abgelehnt(code). Ergebnis: {typ, seiten, stellen, personen, werte, hinweise}. ``werte`` enthält Personendaten und wird nie gespeichert."""
    pruefe(daten)
    hinweise: list = []
    try:
        with pdfplumber.open(io.BytesIO(daten)) as pdf:
            n = len(pdf.pages)
            seiten = []
            zeichen = 0
            weisse = []
            for nr, s in enumerate(pdf.pages):
                zeichen += len(s.chars)
                if nr == 0:
                    weisse = _weisse_zeilen(s)
                seiten.append((s, _zeilen(s)))
            if zeichen < max(100, 30 * n):
                raise Abgelehnt("keine_textebene")
            anker_a = any(MITWIRKUNG in _norm(z["text"]).lower() for _, zl in seiten for z in zl)
            bloecke = _bloecke(seiten)
            b_bloecke = [b for b in bloecke if b["art"] == "B"]
            a_bloecke = [b for b in bloecke if b["art"] == "A"]
            if a_bloecke and not b_bloecke and anker_a:
                typ = "A"
            elif b_bloecke and not a_bloecke:
                typ = "B"
            else:
                raise Abgelehnt("typ_unbekannt")
            if typ == "A":
                if len(a_bloecke) != 1 or len(a_bloecke[0]["gruppen"]) not in (1, 2):
                    raise Abgelehnt("stellen_unklar")
                personen = None
                werte = _werte_a(seiten, hinweise)
                if any(re.match(r"^20\s+Identifikationsnummer\b", _norm(z["text"])) for z in seiten[0][1]):
                    hinweise.append("a_nennt_ehegatten")
                stellen = _stellen(a_bloecke)
            else:
                anzahl = {len(b["gruppen"]) for b in b_bloecke}
                if len(b_bloecke) > MAX_BLOECKE_B or len(anzahl) != 1 or anzahl.copy().pop() not in (1, 2):
                    raise Abgelehnt("stellen_unklar")
                personen = len(b_bloecke[0]["gruppen"])
                werte = _werte_b(seiten[0][1], _weisse_zeile_lesen(weisse), hinweise)
                if "mail" not in werte:
                    hinweise.append("mail_fehlt")
                stellen = _stellen(b_bloecke)
    except Abgelehnt:
        raise
    except Exception:
        raise Abgelehnt("defekt")
    return {"typ": typ, "seiten": n, "stellen": stellen, "personen": personen, "werte": werte, "hinweise": hinweise}
