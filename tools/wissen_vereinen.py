"""Zwei Stände der Wissens-Datenbank vereinen (Fehler vom 14.11.2026).

Wenn während eines Baus zusätzlich Karten von Hand gespeichert werden, ändern beide dieselbe Datei
wissen/wissen.json. Git kann das nicht zusammenführen, der Speicherschritt scheiterte und der ganze
Lauf ging verloren. Dieses Skript vereint beide Stände: alle Karten aus beiden, bei derselben Karte
gewinnt die höhere Version, bei gleicher Version die jüngere.

Aufruf: python tools/wissen_vereinen.py FREMD.json EIGEN.json ZIEL.json
"""

import json
import sys
from pathlib import Path


def neuer(a: dict, b: dict) -> dict:
    return max(a, b, key=lambda k: (k.get("version", 0), k.get("erstellt", "")))


def vereinen(fremd: dict, eigen: dict) -> dict:
    ergebnis = dict(eigen)
    karten = dict(fremd.get("karten") or {})
    for tid, karte in (eigen.get("karten") or {}).items():
        karten[tid] = neuer(karten[tid], karte) if tid in karten else karte
    ergebnis["karten"] = karten
    ergebnis["stand"] = max(fremd.get("stand", ""), eigen.get("stand", ""))
    return ergebnis


def main() -> int:
    fremd, eigen, ziel = (Path(p) for p in sys.argv[1:4])
    d = vereinen(json.loads(fremd.read_text(encoding="utf-8")), json.loads(eigen.read_text(encoding="utf-8")))
    ziel.write_text(json.dumps(d, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"Wissen vereint: {len(d['karten'])} Karten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
