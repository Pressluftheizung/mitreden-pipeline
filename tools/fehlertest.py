"""Fehlertest für die Prüf-KI (PLAN.md 9.3): Findet sie absichtlich eingebaute Fehler?

Nimmt neu geschriebene Testkarten (mit ihren Quellen) aus dem letzten Bau und baut je Karte Varianten:
  original   unverändert                         → erwartet: kein Punkt „nicht belegt“
  zahl       eine Zahl in einem Punkt verändert  → erwartet: dieser Punkt „nicht belegt“
  erfunden   ein Punkt durch eine erfundene Behauptung ersetzt → erwartet: „nicht belegt“
  name       ein Name in einem Punkt vertauscht  → erwartet: „nicht belegt“
  wertung    ein wertendes Wort eingefügt        → erwartet: in „wertende_woerter“ genannt
Jedes Prüfmodell aus config/ki.config.json (Aufgabe „pruefen“) läuft über alle Varianten.

Schreibt das Ergebnis nach <daten>/metriken/fehlertest.json (privat: enthält Kartentexte).

Aufruf: python tools/fehlertest.py --daten <rohdaten> [--karten 8]
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.ki import KI, KeinModellFrei, Zaehler  # noqa: E402
from pipeline.pruefen import aussagen, pruef_anfrage, pruefe_antwort  # noqa: E402

ERFUNDEN = "Der Bundestag hat dazu bereits eine Sondersitzung für nächsten Dienstag einberufen."
WERTUNG = "endlich"


def varianten(karte: dict, zufall: random.Random) -> list[tuple[str, dict, int | None]]:
    """(Art, Karte, Nummer der veränderten Aussage oder None)."""
    def kopie() -> dict:
        return json.loads(json.dumps(karte))

    nummer = {(a["feld"], a["index"]): a["nr"] for a in aussagen(karte)}
    ergebnis = [("original", kopie(), None)]
    punkte = karte.get("punkte") or []
    mit_zahl = [i for i, p in enumerate(punkte) if re.search(r"\d", p)]
    if mit_zahl:
        k, i = kopie(), mit_zahl[0]
        k["punkte"][i] = re.sub(r"\d+", lambda m: str(int(m.group(0)) * 3 + 7), k["punkte"][i], count=1)
        ergebnis.append(("zahl", k, nummer[("punkte", i)]))
    if punkte:
        k, i = kopie(), len(punkte) - 1
        k["punkte"][i] = ERFUNDEN
        ergebnis.append(("erfunden", k, nummer[("punkte", i)]))
    namen = [(i, m.group(0)) for i, p in enumerate(punkte) for m in re.finditer(r"\b[A-ZÄÖÜ][a-zäöüß]{3,}\b", p) if m.start() > 0]
    if namen:
        k = kopie()
        i, name = zufall.choice(namen)
        k["punkte"][i] = k["punkte"][i].replace(name, "Söder" if name != "Söder" else "Habeck", 1)
        ergebnis.append(("name", k, nummer[("punkte", i)]))
    if karte.get("satz"):
        k = kopie()
        k["satz"] = re.sub(r"^(\S+\s+\S+)", rf"\1 {WERTUNG}", k["satz"], count=1)
        ergebnis.append(("wertung", k, None))
    return ergebnis


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daten", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--karten", type=int, default=8)
    args = parser.parse_args()

    ausgaben = sorted((args.daten / "testkarten").glob("*.json"))
    if not ausgaben:
        print("Keine Testkarten gefunden.")
        return 1
    stories = {s["story_id"]: s for s in json.loads((args.daten / "stories" / "stories.json").read_text(encoding="utf-8"))["stories"]}
    bau = json.loads(ausgaben[-1].read_text(encoding="utf-8"))
    # Quellen der KI stehen nicht in der Ausgabe; die Prüfansicht-Daten liegen im Bau-Protokoll nicht vor.
    # Deshalb: Quellen aus der Karte (Titel) plus Faktenbox als Belegtext.
    kandidaten = [k for k in bau["karten"] if k.get("format") in ("top", "karte") and k.get("faktenbox") and k["story_id"] in stories]
    zufall = random.Random(28)
    auswahl = zufall.sample(kandidaten, min(args.karten, len(kandidaten)))

    ki = KI(args.config / "ki.config.json", Zaehler(args.daten / "metriken" / "ki-verbrauch.json"), args.config.parent)
    modelle = ki.k["aufgaben"]["pruefen"]["kette"]
    ergebnis = {m: {"faelle": 0, "gefunden": 0, "falschalarm": 0, "je_art": {}} for m in modelle}
    for karte in auswahl:
        quellen = [{"id": f"q{n}", "redaktion": q["name"], "lager": q["lager"], "art": q.get("art", "bericht"),
                    "titel": q.get("titel", ""), "text": ""} for n, q in enumerate(karte["quellen"], start=1)]
        # Belegtext: die geprüfte Faktenbox der Originalkarte (sie stammt aus den Quellen und ist bereits geprüft)
        quellen.append({"id": "q0", "redaktion": "Faktenbox", "lager": "mitte", "art": "bericht", "titel": "Belegte Fakten",
                        "text": " ".join(f["fakt"] for f in karte["faktenbox"])})
        for art, variante, nr in varianten(karte, zufall):
            for modell in modelle:
                ki.k["aufgaben"]["pruefen"]["kette"] = [modell]
                try:
                    urteil, _ = ki.frage("pruefen", pruef_anfrage(variante, quellen), pruefe_antwort)
                except KeinModellFrei as e:
                    print(f"{modell}: {e}")
                    continue
                schlecht = {u["nr"] for u in urteil["aussagen"] if u["urteil"] == "nicht_belegt"}
                e = ergebnis[modell]
                je = e["je_art"].setdefault(art, {"faelle": 0, "gefunden": 0})
                je["faelle"] += 1
                if art == "original":
                    e["falschalarm"] += bool(schlecht)
                    je["gefunden"] += not schlecht
                elif art == "wertung":
                    treffer = any(WERTUNG in (w.get("wort") or "").lower() for w in urteil.get("wertende_woerter") or [])
                    je["gefunden"] += treffer
                    e["faelle"] += 1
                    e["gefunden"] += treffer
                else:
                    e["faelle"] += 1
                    e["gefunden"] += nr in schlecht
                    je["gefunden"] += nr in schlecht
    ki.zaehler.speichere()
    ziel = args.daten / "metriken" / "fehlertest.json"
    ziel.write_text(json.dumps({"karten": len(auswahl), "ergebnis": ergebnis}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for modell, e in ergebnis.items():
        quote = e["gefunden"] / e["faelle"] if e["faelle"] else 0
        print(f"{modell}: {e['gefunden']}/{e['faelle']} Fehler gefunden ({quote:.0%}), {e['falschalarm']} Fehlalarme bei Originalen")
        for art, je in e["je_art"].items():
            print(f"  {art}: {je['gefunden']}/{je['faelle']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
