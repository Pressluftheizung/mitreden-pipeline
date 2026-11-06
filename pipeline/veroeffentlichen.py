"""Neueste Ausgabe in die App legen (privates Repo, Branch main, Datei karten/karten.json).

Nur die Felder, die die App anzeigt (Positivliste). Begründungen, Prüfprotokolle, Punktzahlen und
Story-Gedächtnis bleiben im Branch rohdaten. Cloudflare Pages baut die App danach neu (hinter Cloudflare Access).

⚠ Bis zum Bias-Review (PLAN.md 8) sind es Testkarten; die App zeigt das an (Feld „hinweis“).
Entscheidung des Nutzers 04.11.2026: früher Prototyp mit Testkarten (PLAN.md 15).

Aufruf:
  python -m pipeline.veroeffentlichen --daten daten --app app
"""

import argparse
import json
import sys
from pathlib import Path

AUSGABE_FELDER = ("hinweis", "ausgabe", "ausgabe_label", "erzeugt", "stand_text", "themen")
KARTEN_FELDER = ("id", "format", "thema", "unterthema", "region", "labels", "titel", "satz", "punkte", "mitreden",
                 "bild", "quellen", "lager_mix", "lager_hinweis", "ebenen", "erklaerung", "version", "aenderung", "neuigkeit", "kapitel", "sammel", "grundwissen", "nebenbei", "vorgeschichte", "zusammenhang", "fakten",
                 "aktualisiert", "meldungen", "werte", "tabelle", "termine")
BILD_FELDER = ("url", "alt", "art")
QUELLEN_FELDER = ("name", "lager", "url", "art", "bezahlschranke")


def fuer_app(ausgabe: dict) -> dict:
    karten = []
    for k in ausgabe["karten"]:
        neu = {f: k[f] for f in KARTEN_FELDER if f in k and k[f] is not None}
        if isinstance(neu.get("bild"), dict):
            neu["bild"] = {f: neu["bild"][f] for f in BILD_FELDER if f in neu["bild"]}
        neu["quellen"] = [{f: q[f] for f in QUELLEN_FELDER if f in q} for q in k.get("quellen", [])]
        neu.setdefault("labels", [])
        neu.setdefault("lager_mix", {})
        karten.append(neu)
    return {**{f: ausgabe[f] for f in AUSGABE_FELDER if f in ausgabe}, "karten": karten}


def neueste(ordner: Path) -> Path | None:
    dateien = sorted(ordner.glob("????-??-??-??.json"))
    return dateien[-1] if dateien else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Mitreden: neueste Ausgabe in die App legen")
    parser.add_argument("--daten", type=Path, required=True, help="Checkout des Branchs rohdaten")
    parser.add_argument("--app", type=Path, required=True, help="Checkout des Branchs main (App)")
    args = parser.parse_args()
    datei = neueste(args.daten / "testkarten")
    if datei is None:
        print("Keine Ausgabe gefunden, nichts zu tun.")
        return 0
    ziel = args.app / "karten" / "karten.json"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    daten = fuer_app(json.loads(datei.read_text(encoding="utf-8")))
    ziel.write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Ausgabe {datei.stem} mit {len(daten['karten'])} Karten in die App gelegt.")  # keine Inhalte ins Log
    return 0


if __name__ == "__main__":
    sys.exit(main())
