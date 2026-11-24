"""Lehrplan-Kanon aus den „Vital Articles“ der englischen Wikipedia (PLAN.md 18.1).

Liest die Liste „Wikipedia:Vital articles/Level/3“ (rund 1.000 Kernthemen aus allen Wissensgebieten),
sucht zu jedem Thema den deutschen Artikel und schreibt config/lehrplan_kanon.json:
je Hauptgebiet ein Modul, alle Themen als Basis (Stufe 1). Nur Titel und Verweise, keine Inhalte.

Aufruf (selten, z. B. wenn die Liste erweitert werden soll):
  python tools/kanon.py            Level 3  -> config/lehrplan_kanon.json
  python tools/kanon.py --level4   Level 4  -> config/lehrplan_kanon2.json (Erweiterung 23.11.2026, Stufe 2,
                                   ohne Themen, die schon in Level 3 stehen)
"""

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.daten import _json as _roh_json  # noqa: E402

EN = "https://en.wikipedia.org/w/api.php?"
SEITE = "Wikipedia:Vital articles/Level 3"

# Hauptgebiet der Liste → Bereich der App; Untergebiete mit eigenem Bereich
BEREICH = {"People": "geschichte", "History": "geschichte", "Geography": "geografie", "Arts": "kultur",
           "Philosophy and religion": "religion", "Everyday life": "sonstiges",
           "Society and social sciences": "politik", "Biological and health sciences": "gesundheit",
           "Physical sciences": "wissen", "Technology": "tech", "Mathematics": "wissen"}
UNTER = [(r"writer|poet|author", "kultur"), (r"artist|architect|musician|composer|film|music|literature|visual arts|performing|actor|actress|entertain|director|dancer", "kultur"),
         (r"scientist|inventor|mathematician|physic|chemi|astronom|earth science", "wissen"),
         (r"philosoph|religio|theolog", "religion"), (r"econom|business|finance", "geld"), (r"law", "recht"),
         (r"language|linguist", "sprache"), (r"sport|recreation|game", "sport"), (r"food|drink|cooking", "gesundheit"),
         (r"health|medicine|disease|anatomy", "gesundheit"), (r"biology|organism|plant|animal", "wissen"),
         (r"country|countries|cities|city|continent|ocean|river|mountain|region", "geografie"),
         (r"politic|government|military|war", "politik")]
TITEL_DE = {"geschichte": "Geschichte", "geografie": "Geografie", "kultur": "Kunst, Literatur und Musik", "religion": "Religion und Philosophie",
            "sonstiges": "Alltag", "politik": "Gesellschaft und Politik", "gesundheit": "Mensch und Gesundheit", "wissen": "Naturwissenschaften",
            "tech": "Technik", "geld": "Wirtschaft", "recht": "Recht", "sprache": "Sprache", "sport": "Sport und Freizeit"}


def bereich(haupt: str, unter: str) -> str:
    for muster, b in UNTER:
        if haupt == "People" and b == "geografie":
            continue  # Personen nach Region sortiert: nicht als Geografie einordnen
        if re.search(muster, unter, re.I):
            return b
    return BEREICH.get(haupt, "sonstiges")


LEVEL4 = ["People", "History", "Geography", "Arts", "Philosophy and religion", "Everyday life", "Society and social sciences",
          "Biological and health sciences", "Physical sciences", "Technology", "Mathematics"]


def _text(seite: str) -> str:
    d = _json(EN + urllib.parse.urlencode({"action": "query", "format": "json", "titles": seite, "redirects": 1, "prop": "revisions",
                                            "rvprop": "content", "rvslots": "main", "formatversion": 2}))
    s = d["query"]["pages"][0]
    return s["revisions"][0]["slots"]["main"]["content"] if "revisions" in s else ""


def lade_liste(seite: str = SEITE, haupt_fest: str = "") -> list[tuple[str, str]]:
    text = _text(seite)
    haupt, unter, eintraege, stapel = haupt_fest, "", [], []
    for zeile in text.splitlines():
        if haupt_fest and (m := re.match(r"^(={2,})\s*(.*?)\s*={2,}\s*$", zeile)):
            ebene = len(m.group(1)) - 2  # alle Überschriften-Ebenen merken, damit „Writers“ auch unter „Nordic“ greift
            stapel[ebene:] = [re.sub(r"\s*\(\d+.*$", "", m.group(2)).strip()]
            unter = " / ".join(stapel)
        elif m := re.match(r"^==\s*([^=].*?)\s*\(\d+.*?==\s*$|^==\s*([^=].*?)\s*==\s*$", zeile):
            haupt, unter = (m.group(1) or m.group(2)).strip(), ""
        elif m := re.match(r"^={3,}\s*(.*?)\s*={3,}\s*$", zeile):
            unter = re.sub(r"\s*\(\d+.*$", "", m.group(1)).strip()
        elif m := re.match(r"^[#*]+\s*(?:\{\{[^}]*\}\}\s*)*\[\[([^\]|#]+)", zeile):
            if re.search(r"compan", unter, re.I):
                continue  # keine Firmenkarten (VW, Apple, Nestlé …), Entscheidung des Nutzers 24.11.2026
            eintraege.append((m.group(1).strip(), bereich(haupt, unter)))
    return eintraege


