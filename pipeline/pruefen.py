"""Prüfung der Karten (PLAN.md 7): erst Regeln im Code, dann die Prüf-KI einer anderen Firma.

1. Code: Jede Zahl der Karte muss in einer Quelle stehen. Titel höchstens 8 Wörter, genau 3 Punkte.
2. Prüf-KI: jede Aussage belegt / nicht belegt / unklar, wertende Wörter, Neutralität, Seitentausch-Test.
3. Anwenden: Nicht belegte Punkte, Fakten und Mitreden-Sätze fliegen raus. Sind Titel oder Satz nicht belegt
   oder die Karte nicht neutral, bekommt sie eine Warnung (PLAN.md 8, B6).

⚠ Neutralität und Wort-Prüfung gehören zum Bias-Algorithmus (PLAN.md 8) und sind bis zum Review des
Nutzers nur Testwerte.
"""

import json
import re

ZAHL = re.compile(r"\d+(?:[.,]\d+)*")


def normalisiere_zahl(text: str) -> str:
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", text):  # Tausenderpunkte: 1.000 / 1,000
        return re.sub(r"[.,]", "", text)
    return text.replace(",", ".")


def zahlen(text: str) -> set[str]:
    return {normalisiere_zahl(z) for z in ZAHL.findall(text)}


def aussagen(karte: dict) -> list[dict]:
    """Alle prüfbaren Aussagen der Karte, nummeriert."""
    liste = [("titel", None, karte.get("titel")), ("satz", None, karte.get("satz"))]
    liste += [("punkte", i, p) for i, p in enumerate(karte.get("punkte") or [])]
    liste.append(("mitreden", None, karte.get("mitreden")))
    liste += [("faktenbox", i, f.get("fakt")) for i, f in enumerate(karte.get("faktenbox") or [])]
    return [{"nr": n, "feld": feld, "index": i, "text": text} for n, (feld, i, text) in enumerate(
        (x for x in liste if x[2]), start=1)]


def code_pruefung(karte: dict, quelltexte: list[str]) -> list[dict]:
    """Regeln ohne KI. Gibt Probleme als {"nr", "feld", "problem"} zurück."""
    bekannt = set().union(*(zahlen(t) for t in quelltexte)) if quelltexte else set()
    probleme = []
    for a in aussagen(karte):
        if a["feld"] == "mitreden":
            continue  # Einordnung darf Wissen und Zahlen von außerhalb der Quellen enthalten (Wunsch des Nutzers)
        fremd = sorted(z for z in zahlen(a["text"]) if z not in bekannt)
        if fremd:
            probleme.append({"nr": a["nr"], "feld": a["feld"], "index": a["index"],
                             "problem": f"Zahl nicht in den Quellen: {', '.join(fremd)}"})
    if len((karte.get("titel") or "").split()) > 8:
        probleme.append({"nr": 1, "feld": "titel", "index": None, "problem": "Titel länger als 8 Wörter", "form": True})
    if karte.get("punkte") is not None and len(karte["punkte"]) != 3:
        probleme.append({"nr": None, "feld": "punkte", "index": None, "problem": f"{len(karte['punkte'])} statt 3 Punkte", "form": True})
    return probleme


def pruef_anfrage(karte: dict, quellen: list[dict]) -> str:
    """Eingabe für die Prüf-KI: Quellen (gekürzt) und nummerierte Aussagen."""
    # Geprüft wird, was der Leser sieht. Die Faktenbox (steht am Ende der Nummerierung) prüft nur der Code;
    # das hält die Anfrage klein genug für das Minutenlimit der Prüf-KI.
    return json.dumps({
        "quellen": [{"id": q["id"], "outlet": q["redaktion"], "leaning": q["lager"], "type": q["art"],
                     "title": q["titel"], "text": (q.get("volltext") or q["text"])[:2500 if q.get("volltext") else 350]}
                    for q in quellen[:6]],
        "aussagen": [{"nr": a["nr"], "feld": a["feld"], "text": a["text"]} for a in aussagen(karte)
                     if a["feld"] not in ("faktenbox", "mitreden")],
        # Der Mitreden-Satz darf Wissen und Spekulation enthalten: nur auf Falsches und Parteinahme geprüft
        "mitreden": karte.get("mitreden"),
    }, ensure_ascii=False)


