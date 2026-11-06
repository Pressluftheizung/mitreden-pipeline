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
    liste += [("kapitel", (j, i), p) for j, k in enumerate(karte.get("kapitel") or []) for i, p in enumerate(k["punkte"])]
    # „Was sagen andere“ ist Wiedergabe: jede Stimme muss in den Quellen stehen
    liste += [("stimmen", i, f"{s['wer']}: {s['text']}") for i, s in enumerate(karte.get("stimmen") or [])]
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


# Höchstlänge der Prüf-Anfrage in Zeichen: Groq nimmt 8000 Tokens pro Minute (Ein- plus Ausgabe). Abzüglich
# Prompt (~3000 Zeichen) und Antwort (1500 Tokens) bleiben gut 16000 Zeichen; 14000 lässt Luft.
PRUEF_MAX_ZEICHEN = 12000
# Stufen (Quellen, Zeichen je Volltext, Zeichen je Anreißer), von voll bis knapp. Seit 6.11. hat fast jede Karte
# Volltexte: höchstens 1500 Zeichen je Volltext, damit das Tageskontingent der Prüf-KI reicht (tpd 200k je Modell)
PRUEF_STUFEN = ((14, 1500, 500), (10, 1200, 400), (8, 1000, 300), (6, 600, 250), (5, 300, 200))


def pruef_anfrage(karte: dict, quellen: list[dict], max_zeichen: int = PRUEF_MAX_ZEICHEN) -> str:
    """Eingabe für die Prüf-KI: Quellen (gekürzt) und nummerierte Aussagen.

    Die Quellen werden so weit gekürzt, bis die Anfrage ins Minutenlimit der Prüf-KI passt (Sammelkarten
    mit 14 Quellen waren am 5.11. zu groß, der Bau hing deshalb).
    """
    # Geprüft wird, was der Leser sieht. Die Faktenbox (steht am Ende der Nummerierung) prüft nur der Code.
    rest = {
        "aussagen": [{"nr": a["nr"], "feld": a["feld"], "text": a["text"]} for a in aussagen(karte)
                     if a["feld"] not in ("faktenbox", "mitreden")],
        # Der Mitreden-Satz darf Wissen und Spekulation enthalten: nur auf Falsches und Parteinahme geprüft
        "mitreden": karte.get("mitreden"),
        # Grundwissen und Nebenbei: eigenes Wissen erlaubt, nur auf Falsches und Parteinahme geprüft
        "einordnung": [{"id": i, "text": t} for i, t in einordnung(karte)],
    }
    text = ""
    for anzahl, voll, kurz in PRUEF_STUFEN:
        # dieselben Quellen, die auch die schreibende KI hatte (Sammelkarten: bis 14, sonst 8)
        anzahl = min(anzahl, 14 if karte.get("kapitel") else 8)
        text = json.dumps({
            "quellen": [{"id": q["id"], "outlet": q["redaktion"], "leaning": q["lager"], "type": q["art"],
                         "title": q["titel"], "text": (q.get("volltext") or q["text"])[:voll if q.get("volltext") else kurz]}
                        for q in quellen[:anzahl]],
            **rest,
        }, ensure_ascii=False)
        if len(text) <= max_zeichen:
            break
    return text


# Hintergrund-Felder mit Kennbuchstabe für die Prüf-KI (Feedback 05./06.11.2026: mehr Hintergrund)
EINORDNUNG = (("g", "grundwissen"), ("v", "vorgeschichte"), ("z", "zusammenhang"), ("f", "fakten"), ("a", "fragen"),
              ("e", "erklaere"))