def deutsch(titel: list[str]) -> dict[str, str]:
    ergebnis = {}
    for i in range(0, len(titel), 50):
        d = _json(EN + urllib.parse.urlencode({"action": "query", "format": "json", "titles": "|".join(titel[i:i + 50]),
                                                "prop": "langlinks", "lllang": "de", "lllimit": 500, "redirects": 1}))
        umleitung = {r["to"]: r["from"] for r in d["query"].get("redirects", [])}
        normal = {n["to"]: n["from"] for n in d["query"].get("normalized", [])}
        for s in d["query"]["pages"].values():
            de = (s.get("langlinks") or [{}])[0].get("*")
            if de:
                quelle = umleitung.get(s["title"], s["title"])
                ergebnis[normal.get(quelle, quelle)] = de
    return ergebnis


def lade_level4() -> list[tuple[str, str]]:
    eintraege = []
    for gebiet in LEVEL4:
        seite = f"Wikipedia:Vital articles/Level/4/{gebiet}"
        teil = lade_liste(seite, gebiet)
        if len(teil) < 50:  # Gebiet ist auf Unterseiten verteilt
            for unterseite in sorted(set(re.findall(r"\[\[(" + re.escape(seite) + r"/[^\]|#]+)", _text(seite)))):
                teil += lade_liste(unterseite, gebiet)
        print(gebiet, len(teil))
        eintraege += teil
    return eintraege


def _json(url: str) -> dict:
    """Abfrage mit Pause und Wiederholung; Wikipedia und Wikidata drosseln schnelle Abfragen (HTTP 429)."""
    for versuch in range(8):
        try:
            d = _roh_json(url)
            time.sleep(0.3)
            return d
        except Exception:
            if versuch == 7:
                raise
            time.sleep(15 * (versuch + 1))
    return {}


_geduldig = _json


def bekanntheit(titel: list[str]) -> dict[str, int]:
    """Zahl der Wikipedia-Sprachversionen je englischem Titel (Wikidata-Sitelinks) als Maß, wie bekannt ein Thema ist."""
    qid = {}
    for i in range(0, len(titel), 50):
        d = _geduldig(EN + urllib.parse.urlencode({"action": "query", "format": "json", "titles": "|".join(titel[i:i + 50]),
                                                    "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1}))
        if not d:
            continue
        umleitung = {r["to"]: r["from"] for r in d["query"].get("redirects", [])}
        normal = {n["to"]: n["from"] for n in d["query"].get("normalized", [])}
        for s in d["query"]["pages"].values():
            if q := s.get("pageprops", {}).get("wikibase_item"):
                quelle = umleitung.get(s["title"], s["title"])
                qid[normal.get(quelle, quelle)] = q
    zahl, ids = {}, sorted(set(qid.values()))
    for i in range(0, len(ids), 50):
        url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "wbgetentities", "format": "json", "ids": "|".join(ids[i:i + 50]), "props": "sitelinks"})
        d = _geduldig(url)
        for q, e in d.get("entities", {}).items():
            zahl[q] = sum(1 for k in e.get("sitelinks", {}) if k.endswith("wiki") and k != "commonswiki")
    return {t: zahl.get(q, 0) for t, q in qid.items()}


def main() -> int:
    level4 = "--level4" in sys.argv
    eintraege = lade_level4() if level4 else lade_liste()
    schon = set()
    if level4:
        alt = json.loads((Path(__file__).resolve().parents[1] / "config" / "lehrplan_kanon.json").read_text(encoding="utf-8"))
        schon = {t["wiki"] for m in alt["module"] for t in m["themen"]}
    de = deutsch(sorted({t for t, _ in eintraege}))
    if level4:  # bekannteste Themen zuerst (Zahl der Sprachversionen), damit es um das geht, worüber man redet
        zahl = bekanntheit(sorted({t for t, _ in eintraege if t in de}))
        eintraege.sort(key=lambda e: -zahl.get(e[0], 0))
    module: dict[str, list] = {}
    gesehen = set()
    for en, b in eintraege:
        if en not in de or de[en] in gesehen or de[en] in schon:
            continue
        gesehen.add(de[en])
        tid = re.sub(r"[^a-z0-9]+", "-", de[en].lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")).strip("-")[:50]
        module.setdefault(b, []).append({"id": tid, "titel": re.sub(r"\s*\(.*\)$", "", de[en]), "stufe": 2 if level4 else 1, "braucht": [],
                                         "auch": [], "wiki": de[en], "grafik": None})
    quelle = "Wikipedia:Vital articles/Level/4" if level4 else SEITE
    praefix = "kanon4" if level4 else "kanon"
    ausgabe = {"_hinweis": f"Automatisch aus „{quelle}“ (en.wikipedia) erzeugt von tools/kanon.py. Nur Titel, keine Inhalte.",
               "module": [{"id": f"{praefix}-{b}", "titel": f"Weltwissen: {TITEL_DE.get(b, b)}", "bereich": b, "themen": t}
                          for b, t in sorted(module.items())]}
    ziel = Path(__file__).resolve().parents[1] / "config" / ("lehrplan_kanon2.json" if level4 else "lehrplan_kanon.json")
    if level4:  # kompakt (nur Wiki-Titel je Modul), sonst zu groß fürs Repo; pipeline/wissen.py ergänzt den Rest
        for m in ausgabe["module"]:
            m["wiki"] = [t["wiki"] for t in m.pop("themen")]
        ziel.write_text(json.dumps(ausgabe, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    else:
        ziel.write_text(json.dumps(ausgabe, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(eintraege)} Einträge, {sum(len(t) for t in module.values())} mit deutschem Artikel:",
          {b: len(t) for b, t in sorted(module.items())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
