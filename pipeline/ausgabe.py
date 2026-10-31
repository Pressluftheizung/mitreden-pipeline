"""Eine Ausgabe (dreimal täglich): Karten bauen und danach kontrollieren, ob alles Relevante drin ist.

Rechnet die Rangliste nur einmal und nutzt sie für beides (spart einige Minuten je Lauf).
Fällt der Bau aus (z. B. KI nicht erreichbar), läuft die Kontrolle trotzdem.

Aufruf:
  python -m pipeline.ausgabe --daten ../mitreden-rohdaten
"""

import argparse
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import bauen, kontrolle
from .score import jetzt_utc
from .werkbank import berechne


def main() -> int:
    parser = argparse.ArgumentParser(description="Mitreden: Ausgabe bauen und kontrollieren")
    parser.add_argument("--daten", type=Path, required=True, help="Checkout des Branchs rohdaten")
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--jetzt", help="Zeitpunkt überschreiben, z. B. 2026-10-31T06:30:00Z")
    args = parser.parse_args()
    jetzt = (datetime.fromisoformat(args.jetzt.replace("Z", "+00:00")) if args.jetzt else jetzt_utc()).astimezone(timezone.utc)

    st = berechne(args.daten, args.config, jetzt)
    bau_ok = True
    try:
        bauen.drucke(bauen.bauen(args.daten, args.config, jetzt, st=st))
    except Exception:
        bau_ok = False
        traceback.print_exc()
    b = kontrolle.kontrolle(args.daten, args.config, jetzt, args.daten / "pruefset", st=st)
    if not bau_ok:
        b["ampel"], b["ampel_gruende"] = "rot", ["Kartenbau fehlgeschlagen"] + b["ampel_gruende"]
    kontrolle.schreibe(b, args.daten, jetzt)
    kontrolle.drucke(b)
    return 0 if bau_ok else 1


if __name__ == "__main__":
    sys.exit(main())
