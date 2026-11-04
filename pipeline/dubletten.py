"""Dubletten mit KI (PLAN.md 6A: „Grauzonen-Fälle entscheidet die KI gesammelt mit Ja/Nein“).

Der Algorithmus in cluster.py legt nur sichere Paare zusammen. Danach bleiben zwei Fehlerarten:
  - eine Geschichte steckt in mehreren Gruppen (mehrere Karten, oder Karte und Kurzzeile zugleich),
  - eine Gruppe enthält fremde Meldungen (Frauen-Pokal und Bundesliga in einer Karte).

Dieser Schritt läuft nur im Kartenbau (Gemini Flash Lite, wenige gebündelte Anfragen) und nur für
Geschichten, die gezeigt würden:
  1. Aufteilen: Karten-Geschichten und uneinheitliche Kurzmeldungen gehen als Liste von Schlagzeilen
     an die KI, die sie nach Ereignis gruppiert. Fremde Meldungen werden eigene Geschichten.
  2. Zusammenlegen: Paare ähnlicher Geschichten (Grauzone, ähnlicher Inhalt oder gemeinsame seltene
     Namen) beurteilt die KI mit gleich / verschieden. Gleiche werden eine Geschichte.
Danach wird alles neu bewertet (werkbank.berechne).

Die Urteile werden einige Tage gemerkt (stories/dubletten.json), damit nicht jeder Bau dasselbe fragt
und Karten nicht zwischen den Ausgaben hin- und herspringen.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .ki import KeinModellFrei
from .text import Seltenheit, produkte_widersprechen

VOLL = ("top", "karte")
# Fassung der Prompts: ändert sie sich, werden gemerkte Urteile verworfen (sie stammen von alten Regeln).
FASSUNG = 2


def _iso(zeit: datetime) -> str:
    return zeit.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deckt(aktuell: set[str], alt: set[str], anteil: float) -> bool:
    return bool(aktuell) and len(aktuell & alt) >= anteil * len(aktuell)


class Speicher:
    """Frühere Urteile, über Meldungs-IDs wiedererkannt (Gruppen wachsen von Bau zu Bau)."""

    def __init__(self, datei: Path | None, jetzt: datetime, tage: float = 2):
        self.datei, self.jetzt = datei, jetzt
        roh = json.loads(datei.read_text(encoding="utf-8")) if datei and datei.exists() else {}
        if roh.get("fassung") != FASSUNG:
            roh = {}
        grenze = _iso(jetzt - timedelta(days=tage))
        self.paare = [p for p in roh.get("paare", []) if p["zeit"] >= grenze]
        self.gruppen = [g for g in roh.get("gruppen", []) if g["zeit"] >= grenze]

    def paar(self, a: set[str], b: set[str]) -> bool | None:
        for p in reversed(self.paare):  # neuestes Urteil zuerst
            pa, pb = set(p["a"]), set(p["b"])
            if (_deckt(a, pa, 0.5) and _deckt(b, pb, 0.5)) or (_deckt(a, pb, 0.5) and _deckt(b, pa, 0.5)):
                return p["gleich"]
        return None

    def merke_paar(self, a: set[str], b: set[str], gleich: bool) -> None:
        self.paare.append({"a": sorted(a), "b": sorted(b), "gleich": gleich, "zeit": _iso(self.jetzt)})

    def aufteilung(self, ids: set[str]) -> list[set[str]] | None:
        """Frühere Gruppierung, wenn sie mindestens 80 % der heutigen Meldungen schon kannte."""
        for g in reversed(self.gruppen):
            teile = [set(t) for t in g["teile"]]
            if _deckt(ids, set().union(*teile), 0.8):
                return [t & ids for t in teile if t & ids]
        return None

    def merke_aufteilung(self, teile: list[set[str]]) -> None:
        self.gruppen.append({"teile": [sorted(t) for t in teile], "zeit": _iso(self.jetzt)})

    def speichere(self) -> None:
        if self.datei:
            self.datei.parent.mkdir(parents=True, exist_ok=True)
            self.datei.write_text(json.dumps({"fassung": FASSUNG, "paare": self.paare, "gruppen": self.gruppen}, ensure_ascii=False,
                                             separators=(",", ":")) + "\n", encoding="utf-8")


# ---------- Hilfen ----------

def auswahl(mitglieder: list[int], meldungen: list, vektoren: np.ndarray, anzahl: int) -> list[int]:
    """Bis zu `anzahl` Meldungen mit verschiedenen Titeln: zuerst die typischste, dann jeweils die,
    die den bisher gewählten am wenigsten ähnelt (so sieht die KI auch fremde Meldungen)."""
    eindeutig: dict[str, int] = {}
    for i in mitglieder:
        eindeutig.setdefault(meldungen[i].titel.lower(), i)
    kandidaten = list(eindeutig.values())
    if len(kandidaten) <= 1:
        return kandidaten
    v = vektoren[kandidaten]
    sim = v @ v.T
    gewaehlt = [int(sim.mean(axis=1).argmax())]
    naechste = sim[gewaehlt[0]].copy()
    while len(gewaehlt) < min(anzahl, len(kandidaten)):
        k = int(naechste.argmin())
        gewaehlt.append(k)
        naechste = np.maximum(naechste, sim[k])
    return [kandidaten[k] for k in gewaehlt]


def einheitlichkeit(mitglieder: list[int], meldungen: list, vektoren: np.ndarray) -> float:
    """Wie gut passt die am schlechtesten passende Meldung zum Rest? (mittlere Ähnlichkeit, 1 = alle gleich)"""
    eindeutig = list({meldungen[i].titel.lower(): i for i in mitglieder}.values())
    if len(eindeutig) < 3:
        return 1.0
    v = vektoren[eindeutig]
    sim = v @ v.T
    return float(((sim.sum(axis=1) - 1) / (len(eindeutig) - 1)).min())


def zuordnen(mitglieder: list[int], gezeigt: list[int], gruppe_von: dict[int, int], meldungen: list,
             vektoren: np.ndarray) -> dict[int, int]:
    """Nicht gezeigte Meldungen kommen in die Gruppe der ähnlichsten gezeigten (gleicher Titel zuerst)."""
    titel = {meldungen[i].titel.lower(): gruppe_von[i] for i in gezeigt}
    ergebnis = dict(gruppe_von)
    for i in mitglieder:
        if i in ergebnis:
            continue
        t = meldungen[i].titel.lower()
        ergebnis[i] = titel[t] if t in titel else gruppe_von[gezeigt[int((vektoren[gezeigt] @ vektoren[i]).argmax())]]
    return ergebnis


def ids(mitglieder, meldungen) -> set[str]:
    return {meldungen[i].teaser["id"] for i in mitglieder}


def lies_gruppen(roh, anzahl: int) -> list[list[int]] | None:
    """Gruppen aus der KI-Antwort (Nummern ab 1). Fehlende Nummern kommen in die größte Gruppe,
    doppelte zählen beim ersten Auftreten. Unbrauchbar → None."""
    if not isinstance(roh, list):
        return None
    gesehen, gruppen = set(), []
    for g in roh:
        if not isinstance(g, list):
            return None
        teil = [int(x) - 1 for x in g if isinstance(x, (int, float, str)) and str(x).strip().isdigit()
                and 0 < int(x) <= anzahl and int(x) - 1 not in gesehen]
        gesehen.update(teil)
        if teil:
            gruppen.append(teil)
    if not gruppen:
        return None
    gruppen.sort(key=len, reverse=True)
    gruppen[0] += [k for k in range(anzahl) if k not in gesehen]
    return gruppen


def pruefe_aufteilung(antwort) -> None:
    if not isinstance(antwort, dict) or not isinstance(antwort.get("geschichten"), list):
        raise ValueError("geschichten fehlt")


def pruefe_paare(antwort) -> None:
    if not isinstance(antwort, dict) or not isinstance(antwort.get("paare"), list):
        raise ValueError("paare fehlt")


class Lauf:
    """Ein Dubletten-Durchgang: zählt Anfragen gegen die Obergrenze und sammelt den Bericht."""

    def __init__(self, ki, cfg: dict, speicher: Speicher):
        self.ki, self.cfg, self.speicher = ki, cfg, speicher
        self.anfragen = 0
        self.bericht: dict = {"aufteilen": {"geprueft": 0, "aus_speicher": 0, "aufgeteilt": 0, "abgetrennt": 0},
                              "zusammenlegen": {"kandidaten": 0, "aus_speicher": 0, "gefragt": 0, "gleich": 0, "kette_verhindert": 0},
                              "anfragen": 0, "fehler": [], "beispiele": {"aufgeteilt": [], "zusammengelegt": []}}

    def frage(self, aufgabe: str, daten: dict, pruefe):
        if self.anfragen >= self.cfg["max_anfragen"]:
            return None
        self.anfragen += 1
        self.bericht["anfragen"] = self.anfragen
        try:
            antwort, _ = self.ki.frage(aufgabe, json.dumps(daten, ensure_ascii=False), pruefe)
            return antwort
        except KeinModellFrei as e:
            self.bericht["fehler"].append(f"{aufgabe}: {e}")
            self.anfragen = self.cfg["max_anfragen"]  # Kontingent leer: nicht weiter versuchen
            return None

    # ---------- 1. Aufteilen ----------

    def aufteilen(self, geschichten: list[list[int]], formate: list[str], meldungen: list,
                  vektoren: np.ndarray) -> tuple[list[list[int]], list[set[int]]]:
        """Gibt die neuen Geschichten zurück (Teile hinten angehängt) und je Aufteilung die Indizes der Teile."""
        c = self.cfg
        kandidaten = []
        for n, (g, f) in enumerate(zip(geschichten, formate)):
            if f == "weg" or len({meldungen[i].titel.lower() for i in g}) < 3:
                continue
            e = einheitlichkeit(g, meldungen, vektoren)
            if f in VOLL or e < c["einheitlich"]:
                kandidaten.append((f not in VOLL, e, n))
        kandidaten = [n for *_, n in sorted(kandidaten)[:c["aufteilen_max"]]]

        teile_von: dict[int, list[list[int]]] = {}
        offen = []
        for n in kandidaten:
            frueher = self.speicher.aufteilung(ids(geschichten[n], meldungen))
            if frueher is None:
                offen.append(n)
                continue
            self.bericht["aufteilen"]["aus_speicher"] += 1
            gezeigt = [i for i in geschichten[n] if meldungen[i].teaser["id"] in set().union(*frueher)]
            gruppe_von = {i: k for i in gezeigt for k, t in enumerate(frueher) if meldungen[i].teaser["id"] in t}
            if len(frueher) > 1:
                z = zuordnen(geschichten[n], gezeigt, gruppe_von, meldungen, vektoren)
                teile_von[n] = [[i for i in geschichten[n] if z[i] == k] for k in range(len(frueher))]

        for portion in (offen[i:i + c["aufteilen_pro_anfrage"]] for i in range(0, len(offen), c["aufteilen_pro_anfrage"])):
            gezeigt = {n: auswahl(geschichten[n], meldungen, vektoren, c["aufteilen_titel"]) for n in portion}
            antwort = self.frage("aufteilen", {"geschichten": [
                {"nr": n, "schlagzeilen": [{"nr": k + 1, "titel": meldungen[i].titel} for k, i in enumerate(gezeigt[n])]}
                for n in portion]}, pruefe_aufteilung)
            if antwort is None:
                break
            for eintrag in antwort["geschichten"]:
                n = eintrag.get("nr") if isinstance(eintrag, dict) else None
                if n not in gezeigt:
                    continue
                gruppen = lies_gruppen(eintrag.get("gruppen"), len(gezeigt[n]))
                if gruppen is None:
                    continue
                self.bericht["aufteilen"]["geprueft"] += 1
                gruppe_von = {gezeigt[n][k]: g for g, teil in enumerate(gruppen) for k in teil}
                z = zuordnen(geschichten[n], gezeigt[n], gruppe_von, meldungen, vektoren)
                teile = [[i for i in geschichten[n] if z[i] == g] for g in range(len(gruppen))]
                self.speicher.merke_aufteilung([ids(t, meldungen) for t in teile])
                if len(teile) > 1:
                    teile_von[n] = teile

        neu = [list(g) for g in geschichten]
        getrennt = []
        for n, teile in teile_von.items():
            teile = sorted((t for t in teile if t), key=len, reverse=True)
            if len(teile) < 2:
                continue
            neu[n] = teile[0]
            indizes = {n}
            for t in teile[1:]:
                indizes.add(len(neu))
                neu.append(t)
            getrennt.append(indizes)
            self.bericht["aufteilen"]["aufgeteilt"] += 1
            self.bericht["aufteilen"]["abgetrennt"] += sum(len(t) for t in teile[1:])
            if len(self.bericht["beispiele"]["aufgeteilt"]) < 10:
                self.bericht["beispiele"]["aufgeteilt"].append(
                    [list(dict.fromkeys(meldungen[i].titel for i in t))[:3] for t in teile])
        return neu, getrennt

    # ---------- 2. Zusammenlegen ----------

    def zusammenlegen(self, geschichten: list[list[int]], formate: list[str], meldungen: list, vektoren: np.ndarray,
                      grauzone: list, getrennt: list[set[int]]) -> list[list[int]]:
        c = self.cfg
        aktiv = [n for n, f in enumerate(formate) if f != "weg"]
        if len(aktiv) < 2:
            return geschichten
        geschichte_von = {i: n for n, g in enumerate(geschichten) for i in g}

        # Mittelpunkt, Kernwörter (in mindestens 30 % der Meldungen) und Produkte jeder Geschichte
        mitte = np.array([vektoren[geschichten[n]].mean(axis=0) for n in aktiv], dtype=np.float32)
        mitte /= np.linalg.norm(mitte, axis=1, keepdims=True)
        seltenheit = Seltenheit([m.woerter for m in meldungen])
        kern, produkte = [], []
        for n in aktiv:
            zaehler: dict[str, int] = {}
            for i in geschichten[n]:
                for w in meldungen[i].woerter:
                    zaehler[w] = zaehler.get(w, 0) + 1
            kern.append({w for w, z in zaehler.items() if z >= 0.3 * len(geschichten[n])})
            produkte.append(set().union(*(meldungen[i].produkte for i in geschichten[n])))

        platz = {n: k for k, n in enumerate(aktiv)}
        sim = mitte @ mitte.T
        wertung: dict[tuple[int, int], float] = {}
        for x, y in zip(*np.nonzero(np.triu(sim >= c["vorauswahl"], k=1))):
            summe, gemeinsam = seltenheit.gemeinsam(kern[x], kern[y], c["selten"])
            w = float(sim[x, y]) + c["woerter_bonus"] * min(1.0, summe / c["woerter_voll"])
            if w >= c["kandidat"]:
                wertung[(int(x), int(y))] = w
        for p in grauzone:  # Grauzonen-Paare aus cluster.py immer, wenn beide Geschichten gezeigt würden
            a, b = geschichte_von.get(p.a), geschichte_von.get(p.b)
            if a is None or b is None or a == b or a not in platz or b not in platz:
                continue
            x, y = sorted((platz[a], platz[b]))
            wertung[(x, y)] = max(wertung.get((x, y), 0.0), p.wertung, float(sim[x, y]))

        kandidaten = [(x, y) for (x, y), w in sorted(wertung.items(), key=lambda kv: -kv[1])
                      if not produkte_widersprechen(produkte[x], produkte[y])][:c["max_paare"]]
        self.bericht["zusammenlegen"]["kandidaten"] = len(kandidaten)

        gleich: list[tuple[int, int]] = []
        verschieden: set[frozenset[int]] = set()  # Indizes in `geschichten`
        offen = []
        for x, y in kandidaten:
            frueher = self.speicher.paar(ids(geschichten[aktiv[x]], meldungen), ids(geschichten[aktiv[y]], meldungen))
            if frueher is None:
                offen.append((x, y))
            else:
                self.bericht["zusammenlegen"]["aus_speicher"] += 1
                if frueher:
                    gleich.append((x, y))
                else:
                    verschieden.add(frozenset((aktiv[x], aktiv[y])))

        def zeige(n: int) -> dict:
            gezeigt = auswahl(geschichten[n], meldungen, vektoren, 3)
            anriss = (meldungen[gezeigt[0]].teaser.get("teaser") or "")[:160]
            return {"schlagzeilen": [meldungen[i].titel for i in gezeigt], **({"anriss": anriss} if anriss else {})}

        for portion in (offen[i:i + c["paare_pro_anfrage"]] for i in range(0, len(offen), c["paare_pro_anfrage"])):
            antwort = self.frage("dubletten", {"paare": [{"nr": k + 1, "a": zeige(aktiv[x]), "b": zeige(aktiv[y])}
                                                         for k, (x, y) in enumerate(portion)]}, pruefe_paare)
            if antwort is None:
                break
            for eintrag in antwort["paare"]:
                if not isinstance(eintrag, dict) or not isinstance(eintrag.get("gleich"), bool):
                    continue
                try:
                    x, y = portion[int(eintrag.get("nr")) - 1]
                except (TypeError, ValueError, IndexError):
                    continue
                self.bericht["zusammenlegen"]["gefragt"] += 1
                self.speicher.merke_paar(ids(geschichten[aktiv[x]], meldungen), ids(geschichten[aktiv[y]], meldungen),
                                         eintrag["gleich"])
                if eintrag["gleich"]:
                    gleich.append((x, y))
                    if len(self.bericht["beispiele"]["zusammengelegt"]) < 15:
                        self.bericht["beispiele"]["zusammengelegt"].append(
                            [str(eintrag.get("ereignis_a", ""))[:80], str(eintrag.get("ereignis_b", ""))[:80],
                             meldungen[geschichten[aktiv[x]][0]].titel, meldungen[geschichten[aktiv[y]][0]].titel])
                else:
                    verschieden.add(frozenset((aktiv[x], aktiv[y])))

        # Gleiche Paare verbinden (auch über mehrere Schritte: A = B und B = C), aber nie zwei Gruppen,
        # zwischen denen ein Paar ausdrücklich als verschieden beurteilt wurde (A = B, B = C, A ≠ C)
        wurzel = list(range(len(geschichten)))
        gruppe = {n: {n} for n in range(len(geschichten))}

        def finde(n: int) -> int:
            while wurzel[n] != n:
                wurzel[n] = wurzel[wurzel[n]]
                n = wurzel[n]
            return n

        for x, y in gleich:
            a, b = finde(aktiv[x]), finde(aktiv[y])
            if a == b:
                continue
            if any(frozenset((p, q)) in verschieden for p in gruppe[a] for q in gruppe[b]):
                self.bericht["zusammenlegen"]["kette_verhindert"] += 1
                continue
            neu_w, alt_w = min(a, b), max(a, b)
            wurzel[alt_w] = neu_w
            gruppe[neu_w] |= gruppe.pop(alt_w)
            self.bericht["zusammenlegen"]["gleich"] += 1
        neu: dict[int, list[int]] = {}
        for n, g in enumerate(geschichten):
            neu.setdefault(finde(n), []).extend(g)
        return list(neu.values())


def pruefe_dubletten(st, ki, cfg: dict, speicher: Speicher, auswerten):
    """Aufteilen, neu bewerten, zusammenlegen, neu bewerten. Gibt den neuen Stand zurück.

    st: werkbank.Stand nach dem ersten Durchgang. auswerten(geschichten) bewertet eigene Geschichten neu
    (Google-Geschichten entstehen dabei jeweils frisch).
    """
    lauf = Lauf(ki, cfg, speicher)
    eigene = st.eigene_geschichten
    formate = [b.format for b in st.in_reihenfolge[:len(eigene)]]
    geschichten, getrennt = lauf.aufteilen(eigene, formate, st.meldungen, st.vektoren)
    if getrennt:
        st = auswerten(geschichten)
        formate = [b.format for b in st.in_reihenfolge[:len(geschichten)]]
    zusammen = lauf.zusammenlegen(geschichten, formate, st.meldungen, st.vektoren, st.ergebnis.grauzone, getrennt)
    if len(zusammen) != len(geschichten):
        st = auswerten(zusammen)
    speicher.speichere()
    st.dubletten = lauf.bericht
    return st
