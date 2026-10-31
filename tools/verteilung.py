"""Nur die Rangliste rechnen (ohne KI) und die Übersicht aller Geschichten ins private Repo schreiben.

Für die Kalibrierung der Schwellen zwischen den Bauläufen. Aufruf: python tools/verteilung.py --daten <rohdaten>
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.score import jetzt_utc  # noqa: E402
from pipeline.werkbank import berechne, schreibe_verteilung  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daten", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config"))
    args = parser.parse_args()
    jetzt = jetzt_utc()
    datei = schreibe_verteilung(berechne(args.daten, args.config, jetzt), args.daten, f"{jetzt:%Y-%m-%d-%H%M}-probe")
    print(f"geschrieben: {datei}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