def pruefe_antwort(antwort: dict) -> None:
    """Wirft ValueError, wenn die Antwort der Prüf-KI nicht den erwarteten Aufbau hat."""
    if not isinstance(antwort, dict) or not isinstance(antwort.get("aussagen"), list):
        raise ValueError("aussagen fehlt")
    for a in antwort["aussagen"]:
        if a.get("urteil") not in {"belegt", "nicht_belegt", "unklar"} or not isinstance(a.get("nr"), int):
            raise ValueError(f"ungültiges Urteil: {a}")


def anwenden(karte: dict, code: list[dict], ki: dict | None, anbieter: str | None, modell: str | None) -> dict:
    """Wendet Code- und KI-Prüfung auf die Karte an (verändert sie) und gibt das Feld `pruefung` zurück."""
    nach_nr = {a["nr"]: a for a in aussagen(karte)}
    schlecht: dict[int, str] = {}
    for p in code:
        if p.get("nr") and not p.get("form"):
            schlecht[p["nr"]] = p["problem"]
    unklar = []
    if ki:
        for u in ki["aussagen"]:
            if u["nr"] not in nach_nr:
                continue
            if u["urteil"] == "nicht_belegt":
                schlecht.setdefault(u["nr"], u.get("grund") or "nicht belegt")
            elif u["urteil"] == "unklar":
                unklar.append(f"{nach_nr[u['nr']]['text'][:60]}: {u.get('grund', '')}")

    hinweise, warnung, mitreden_weg = [], False, False
    weg_punkte, weg_fakten = set(), set()
    for nr, grund in schlecht.items():
        a = nach_nr[nr]
        if a["feld"] == "punkte":
            weg_punkte.add(a["index"])
            hinweise.append(f"Punkt entfernt ({grund}): {a['text']}")
        elif a["feld"] == "faktenbox":
            weg_fakten.add(a["index"])
            hinweise.append(f"Fakt entfernt ({grund}): {a['text']}")
        elif a["feld"] == "mitreden":  # Zusatz ohne Beleg: lieber weglassen als warnen
            karte["mitreden"] = None
            mitreden_weg = True
            hinweise.append(f"Mitreden-Satz entfernt ({grund}): {a['text']}")
        else:
            warnung = True
            hinweise.append(f"{a['feld'].capitalize()} nicht belegt ({grund}): {a['text']}")
    if weg_punkte:
        rest = [p for i, p in enumerate(karte["punkte"]) if i not in weg_punkte]
        if rest:
            karte["punkte"] = rest
        else:
            warnung = True  # nichts übrig: Punkte bleiben, Karte bekommt eine Warnung
    if weg_fakten:
        karte["faktenbox"] = [f for i, f in enumerate(karte["faktenbox"]) if i not in weg_fakten]
    problem = (ki or {}).get("mitreden_problem")
    if problem in {"falsch", "spekulation_als_fakt", "parteiisch"} and karte.get("mitreden"):
        hinweise.append(f"Mitreden-Satz entfernt ({problem}: {(ki or {}).get('mitreden_grund', '')}): {karte['mitreden']}")
        karte["mitreden"] = None
        mitreden_weg = True
    hinweise += [p["problem"] for p in code if p.get("form")]
    hinweise += [f"unklar: {u}" for u in unklar]

    neutral = (ki or {}).get("neutralitaet")
    seitentausch = (ki or {}).get("seitentausch_ok")
    if neutral in {"deutlich_links", "deutlich_rechts"} or seitentausch is False:
        warnung = True
        hinweise.append(f"Neutralität: {neutral}, Seitentausch-Test {'bestanden' if seitentausch else 'nicht bestanden'}")
    woerter = (ki or {}).get("wertende_woerter") or []
    hinweise += [f"wertendes Wort „{w.get('wort')}“ → „{w.get('ersatz')}“" for w in woerter if isinstance(w, dict)]

    eingriffe = len(weg_punkte) + len(weg_fakten) + mitreden_weg
    if ki is None:
        ergebnis = "ungeprueft"
    elif warnung:
        ergebnis = "warnung"
    elif eingriffe:
        ergebnis = "korrigiert"
    else:
        ergebnis = "ok"
    return {"anbieter": anbieter, "modell": modell, "ergebnis": ergebnis, "neutralitaet": neutral,
            "seitentausch_ok": seitentausch, "mitreden_problem": (ki or {}).get("mitreden_problem"),
            "eingriffe": eingriffe, "unklar": len(unklar), "hinweise": hinweise}
