"""Wissenskarten als eigener Schritt nach der Ausgabe (Umbau 16.11.2026).

Früher liefen sie am Ende von pipeline/ausgabe.py mit; weil der Nachrichtenbau gut eine Stunde dauert,
griff dort eine Zeitgrenze und es entstanden nur 4–6 statt 16 Karten je Lauf. Jetzt speichert der
Workflow erst die Nachrichten, danach baut dieser Schritt die Wissenskarten ohne Zeitgrenze.

Aufruf:
  python -m pipeline.wissen_lauf --daten ../mitreden-rohdaten
"""

import argparse
import sys
from datetime import timezone
from pathlib import Path

from . import wissen
from .ki import KI, Zaehler
from .score import jetzt_utc


def main() -> int:
    parser = argparse.ArgumentParser(description="Mitreden: neue Wissenskarten bauen")
    parser.add_argument("--daten", type=Path, required=True, help="Checkout des Branchs rohdaten")
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--anzahl", type=int, help="statt wissen_je_lauf aus ki.config.json")
    args = parser.parse_args()
    ki = KI(args.config / "ki.config.json", Zaehler(args.daten / "metriken" / "ki-verbrauch.json"))
    anzahl = args.anzahl or ki.k["je_bau"].get("wissen_je_lauf", 6)
    w = wissen.baue(args.daten, args.config, ki, jetzt_utc().astimezone(timezone.utc), anzahl)
    ki.zaehler.speichere()
    print(f"Wissen: {w['gebaut']} neu, {w['gesamt']} von {w['lehrplan']} Themen fertig"
          f" (verworfen {w['verworfen']}, ohne Quelle {w['ohne_quelle']}){' · ' + '; '.join(w['fehler']) if w['fehler'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
