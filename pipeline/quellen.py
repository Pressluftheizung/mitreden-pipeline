"""Lädt config/sources.yaml (Quellen und Aufmerksamkeits-Signale)."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

LAGER = {"links", "mitte_links", "mitte", "mitte_rechts", "rechts", "international"}
BEZAHLSCHRANKE = {"nein", "teilweise", "ja"}


@dataclass(frozen=True)
class Quelle:
    id: str
    name: str
    url: str
    sprache: str
    region: str
    lager: str
    art: str
    volltext_erlaubt: bool
    bezahlschranke: str
    gewicht: float = 1.0
    meinung_muster: tuple[str, ...] = ()
    bezahl_muster: tuple[str, ...] = ()
    bezahl_stichwoerter: tuple[str, ...] = ()
    bezahl_feld: str | None = None
    # True: Der Feed kennzeichnet Bezahlartikel zuverlässig, ohne Kennzeichen ist der Artikel frei.
    bezahl_markiert: bool = False
    # Mehrere Feeds desselben Hauses (z. B. tagesschau und tagesschau Wirtschaft) zählen als eine Redaktion.
    haus: str | None = None

    @property
    def redaktion(self) -> str:
        return self.haus or self.id


@dataclass(frozen=True)
class Signal:
    id: str
    url: str
    name: str | None = None
    anzahl: int | None = None  # nur die ersten Einträge speichern
    gruppe: str | None = None  # Google News: kern | rubrik | global (Maßstab der Kontrolle)


@dataclass(frozen=True)
class Konfiguration:
    quellen: list[Quelle]
    signale: list[Signal]
    meinung_muster: tuple[str, ...] = ()
    meinung_titel: tuple[str, ...] = ()
    meinung_stichwoerter: tuple[str, ...] = field(default_factory=tuple)
    werbung_titel: tuple[str, ...] = field(default_factory=tuple)


def lade_konfiguration(pfad: Path) -> Konfiguration:
    roh = yaml.safe_load(pfad.read_text(encoding="utf-8"))
    standard = roh.get("standard", {})
    quellen = []
    gesehen = set()
    for eintrag in roh["quellen"]:
        q = Quelle(
            **{k: v for k, v in eintrag.items() if k not in {"meinung_muster", "bezahl_muster", "bezahl_stichwoerter"}},
            meinung_muster=tuple(eintrag.get("meinung_muster", ())),
            bezahl_muster=tuple(eintrag.get("bezahl_muster", ())),
            bezahl_stichwoerter=tuple(eintrag.get("bezahl_stichwoerter", ())),
        )
        if q.id in gesehen:
            raise ValueError(f"Quelle doppelt: {q.id}")
        if q.lager not in LAGER:
            raise ValueError(f"{q.id}: unbekanntes Lager {q.lager!r}")
        if q.bezahlschranke not in BEZAHLSCHRANKE:
            raise ValueError(f"{q.id}: unbekannte Bezahlschranke {q.bezahlschranke!r}")
        gesehen.add(q.id)
        quellen.append(q)
    return Konfiguration(
        quellen=quellen,
        signale=[Signal(**s) for s in roh.get("signale", [])],
        meinung_muster=tuple(standard.get("meinung_muster", ())),
        meinung_titel=tuple(standard.get("meinung_titel", ())),
        meinung_stichwoerter=tuple(standard.get("meinung_stichwoerter", ())),
        werbung_titel=tuple(standard.get("werbung_titel", ())),
    )
