"""Algorithmus B (PLAN.md 6B): Wie wichtig ist eine Geschichte?

Wichtigkeit = (Quellenbreite + Lagerbreite + Tempo + Aufmerksamkeit + Platzierung, je gewichtet)
              × Mitreden-Faktor × Frische

Jede Geschichte bekommt eine Begründung in Worten, damit sich in der Werkbank nachvollziehen lässt,
warum sie oben oder unten steht.

⚠ Die Lagerbreite nutzt die Lager-Tabelle aus config/sources.yaml. Die ist ein Vorschlag und Teil
des Bias-Reviews durch den Nutzer (PLAN.md 8). Bis zur Freigabe dient die Rangliste nur der Werkbank.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

from .abgleich import GoogleTreffer
from .cluster import Meldung
from .text import Seltenheit, inhaltswoerter, woerter

LAGERGRUPPE = {"links": "links", "mitte_links": "links", "mitte": "mitte",
               "mitte_rechts": "rechts", "rechts": "rechts", "international": "international"}


@dataclass
class Bewertung:
    wichtigkeit: float
    format: str
    titel: str
    thema: str
    unterthema: str | None
    region: str
    teile: dict[str, float]
    faktoren: dict[str, float]
    begruendung: list[str]
    quellen: list[str]
    mitglieder: list[int] = field(default_factory=list)


class Themen:
    """Ordnet eine Geschichte über Schlüsselwörter und Quellen einem Thema zu (Phase 1, ohne KI)."""

    def __init__(self, pfad: Path):
        self.k = yaml.safe_load(pfad.read_text(encoding="utf-8"))

    @staticmethod
    def treffer(begriffe: list[str], wortliste: list[str]) -> int:
        """Zählt Treffer am Wortanfang: "ice-" passt auf "ice-strecke", nicht auf "vice-president".

        Ein "=" vorne verlangt das ganze Wort ("=fed" passt nicht auf "federer"). Mehrere Wörter
        müssen aufeinander folgen, jedes am Wortanfang ("trauer um" passt auf "trauern um").
        """
        def passt(wort: str, teil: str) -> bool:
            return wort == teil[1:] if teil.startswith("=") else wort.startswith(teil)

        n = 0
        for b in begriffe:
            teile = b.split()
            n += sum(1 for i in range(len(wortliste) - len(teile) + 1)
                     if all(passt(wortliste[i + j], t) for j, t in enumerate(teile)))
        return n

    def einordnen(self, meldungen: list[Meldung]) -> tuple[str, str | None, float, list[str]]:
        punkte: dict[tuple[str, str], float] = {}
        for m in meldungen:
            titelwoerter, textwoerter = woerter(m.titel), woerter(m.text)
            for thema, t in self.k["themen"].items():
                for unter, u in t["unterthemen"].items():
                    n = 2 * self.treffer(u["woerter"], titelwoerter) + self.treffer(u["woerter"], textwoerter)
                    if n:
                        punkte[(thema, unter)] = punkte.get((thema, unter), 0) + n
            hinweis = self.k["quelle_thema"].get(m.quelle.id)
            if hinweis:
                for unter in self.k["themen"][hinweis]["unterthemen"]:
                    punkte[(hinweis, unter)] = punkte.get((hinweis, unter), 0) + 0.5
        if not punkte:
            return "panorama", None, 0.7, ["kein Thema erkannt, Grundwert 0,7"]
        (thema, unter), _ = max(punkte.items(), key=lambda kv: kv[1])
        faktor = self.k["themen"][thema]["unterthemen"][unter]["faktor"]
        return thema, unter, faktor, [f"Thema {self.k['themen'][thema]['name']} / {unter} (Grundwert {faktor:.1f})"]

    def zusatzfaktoren(self, meldungen: list[Meldung]) -> tuple[float, list[str], str]:
        listen = [woerter(m.text) for m in meldungen]
        faktor, gruende = 1.0, []
        for name, e in self.k["ereignis"].items():
            if sum(1 for w in listen if self.treffer(e["woerter"], w)) >= max(1, len(listen) * 0.3):
                faktor *= e["faktor"]
                gruende.append(f"Ereignis „{name}“ ×{e['faktor']}")
        d = self.k["deutschland"]
        mit_bezug = sum(1 for w in listen if self.treffer(d["woerter"], w))
        if mit_bezug >= max(1, len(listen) * d["anteil"]):
            faktor *= d["faktor"]
            gruende.append(f"Deutschland-Bezug ×{d['faktor']}")
        region = "deutschland" if mit_bezug >= max(1, len(listen) * d["anteil"]) else "welt"
        if region == "welt":
            for r, begriffe in self.k["regionen"].items():
                if sum(1 for w in listen if self.treffer(begriffe, w)) >= max(1, len(listen) * 0.3):
                    region = r
                    break
        return faktor, gruende, region


@dataclass
class Signale:
    """Aufmerksamkeit: Google News Top (Rang, Schlagzeilen) und Google Trends (Suchbegriff und Suchmenge)."""

    news: list[dict]
    trends: list[dict]

    @classmethod
    def aus_dateien(cls, ordner: Path) -> "Signale":
        import json

        zeilen = [json.loads(z) for d in sorted(ordner.glob("*.jsonl")) for z in d.read_text(encoding="utf-8").splitlines() if z]
        neueste = {}
        for z in zeilen:  # je Signal nur der letzte Lauf zählt
            neueste[z["signal"]] = max(neueste.get(z["signal"], ""), z["zeit"])
        aktuell = [z for z in zeilen if z["zeit"] == neueste[z["signal"]]]
        return cls([z for z in aktuell if z["signal"].startswith("google_news")],
                   [z for z in aktuell if z["signal"].startswith("google_trends")])


def suchmenge(text: str | None) -> float:
    zahl = re.sub(r"[^0-9]", "", text or "")
    return float(zahl) if zahl else 0.0


class Bewerter:
    def __init__(self, gewichte_pfad: Path, themen_pfad: Path):
        self.g = yaml.safe_load(gewichte_pfad.read_text(encoding="utf-8"))
        self.themen = Themen(themen_pfad)

    def bewerte(self, geschichte: list[int], meldungen: list[Meldung], vektoren: np.ndarray, signale: Signale,
                jetzt: datetime, google: list[GoogleTreffer] = (), seltenheit: Seltenheit | None = None,
                stand: datetime | None = None) -> Bewertung:
        """google: die Google-News-Einträge, die zu dieser Geschichte gehören (aus abgleich.gleiche_ab).
        stand: Zeit des letzten Sammellaufs; nur was da noch im Feed stand, zählt für die Platzierung."""
        ms = [meldungen[i] for i in geschichte]
        g = self.g
        begruendung: list[str] = []

        # Quellenbreite: unabhängige Redaktionen; fast gleiche Titel (kopierte Agenturtexte) zählen halb.
        # Mehrere Feeds derselben Redaktion (tagesschau und tagesschau Wirtschaft) zählen einmal.
        quellen = sorted({m.quelle.id for m in ms})
        erste_titel: dict[str, set[str]] = {}
        for m in ms:
            erste_titel.setdefault(m.quelle.redaktion, inhaltswoerter(m.titel))
        breite_eff, kopien = 0.0, 0
        gezaehlt: list[set[str]] = []
        for r in sorted(erste_titel):
            w = erste_titel[r]
            kopie = any(w and len(w & x) / len(w | x) >= 0.8 for x in gezaehlt)
            breite_eff += 0.5 if kopie else 1.0
            kopien += kopie
            gezaehlt.append(w)
        breite = math.log2(1 + breite_eff)
        zusatz = f" ({kopien} fast gleiche Titel halb gezählt)" if kopien else ""
        if len(erste_titel) < len(quellen):
            zusatz += f" ({len(erste_titel)} Redaktionen)"
        begruendung.append(f"{len(quellen)} Quellen{zusatz}")

        # Lagerbreite: links, Mitte, rechts, international (Vorschlag, Teil des Bias-Reviews)
        gruppen = sorted({LAGERGRUPPE[m.quelle.lager] for m in ms})
        lager = 2 * len(gruppen) / 4
        begruendung.append("Lager: " + ", ".join(gruppen))

        # Tempo: Meldungen in den letzten Stunden
        fenster = g["tempo_fenster_h"] * 3600
        neu = sum(1 for m in ms if (jetzt - m.zeit).total_seconds() <= fenster)
        tempo = math.log2(1 + neu)
        if neu:
            begruendung.append(f"{neu} Meldungen in den letzten {g['tempo_fenster_h']} Std")

        # Aufmerksamkeit: Google News Top. Google sortiert die Liste nach Rubriken (erst Schlagzeilen,
        # dann Wirtschaft, Tech, Unterhaltung, Sport, Gesundheit). Ein Platz weiter unten heißt also nicht
        # "unwichtig", nur "andere Rubrik". Deshalb zählt: Schlagzeile voll, sonst ein fester Anteil.
        gn = g["google_news"]
        aufmerksamkeit = 0.0
        beste: dict[str, GoogleTreffer] = {}
        for t in google:
            if t.signal not in beste or t.rang < beste[t.signal].rang:
                beste[t.signal] = t
        for t in beste.values():
            schlagzeile = t.rang <= gn["schlagzeilen_bis"]
            aufmerksamkeit += gn["land"].get(t.land, 1.0) * (1.0 if schlagzeile else gn["rubrik_anteil"])
            begruendung.append(f"Google News {t.land} Platz {t.rang}" + ("" if schlagzeile else " (Rubrik)"))

        # Google Trends: Suchbegriff muss in der Geschichte vorkommen. Einzelne Wörter zählen nur,
        # wenn sie selten sind und in einem Titel stehen ("cancer" im Teaser reicht nicht).
        titelwoerter = set().union(*(inhaltswoerter(m.titel) for m in ms))
        alle_woerter = set().union(*(m.woerter for m in ms))
        trends = 0.0
        for t in signale.trends:
            begriff = inhaltswoerter(t["titel"])
            if not begriff or not begriff <= alle_woerter:
                continue
            if len(begriff) == 1:
                (wort,) = begriff
                selten = seltenheit is None or seltenheit.zaehler.get(wort, 0) <= seltenheit.n * 0.02
                if wort not in titelwoerter or not selten:
                    continue
            land = t["signal"].rsplit("_", 1)[-1].upper()
            menge = suchmenge(t.get("suchmenge"))
            trends += (1.0 if land == "DE" else 0.6) * min(1.0, math.log10(1 + menge) / 5)
            begruendung.append(f"Google Trends {land}: „{t['titel']}“ ({t.get('suchmenge') or '?'})")
        aufmerksamkeit += min(g["trends_maximum"], trends)

        # Platzierung: weit oben bei Leitmedien, und zwar im letzten Sammellauf (nicht vor einem Tag).
        bis = g["platzierung_bis"]
        aktuell_ab = (stand or jetzt) - timedelta(hours=2)
        plaetze: dict[str, int] = {}
        for m in ms:
            gesehen = datetime.strptime(m.teaser["zuletzt_gesehen"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if m.quelle.id in g["leitmedien"] and gesehen >= aktuell_ab:
                plaetze[m.quelle.id] = min(plaetze.get(m.quelle.id, 999), m.teaser["position_im_feed"])
        oben = {q: p for q, p in plaetze.items() if p <= bis}
        platzierung = min(3.0, sum((bis + 1 - p) / bis for p in oben.values()))
        if oben:
            begruendung.append("weit oben bei " + ", ".join(f"{q} (Platz {p})" for q, p in sorted(oben.items(), key=lambda x: x[1])))

        # Mitreden-Faktor und Frische
        thema, unter, grundwert, thema_gruende = self.themen.einordnen(ms)
        zusatz, zusatz_gruende, region = self.themen.zusatzfaktoren(ms)
        # PLAN.md 4.2: Live-Aufmerksamkeit kommt zum Mitreden-Faktor dazu und holt herein, was plötzlich gefragt ist.
        live = min(g["live_maximum"], g["live_faktor"] * aufmerksamkeit)
        mitreden = grundwert * zusatz + live
        if live:
            zusatz_gruende.append(f"Live-Aufmerksamkeit +{live:.2f}")
        alter_h = (jetzt - max(m.zeit for m in ms)).total_seconds() / 3600
        frische = max(g["frische_minimum"], 0.5 ** (max(0.0, alter_h) / g["frische_halbwertszeit_h"]))
        begruendung += thema_gruende + zusatz_gruende
        begruendung.append(f"neueste Meldung vor {alter_h:.0f} Std (Frische {frische:.2f})")

        w = g["gewichte"]
        teile = {"breite": breite, "lager": lager, "tempo": tempo, "aufmerksamkeit": aufmerksamkeit, "platzierung": platzierung}
        basis = sum(w[k] * v for k, v in teile.items())
        wichtigkeit = basis * mitreden * frische

        return Bewertung(
            wichtigkeit=round(wichtigkeit, 3), format="", titel=repraesentativ(ms, vektoren[geschichte]),
            thema=thema, unterthema=unter, region=region,
            teile={k: round(v, 2) for k, v in teile.items()},
            faktoren={"mitreden": round(mitreden, 2), "live": round(live, 2), "frische": round(frische, 2)},
            begruendung=begruendung, quellen=quellen, mitglieder=list(geschichte),
        )

    def formate_vergeben(self, bewertungen: list[Bewertung]) -> None:
        """Top-Story, Karte, Kurz & knapp oder weg (PLAN.md 4.1 und 6B)."""
        g = self.g
        bewertungen.sort(key=lambda b: -b.wichtigkeit)
        for i, b in enumerate(bewertungen):
            if b.wichtigkeit >= g["schwelle_karte"]:
                b.format = "top" if i < g["top_anzahl"] else "karte"
            elif b.wichtigkeit >= g["schwelle_kurz"]:
                b.format = "kurz"
            else:
                b.format = "weg"


def repraesentativ(ms: list[Meldung], vektoren: np.ndarray) -> str:
    """Der Titel, der am besten für die ganze Geschichte steht: Bericht vor Meinung, deutsch bevorzugt,
    nah an der Mitte."""
    mitte = vektoren.mean(axis=0)
    werte = vektoren @ mitte
    beste = max(range(len(ms)), key=lambda i: (not ms[i].teaser["ist_meinung"], ms[i].quelle.sprache == "de", werte[i]))
    return ms[beste].titel


def jetzt_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
