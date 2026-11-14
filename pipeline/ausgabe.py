"""Eine Ausgabe (dreimal täglich): Karten bauen und danach kontrollieren, ob alles Relevante drin ist.

Rechnet die Rangliste nur einmal und nutzt sie für beides (spart einige Minuten je Lauf).
Fällt der Bau aus (z. B. KI nicht erreichbar), läuft die Kontrolle trotzdem.

Aufruf:
  python -m pipeline.ausgabe --daten ../mitreden-rohdaten
"""

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import bauen, kontrolle, wissen
from .ki import KI, Zaehler
from .score import jetzt_utc
from .werkbank import berechne, schreibe_verteilung


def main() -> int:
    parser = argparse.ArgumentParser(description="Mitreden: Ausgabe bauen und kontrollieren")
    parser.add_argument("--daten", type=Path, required=True, help="Checkout des Branchs rohdaten")
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--jetzt", help="Zeitpunkt überschreiben, z. B. 2026-10-31T06:30:00Z")
    args = parser.parse_args()
    jetzt = (datetime.fromisoformat(args.jetzt.replace("Z", "+00:00")) if args.jetzt else jetzt_utc()).astimezone(timezone.utc)

    # Eine KI-Schicht für Dubletten und Kartenbau: ein gemeinsamer Kontingent-Zähler
    ki = KI(args.config / "ki.config.json", Zaehler(args.daten / "metriken" / "ki-verbrauch.json"))
    st = berechne(args.daten, args.config, jetzt, ki=ki)
    schreibe_verteilung(st, args.daten, bauen.ausgabe_id(jetzt)[0])
    bau_ok = True
    beginn = time.monotonic()
    try:
        bauen.drucke(bauen.bauen(args.daten, args.config, jetzt, ki=ki, st=st))
    except Exception:
        bau_ok = False
        traceback.print_exc()
        # Das Log bei GitHub ist ohne Anmeldung nicht lesbar: Fehler zusätzlich ins private Repo schreiben
        (args.daten / "metriken").mkdir(parents=True, exist_ok=True)
        (args.daten / "metriken" / f"bau-fehler-{bauen.ausgabe_id(jetzt)[0]}.txt").write_text(
            traceback.format_exc(), encoding="utf-8")
        ki.zaehler.speichere()  # Anfragen des Dubletten-Schritts trotzdem zählen
    # Allgemeinwissen (PLAN.md 18): ein paar neue Wissenskarten je Lauf; Fehler hier stoppen nichts
    try:
        # Die Ausgabe soll pünktlich fertig sein: hat der Nachrichtenbau schon lange gedauert, weniger Wissenskarten
        anzahl = ki.k["je_bau"].get("wissen_je_lauf", 6)
        if time.monotonic() - beginn > 15 * 60:
            anzahl = min(anzahl, 6)
        w = wissen.baue(args.daten, args.config, ki, jetzt, anzahl)
        print(f"Wissen: {w['gebaut']} neu, {w['gesamt']} von {w['lehrplan']} Themen fertig"
              f" (verworfen {w['verworfen']}, ohne Quelle {w['ohne_quelle']}){' · ' + '; '.join(w['fehler']) if w['fehler'] else ''}")
        ki.zaehler.speichere()
    except Exception:
        traceback.print_exc()
    b = kontrolle.kontrolle(args.daten, args.config, jetzt, args.daten / "pruefset", st=st)
    if not bau_ok:
        b["ampel"], b["ampel_gruende"] = "rot", ["Kartenbau fehlgeschlagen"] + b["ampel_gruende"]
    kontrolle.schreibe(b, args.daten, jetzt)
    kontrolle.drucke(b)
    return 0 if bau_ok else 1


if __name__ == "__main__":
    sys.exit(main())
