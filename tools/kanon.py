"""Lehrplan-Kanon aus den „Vital Articles“ der englischen Wikipedia (PLAN.md 18.1).

Liest die Liste „Wikipedia:Vital articles/Level/3“ (rund 1.000 Kernthemen aus allen Wissensgebieten),
sucht zu jedem Thema den deutschen Artikel und schreibt config/lehrplan_kanon.json:
je Hauptgebiet ein Modul, alle Themen als Basis (Stufe 1). Nur Titel und Verweise, keine Inhalte.

Aufruf (selten, z. B. wenn die Liste erweitert werden soll):
  python tools/kanon.py
"""

import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.daten import _json  # noqa: E402

EN = "https://en.wikipedia.org/w/api.php?"
SEITE = "Wikipedia:Vital articles/Level 3"

# Hauptgebiet der Liste → Bereich der App; Untergebiete mit eigenem Bereich
BEREICH = {"People": "geschichte", "History": "geschichte", "Geography": "geografie", "Arts": "kultur",
           "Philosophy and religion": "religion", "Everyday life": "sonstiges",
           "Society and social sciences": "politik", "Biological and health sciences": "gesundheit",
           "Physical sciences": "wissen", "Technology": "tech", "Mathematics": "wissen"}
UNTER = [(r"writer|poet|author", "kultur"), (r"artist|architect|musician|composer|film|music|literature|visual arts|performing", "kultur"),
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
        if re.search(muster, unter, re.I):
            return b
    return BEREICH.get(haupt, "sonstiges")


def lade_liste() -> list[tuple[str, str]]:
    d = _json(EN + urllib.parse.urlencode({"action": "query", "format": "json", "titles": SEITE, "redirects": 1, "prop": "revisions",
                                            "rvprop": "content", "rvslots": "main", "formatversion": 2}))
    text = d["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
    haupt, unter, eintraege = "", "", []
    for zeile in text.splitlines():
        if m := re.match(r"^==\s*([^=].*?)\s*\(\d+.*?==\s*$|^==\s*([^=].*?)\s*==\s*$", zeile):
            haupt, unter = (m.group(1) or m.group(2)).strip(), ""
        elif m := re.match(r"^={3,}\s*(.*?)\s*={3,}\s*$", zeile):
            unter = re.sub(r"\s*\(\d+.*$", "", m.group(1)).strip()
        elif m := re.match(r"^[#*]+\s*(?:\{\{[^}]*\}\}\s*)*\[\[([^\]|#]+)", zeile):
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


def main() -> int:
    eintraege = lade_liste()
    de = deutsch(sorted({t for t, _ in eintraege}))
    module: dict[str, list] = {}
    gesehen = set()
    for en, b in eintraege:
        if en not in de or de[en] in gesehen:
            continue
        gesehen.add(de[en])
        tid = re.sub(r"[^a-z0-9]+", "-", de[en].lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")).strip("-")[:50]
        module.setdefault(b, []).append({"id": tid, "titel": re.sub(r"\s*\(.*\)$", "", de[en]), "stufe": 1, "braucht": [],
                                         "auch": [], "wiki": de[en], "grafik": None})
    ausgabe = {"_hinweis": f"Automatisch aus „{SEITE}“ (en.wikipedia) erzeugt von tools/kanon.py. Nur Titel, keine Inhalte.",
               "module": [{"id": f"kanon-{b}", "titel": f"Weltwissen: {TITEL_DE.get(b, b)}", "bereich": b, "themen": t}
                          for b, t in sorted(module.items())]}
    ziel = Path(__file__).resolve().parents[1] / "config" / "lehrplan_kanon.json"
    ziel.write_text(json.dumps(ausgabe, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(eintraege)} Einträge, {sum(len(t) for t in module.values())} mit deutschem Artikel:",
          {b: len(t) for b, t in sorted(module.items())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
