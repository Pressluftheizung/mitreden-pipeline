"""Abgleich mit Google News Top (PLAN.md 5.2 und 7): Welche unserer Geschichten meint Google?

Ein Google-News-Eintrag bündelt Schlagzeilen mehrerer Redaktionen zum selben Ereignis. Wir vergleichen
jede dieser Formulierungen mit jeder unserer Meldungen, einmal mit dem Titel allein und einmal mit Titel
und Teaser, und rechnen seltene gemeinsame Wörter (Namen, Orte) als Bonus dazu, wie bei den Dubletten.
Jeder Google-Eintrag gehört höchstens zu einer Geschichte, nämlich der mit dem besten Treffer.

Die Bewertung (Aufmerksamkeit) und die Messung (Abdeckung) nutzen dasselbe Ergebnis.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from .cluster import Meldung
from .quellen import Quelle
from .teaser import KEINE_SCHLAGZEILE
from .text import Seltenheit, inhaltswoerter, produkte


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
    nur_google: bool = False  # keine eigene Geschichte passte: der Google-Eintrag ist selbst zur Geschichte geworden

    @property
    def land(self) -> str:
        return self.signal.rsplit("_", 1)[-1].upper()

    @property
    def schlagzeile(self) -> bool:
        return self.signal.startswith("google_news_top_")


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
    return [titel, *(v for v in eintrag.get("verwandt", []) if not KEINE_SCHLAGZEILE.match(v))]


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


GRUENDE = ("abgedeckt", "nur_google", "aussortiert", "unsicher", "fehlt")


def abdeckung(google: list[GoogleTreffer], in_reihenfolge: list, sortiert: list, gruppe_von: dict[str, str],
              g: dict, messen_bis: int = 10) -> dict:
    """Abdeckungsquote (PLAN.md 7): Haben wir jede Google-Meldung als Top-Story, Karte oder Kurzmeldung?

    Gemessen an den ersten Einträgen jeder Google-Liste. Jeder Eintrag bekommt einen Grund:
      abgedeckt    eine Geschichte aus unseren eigenen Quellen, die nicht wegfällt
      nur_google   keine eigene Geschichte passte; der Google-Eintrag steht selbst in der Rangliste
                   (drin, aber ein Hinweis auf eine fehlende Quelle)
      aussortiert  wir haben sie, aber sie fällt weg
      unsicher / fehlt  (nur ohne Google-Geschichten) ähnliche Geschichte unsicher bzw. keine
    """
    platz = {id(b): n for n, b in enumerate(sortiert, start=1)}
    liste = []
    for t in google:
        if t.rang > messen_bis:
            continue
        b = in_reihenfolge[t.geschichte if t.geschichte is not None else t.beste]
        if t.geschichte is None:
            grund = "unsicher" if t.wert >= g["unsicher"] else "fehlt"
        elif b.format == "weg":
            grund = "aussortiert"
        else:
            grund = "nur_google" if t.nur_google else "abgedeckt"
        liste.append({"signal": t.signal, "gruppe": gruppe_von.get(t.signal, "info"), "rang": t.rang, "titel": t.titel,
                      "grund": grund, "platz": platz[id(b)] if t.geschichte is not None else None,
                      "format": b.format, "wert": t.wert, "aehnlichkeit": t.aehnlichkeit, "woerter": t.woerter,
                      "unser_titel": b.titel})

    def zusammen(teil: list[dict]) -> dict:
        # Dieselbe Meldung steht oft in mehreren Listen: nur einmal zählen.
        einmal: dict[str, dict] = {}
        for x in teil:
            einmal.setdefault(x["titel"].lower(), x)
        teil = list(einmal.values())
        if not teil:
            return {"quote": None, "quote_eigene": None, "anzahl": 0, "gruende": {}}
        zaehle = {gr: sum(1 for x in teil if x["grund"] == gr) for gr in GRUENDE}
        return {"quote": round((zaehle["abgedeckt"] + zaehle["nur_google"]) / len(teil), 3),
                "quote_eigene": round(zaehle["abgedeckt"] / len(teil), 3), "anzahl": len(teil), "gruende": zaehle}

    gruppen = sorted({x["gruppe"] for x in liste})
    return {"gesamt": zusammen(liste),
            "gruppen": {gr: zusammen([x for x in liste if x["gruppe"] == gr]) for gr in gruppen},
            "listen": {s: zusammen([x for x in liste if x["signal"] == s]) for s in sorted({x["signal"] for x in liste})},
            "liste": liste}


def _google_quelle(sprache: str) -> Quelle:
    return Quelle(id="google_news", name="Google News", url="https://news.google.com", sprache=sprache, region="welt",
                  lager="unbekannt", art="aggregator", volltext_erlaubt=False, bezahlschranke="nein")


def ergaenze_google_geschichten(news: list[dict], formulierungen: np.ndarray, treffer: list[GoogleTreffer],
                                meldungen: list[Meldung], vektoren: np.ndarray, titel_vektoren: np.ndarray,
                                geschichten: list[list[int]], g: dict) -> tuple[np.ndarray, np.ndarray]:
    """Nichts soll fehlen: Passt zu einem Google-Eintrag keine eigene Geschichte, wird er selbst zur Geschichte.

    Ihre Meldungen sind die Schlagzeilen des Eintrags (Titel und Link, kein Text). Dieselbe Meldung aus
    mehreren Listen (Schlagzeilen und Rubrik, Deutschland und USA) wird zu einer Geschichte zusammengelegt.
    Ergänzt meldungen und geschichten, setzt in treffer die neue Geschichte und gibt die ergänzten Vektoren zurück.
    """
    start = np.cumsum([0] + [len(google_titel(n)) for n in news])
    offen = [k for k, t in enumerate(treffer) if t.geschichte is None]
    if not offen:
        return vektoren, titel_vektoren
    # Zusammenlegen: gleiche Formulierung oder sehr ähnliche Hauptschlagzeile (Union-Find).
    wurzel = {k: k for k in offen}

    def finde(k: int) -> int:
        while wurzel[k] != k:
            wurzel[k] = wurzel[wurzel[k]]
            k = wurzel[k]
        return k

    haupt = formulierungen[[start[k] for k in offen]]
    aehnlich = haupt @ haupt.T
    texte: dict[str, int] = {}
    for a, k in enumerate(offen):
        for text in google_titel(news[k]):
            if text.lower() in texte:
                wurzel[finde(k)] = finde(texte[text.lower()])
            texte.setdefault(text.lower(), k)
        for b in range(a):
            if aehnlich[a, b] >= g["gleich"]:
                wurzel[finde(k)] = finde(offen[b])
    gruppen: dict[int, list[int]] = {}
    for k in offen:
        gruppen.setdefault(finde(k), []).append(k)

    neue_vektoren = []
    for gruppe in gruppen.values():
        n = len(geschichten)
        mitglieder, gesehen = [], set()
        for k in sorted(gruppe, key=lambda k: (not treffer[k].schlagzeile, treffer[k].rang)):
            eintrag = news[k]
            sprache = "de" if eintrag["signal"].endswith("_de") else "en"
            zeit = datetime.strptime(eintrag["zeit"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            for v, text in enumerate(google_titel(eintrag)):
                if text.lower() in gesehen:
                    continue
                gesehen.add(text.lower())
                teaser = {"id": f"google:{eintrag['signal']}:{eintrag['rang']}:{v}", "quelle": "google_news",
                          "url": eintrag.get("url"), "titel": text, "teaser": "", "veroeffentlicht": eintrag["zeit"],
                          "gesammelt": eintrag["zeit"], "zuletzt_gesehen": eintrag["zeit"], "ist_meinung": False,
                          "bezahlschranke": None, "position_im_feed": eintrag["rang"]}
                mitglieder.append(len(meldungen))
                meldungen.append(Meldung(teaser, _google_quelle(sprache), text, inhaltswoerter(text), produkte(text), zeit))
                neue_vektoren.append(formulierungen[start[k] + v])
            treffer[k].geschichte, treffer[k].beste, treffer[k].nur_google = n, n, True
        geschichten.append(mitglieder)
    neu = np.array(neue_vektoren, dtype=np.float32)
    return np.vstack([vektoren, neu]), np.vstack([titel_vektoren, neu])
