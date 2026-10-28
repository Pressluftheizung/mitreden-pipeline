"""Abgleich mit Google News Top (PLAN.md 5.2 und 7): Welche unserer Geschichten meint Google?

Ein Google-News-Eintrag bündelt Schlagzeilen mehrerer Redaktionen zum selben Ereignis. Wir vergleichen
jede dieser Formulierungen mit jeder unserer Meldungen, einmal mit dem Titel allein und einmal mit Titel
und Teaser, und rechnen seltene gemeinsame Wörter (Namen, Orte) als Bonus dazu, wie bei den Dubletten.
Jeder Google-Eintrag gehört höchstens zu einer Geschichte, nämlich der mit dem besten Treffer.

Die Bewertung (Aufmerksamkeit) und die Messung (Abdeckung) nutzen dasselbe Ergebnis.
"""

import re
from dataclasses import dataclass, field

import numpy as np

from .cluster import Meldung
from .text import Seltenheit, inhaltswoerter


@dataclass
class GoogleTreffer:
    signal: str
    rang: int
    titel: str
    geschichte: int | None  # Index der passenden Geschichte, None = keine passt sicher
    beste: int  # Index der ähnlichsten Geschichte (auch wenn sie nicht sicher passt)
    wert: float  # Ähnlichkeit plus Bonus für seltene gemeinsame Wörter
    aehnlichkeit: float
    woerter: list[str] = field(default_factory=list)

    @property
    def land(self) -> str:
        return self.signal.rsplit("_", 1)[-1].upper()


def google_titel(eintrag: dict) -> list[str]:
    """Hauptschlagzeile ohne Redaktionsnamen plus die verwandten Schlagzeilen anderer Redaktionen."""
    titel = eintrag["titel"]
    quelle = eintrag.get("quelle")
    if quelle:
        while titel.endswith(f" - {quelle}"):
            titel = titel[: -len(quelle) - 3]
    else:  # ältere Signale ohne Redaktionsnamen
        titel = titel.rsplit(" - ", 1)[0]
    titel = re.sub(r"^\(S\+\)\s*", "", titel.strip())  # Spiegel-Bezahlkennzeichen
    return [titel, *eintrag.get("verwandt", [])]


def gleiche_ab(news: list[dict], formulierungen: np.ndarray, meldungen: list[Meldung],
               text_vektoren: np.ndarray, titel_vektoren: np.ndarray, geschichten: list[list[int]],
               g: dict) -> list[GoogleTreffer]:
    """Ordnet jeden Google-Eintrag der passendsten Geschichte zu.

    formulierungen: Vektoren aller Formulierungen aus google_titel(), Eintrag für Eintrag hintereinander.
    g: Abschnitt google_news aus config/gewichte.yaml.
    """
    geschichte_von = np.empty(len(meldungen), dtype=int)
    for n, gs in enumerate(geschichten):
        geschichte_von[gs] = n
    seltenheit = Seltenheit([m.woerter for m in meldungen])
    grenze = max(2, seltenheit.n * g["max_haeufig"])
    index: dict[str, list[int]] = {}  # seltenes Wort → Meldungen, die es enthalten
    for i, m in enumerate(meldungen):
        for w in m.woerter:
            if seltenheit.zaehler[w] <= grenze:
                index.setdefault(w, []).append(i)
    # Ähnlichkeit jeder Formulierung zu jeder Meldung: die bessere aus "nur Titel" und "Titel und Teaser".
    aehnlich = np.maximum(formulierungen @ titel_vektoren.T, formulierungen @ text_vektoren.T)

    ergebnis = []
    start = 0
    for eintrag in news:
        varianten = google_titel(eintrag)
        # Je Formulierung und Geschichte: bester Wert und ob die Formulierung die Geschichte sicher trifft.
        bester_wert = np.full((len(varianten), len(geschichten)), -1.0)
        trifft = np.zeros((len(varianten), len(geschichten)), dtype=bool)
        details: dict[tuple[int, int], tuple[float, float, list[str]]] = {}  # (Formulierung, Geschichte) → Wert, Ähnlichkeit, Wörter
        for v, text in enumerate(varianten):
            sim = aehnlich[start + v]
            wert = sim.copy()
            woerter: dict[int, list[str]] = {}
            for w in inhaltswoerter(text):
                for i in index.get(w, ()):
                    woerter.setdefault(i, []).append(w)
            for i, liste in woerter.items():
                wert[i] += g["woerter_bonus"] * min(1.0, sum(seltenheit.gewicht(w) for w in liste) / g["woerter_voll"])
            # Sicher: sehr ähnlich, oder ähnlich genug und dieselben Namen/Begriffe. Gemeinsame Wörter allein
            # reichen nie ("Android-Trojaner" ist nicht "Android 18").
            sicher = (sim >= g["sicher_allein"]) | ((wert >= g["treffer"]) & (sim >= g["mindest_aehnlichkeit"]))
            np.maximum.at(bester_wert[v], geschichte_von, wert)
            np.logical_or.at(trifft[v], geschichte_von, sicher)
            for i in np.flatnonzero(wert >= bester_wert[v][geschichte_von]):
                details[(v, int(geschichte_von[i]))] = (float(wert[i]), float(sim[i]), sorted(woerter.get(i, []), key=seltenheit.gewicht, reverse=True))
        start += len(varianten)

        # Die Geschichte mit den meisten sicheren Treffern gewinnt, bei Gleichstand der höchste Wert.
        # Sicher zugeordnet ist sie, wenn die Hauptschlagzeile passt oder mindestens zwei Formulierungen.
        # So zieht ein einzelner Live-Blog unter den verwandten Schlagzeilen den Eintrag nicht zu einem anderen Thema.
        stimmen = trifft.sum(axis=0)
        hoechster = bester_wert.max(axis=0)
        n = int(np.lexsort((hoechster, stimmen))[-1])
        sicher = bool(trifft[0, n]) or stimmen[n] >= 2
        v = int(bester_wert[:, n].argmax())
        wert, sim, woerter = details.get((v, n), (float(hoechster[n]), 0.0, []))
        ergebnis.append(GoogleTreffer(
            signal=eintrag["signal"], rang=eintrag["rang"], titel=varianten[0],
            geschichte=n if sicher else None, beste=n,
            wert=round(wert, 3), aehnlichkeit=round(sim, 3), woerter=woerter[:5],
        ))
    return ergebnis