def einordnung(karte: dict) -> list[tuple[str, str]]:
    """Grundwissen (g1 …), Vorgeschichte (v1 …), Zusammenhang (z1 …), Fakten (f1 …), Nebenbei (n1) mit Kennung."""
    liste = []
    for zeichen, feld in EINORDNUNG:
        for i, x in enumerate(karte.get(feld) or []):
            text = (f"{x['begriff']}: {x['text']}" if feld == "grundwissen" else f"{x['frage']} {x['antwort']}"
                    if feld == "fragen" else f"{x['wort']}: {x['text']}" if feld == "erklaere"
                    else x["text"] if isinstance(x, dict) else x)
            liste.append((f"{zeichen}{i + 1}", text))
    if karte.get("nebenbei"):  # ältere Karten
        liste.append(("n1", karte["nebenbei"]))
    return liste


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
    weg_punkte, weg_fakten, weg_kapitel, weg_stimmen = set(), set(), set(), set()
    for nr, grund in schlecht.items():
        a = nach_nr[nr]
        if a["feld"] == "punkte":
            weg_punkte.add(a["index"])
            hinweise.append(f"Punkt entfernt ({grund}): {a['text']}")
        elif a["feld"] == "kapitel":
            weg_kapitel.add(a["index"])
            hinweise.append(f"Kapitelpunkt entfernt ({grund}): {a['text']}")
        elif a["feld"] == "stimmen":
            weg_stimmen.add(a["index"])
            hinweise.append(f"Stimme entfernt ({grund}): {a['text']}")
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
    if weg_kapitel:
        karte["kapitel"] = [{**k, "punkte": [p for i, p in enumerate(k["punkte"]) if (j, i) not in weg_kapitel]}
                            for j, k in enumerate(karte["kapitel"])]
        karte["kapitel"] = [k for k in karte["kapitel"] if k["punkte"]]
    if weg_stimmen:
        karte["stimmen"] = [s for i, s in enumerate(karte["stimmen"]) if i not in weg_stimmen]
    if weg_fakten:
        karte["faktenbox"] = [f for i, f in enumerate(karte["faktenbox"]) if i not in weg_fakten]
    problem = (ki or {}).get("mitreden_problem")
    if problem == "spekulation_als_fakt" and karte.get("mitreden"):  # nur beobachten (Wunsch des Nutzers: Freiheit)
        hinweise.append(f"Mitreden-Satz beobachten (Spekulation als Fakt? {(ki or {}).get('mitreden_grund', '')})")
    if problem in {"falsch", "parteiisch"} and karte.get("mitreden"):
        hinweise.append(f"Mitreden-Satz entfernt ({problem}: {(ki or {}).get('mitreden_grund', '')}): {karte['mitreden']}")
        karte["mitreden"] = None
        mitreden_weg = True
    falsch = {str(x) for x in (ki or {}).get("einordnung_falsch") or [] if isinstance(x, (str, int))}
    grund_e = (ki or {}).get("einordnung_grund") or ""
    weg_einordnung = 0
    for i, t in einordnung(karte):
        if i in falsch:
            weg_einordnung += 1
            hinweise.append(f"Einordnung entfernt ({grund_e}): {t}")
    if weg_einordnung:
        for zeichen, feld in EINORDNUNG:
            if karte.get(feld):
                karte[feld] = [x for j, x in enumerate(karte[feld]) if f"{zeichen}{j + 1}" not in falsch]
        if "n1" in falsch:
            karte["nebenbei"] = None
    hinweise += [p["problem"] for p in code if p.get("form")]
    hinweise += [f"unklar: {u}" for u in unklar]

    neutral = (ki or {}).get("neutralitaet")
    seitentausch = (ki or {}).get("seitentausch_ok")
    if neutral in {"deutlich_links", "deutlich_rechts"} or seitentausch is False:
        warnung = True
        hinweise.append(f"Neutralität: {neutral}, Seitentausch-Test {'bestanden' if seitentausch else 'nicht bestanden'}")
    woerter = (ki or {}).get("wertende_woerter") or []
    hinweise += [f"wertendes Wort „{w.get('wort')}“ → „{w.get('ersatz')}“" for w in woerter if isinstance(w, dict)]

    eingriffe = len(weg_punkte) + len(weg_fakten) + len(weg_kapitel) + weg_einordnung + mitreden_weg
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
