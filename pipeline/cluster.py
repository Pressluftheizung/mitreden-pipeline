"""Algorithmus A (PLAN.md 6A): Welche Meldungen gehören zur selben Geschichte?

Zwei Meldungen gehören zusammen, wenn
  1. der Inhalt ähnlich ist (Sprachmodell über Titel und Teaser, auch deutsch gegen englisch),
  2. sie dieselben Hauptdarsteller nennen (gemeinsame seltene Wörter: Namen, Orte, Fachbegriffe),
  3. sie höchstens 36 Stunden auseinanderliegen,
  4. und sie keine unterschiedlichen Produkte nennen (Pixel-Regel).
Unsichere Paare kommen in die Grauzone. Sie werden nicht zusammengelegt; ab Phase 2 entscheidet dort die KI.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from .quellen import Quelle
from .text import Seltenheit, bereinige_titel, inhaltswoerter, produkte, produkte_widersprechen

MODELL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
TEASER_ZEICHEN = 300  # so viel vom Teaser fließt in die Ähnlichkeit ein


@dataclass(frozen=True)
class Schwellen:
    zeitfenster_h: float = 36
    sicher: float = 0.88          # so ähnlich, dass es ohne weitere Hinweise reicht
    zusammen: float = 0.72        # ähnlich plus mindestens ein gemeinsamer seltener Begriff
    sprachuebergreifend: float = 0.72  # deutsch/englisch: gemeinsame Wörter gibt es kaum, nur Ähnlichkeit zählt
    grauzone: float = 0.55
    gruppe: float = 0.62          # mittlere Ähnlichkeit, die zwei Gruppen zum Zusammenlegen brauchen
    kandidat: float = 0.45        # darunter wird ein Paar gar nicht erst betrachtet
    woerter_bonus: float = 0.25   # wie stark gemeinsame seltene Wörter die Ähnlichkeit erhöhen
    woerter_voll: float = 12.0    # ab dieser Seltenheitssumme zählt der volle Bonus
    selten: float = 0.05          # ein Wort gilt beim Vergleich als selten, wenn es in höchstens 5 % der Meldungen steht


@dataclass
class Meldung:
    teaser: dict
    quelle: Quelle
    text: str
    woerter: set[str]
    produkte: set[str]
    zeit: datetime

    @property
    def titel(self) -> str:
        return bereinige_titel(self.teaser["titel"], self.quelle.name)


@dataclass
class Paar:
    a: int
    b: int
    aehnlichkeit: float
    wertung: float
    gemeinsame_woerter: list[str]
    urteil: str  # "zusammen" | "grauzone" | "getrennt: Produkt"


@dataclass
class Ergebnis:
    geschichten: list[list[int]]
    paare: list[Paar] = field(default_factory=list)

    @property
    def grauzone(self) -> list[Paar]:
        return [p for p in self.paare if p.urteil == "grauzone"]


def zeitpunkt(teaser: dict) -> datetime:
    text = teaser.get("veroeffentlicht") or teaser["gesammelt"]
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def baue_meldungen(teaser: list[dict], quellen: dict[str, Quelle]) -> list[Meldung]:
    meldungen = []
    for t in teaser:
        q = quellen.get(t["quelle"])
        if q is None:
            continue  # Quelle wurde aus der Konfiguration entfernt
        titel = bereinige_titel(t["titel"], q.name)
        text = f"{titel}. {t.get('teaser', '')[:TEASER_ZEICHEN]}".strip()
        meldungen.append(Meldung(t, q, text, inhaltswoerter(text), produkte(text), zeitpunkt(t)))
    return meldungen


_modell = None


def bette_ein(texte: list[str]) -> np.ndarray:
    """Vektoren für alle Texte, auf Länge 1 normiert (Skalarprodukt = Kosinus-Ähnlichkeit)."""
    global _modell
    if _modell is None:
        from fastembed import TextEmbedding  # erst hier laden: langsam und nur für echte Läufe nötig

        _modell = TextEmbedding(MODELL)
    vektoren = np.array(list(_modell.embed(texte, batch_size=64)), dtype=np.float32)
    return vektoren / np.linalg.norm(vektoren, axis=1, keepdims=True)


def bewerte_paare(meldungen: list[Meldung], vektoren: np.ndarray, s: Schwellen) -> list[Paar]:
    """Kandidaten finden (ähnlicher Inhalt oder gemeinsames seltenes Wort) und jedes Paar beurteilen."""
    n = len(meldungen)
    aehnlich = vektoren @ vektoren.T
    seltenheit = Seltenheit([m.woerter for m in meldungen])

    kandidaten = set(zip(*np.nonzero(np.triu(aehnlich >= s.kandidat, k=1))))
    # Seltene gemeinsame Wörter finden auch Paare mit wenig Ähnlichkeit (Sturmtief / Orkanböen).
    index: dict[str, list[int]] = {}
    grenze = max(2, n * 0.02)
    for i, m in enumerate(meldungen):
        for w in m.woerter:
            if seltenheit.zaehler[w] <= grenze:
                index.setdefault(w, []).append(i)
    for liste in index.values():
        if len(liste) > 30:
            continue  # zu viele Treffer: das Wort ist für diese Auswahl nicht selten genug
        for x in range(len(liste)):
            for y in range(x + 1, len(liste)):
                kandidaten.add((liste[x], liste[y]))

    paare = []
    for a, b in kandidaten:
        ma, mb = meldungen[a], meldungen[b]
        if abs((ma.zeit - mb.zeit).total_seconds()) > s.zeitfenster_h * 3600:
            continue
        sim = float(aehnlich[a, b])
        summe, gemeinsam = seltenheit.gemeinsam(ma.woerter, mb.woerter, s.selten)
        wertung = sim + s.woerter_bonus * min(1.0, summe / s.woerter_voll)
        if produkte_widersprechen(ma.produkte, mb.produkte):
            urteil = "getrennt: Produkt"
        elif (sim >= s.sicher
              or (wertung >= s.zusammen and gemeinsam)
              or (ma.quelle.sprache != mb.quelle.sprache and sim >= s.sprachuebergreifend)):
            urteil = "zusammen"
        elif wertung >= s.grauzone:
            urteil = "grauzone"
        else:
            continue
        paare.append(Paar(int(a), int(b), round(sim, 3), round(wertung, 3), gemeinsam[:5], urteil))
    return paare


def bilde_geschichten(meldungen: list[Meldung], vektoren: np.ndarray, paare: list[Paar], s: Schwellen) -> list[list[int]]:
    """Sichere Paare zu Geschichten verbinden, stärkste zuerst.

    Zwei Gruppen werden nur zusammengelegt, wenn sie im Mittel ähnlich genug sind und keine
    widersprüchlichen Produkte enthalten. Das verhindert Ketten (A ähnelt B, B ähnelt C, aber A nicht C).
    """
    gruppe = list(range(len(meldungen)))
    mitglieder = {i: [i] for i in range(len(meldungen))}

    def wurzel(i: int) -> int:
        while gruppe[i] != i:
            gruppe[i] = gruppe[gruppe[i]]
            i = gruppe[i]
        return i

    for p in sorted((p for p in paare if p.urteil == "zusammen"), key=lambda p: -p.wertung):
        ra, rb = wurzel(p.a), wurzel(p.b)
        if ra == rb:
            continue
        a, b = mitglieder[ra], mitglieder[rb]
        if float((vektoren[a] @ vektoren[b].T).mean()) < s.gruppe:
            continue
        if any(produkte_widersprechen(meldungen[x].produkte, meldungen[y].produkte) for x in a for y in b):
            continue
        gruppe[rb] = ra
        mitglieder[ra] = a + b
        del mitglieder[rb]
    return sorted(mitglieder.values(), key=len, reverse=True)


def finde_geschichten(meldungen: list[Meldung], vektoren: np.ndarray | None = None,
                      s: Schwellen = Schwellen()) -> Ergebnis:
    if not meldungen:
        return Ergebnis([])
    if vektoren is None:
        vektoren = bette_ein([m.text for m in meldungen])
    paare = bewerte_paare(meldungen, vektoren, s)
    return Ergebnis(bilde_geschichten(meldungen, vektoren, paare, s), paare)
