"""Kontrolle (PLAN.md 7 und 16): Nimmt unser Modell alles Relevante auf, und zwar richtig?

Drei Prüfungen, zusammengefasst in einer Ampel:
  1. Sammeln     Ist der letzte Sammellauf frisch? Welche Feeds fallen aus oder liefern nichts mehr?
  2. Abdeckung   Google News (Schlagzeilen und Rubriken für Deutschland, USA, UK; je die ersten 10):
                 Steht jede Meldung bei uns als Top-Story, Karte oder Kurzmeldung? Und kam sie aus unseren
                 eigenen Quellen oder nur über Google (dann fehlt uns eine Quelle)?
  3. Abgleich    Prüfsets mit Handbewertung: Ordnet der Abgleich die Google-Meldungen richtig zu?
                 (Fängt Rückschritte ab, wenn Schwellen oder Code geändert werden.)

Schreibt in den Datenordner (privat, enthält Titel):
  metriken/kontrolle.jsonl        eine kurze Zeile je Lauf (Verlauf der Quoten, 60 Tage)
  metriken/kontrolle-letzte.json  alle Details des letzten Laufs, auch jede nicht abgedeckte Meldung

Aufruf:
  python -m pipeline.kontrolle --daten ../mitreden-rohdaten              mit Eintrag in metriken/
  python -m pipeline.kontrolle --daten ../mitreden-rohdaten --nur-lesen  nichts schreiben, nur anzeigen
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .abgleich import abdeckung, gleiche_ab, google_titel
from .cluster import Schwellen, baue_meldungen, bette_ein, finde_geschichten
from .collect import lade_teaser, lies_jsonl, zeit
from .quellen import lade_konfiguration
from .score import Signale, jetzt_utc
from .teaser import iso
from .werkbank import berechne


def pruefe_sammeln(daten: Path, k: dict, jetzt: datetime, quellen_ids: list[str], signal_ids: list[str]) -> dict:
    """Frische des letzten Laufs, ausgefallene Feeds und Google-Listen, Quellen ohne neue Meldungen."""
    zustand_datei = daten / "sammeln" / "zustand.json"
    zustand = json.loads(zustand_datei.read_text(encoding="utf-8")) if zustand_datei.exists() else {}
    versuche = [zeit(z["letzter_versuch"]) for z in zustand.values() if z.get("letzter_versuch")]
    letzter = max(versuche, default=None)
    alter_h = round((jetzt - letzter).total_seconds() / 3600, 1) if letzter else None
    alle = quellen_ids + signal_ids
    ausgefallen = sorted(q for q in alle if zustand.get(q, {}).get("fehler_in_folge", 0) >= k["max_fehler_in_folge"])
    nie_gesammelt = sorted(q for q in alle if q not in zustand)
    grenze = iso(jetzt - timedelta(hours=k["leer_seit_h"]))
    aktiv = {t["quelle"] for t in lade_teaser(daten / "teaser").values() if t["gesammelt"] >= grenze}
    leer = sorted(q for q in quellen_ids if q in zustand and q not in aktiv)
    return {"letzter_lauf": iso(letzter) if letzter else None, "alter_h": alter_h, "ausgefallen": ausgefallen,
            "nie_gesammelt": nie_gesammelt, "ohne_neue_meldungen": leer}


def pruefe_pruefsets(ordner: Path, config: Path) -> dict | None:
    """Abgleich gegen handbewertete Stände: richtig erkannt, falsch zugeordnet, übersehen."""
    sets = sorted(d for d in ordner.glob("*") if (d / "bewertung.json").exists()) if ordner.exists() else []
    if not sets:
        return None
    quellen = {q.id: q for q in lade_konfiguration(config / "sources.yaml").quellen}
    gewichte = yaml.safe_load((config / "gewichte.yaml").read_text(encoding="utf-8"))
    summe = {"richtig": 0, "falsch_zugeordnet": 0, "uebersehen": 0, "fehler": []}
    for d in sets:
        bewertung = json.loads((d / "bewertung.json").read_text(encoding="utf-8"))
        meldungen = baue_meldungen(list(lade_teaser(d / "teaser").values()), quellen)
        vektoren = bette_ein([m.text for m in meldungen])
        geschichten = finde_geschichten(meldungen, vektoren, Schwellen(**gewichte["dubletten"])).geschichten
        news = Signale.aus_dateien(d / "signale").news
        treffer = gleiche_ab(news, bette_ein([t for n in news for t in google_titel(n)]), meldungen, vektoren,
                             bette_ein([m.titel for m in meldungen]), geschichten, gewichte["google_news"])
        for t in treffer:
            richtig = t.rang in bewertung["richtig"].get(t.signal, [])
            if t.geschichte is not None and richtig:
                summe["richtig"] += 1
            elif t.geschichte is not None:
                summe["falsch_zugeordnet"] += 1
                summe["fehler"].append(f"{d.name}: {t.signal} {t.rang} falsch zugeordnet: {t.titel}")
            elif richtig:
                summe["uebersehen"] += 1
                summe["fehler"].append(f"{d.name}: {t.signal} {t.rang} übersehen: {t.titel}")
    return summe


def ampel(bericht: dict, k: dict) -> tuple[str, list[str]]:
    gruende = []
    rot = gelb = False
    s = bericht["sammeln"]
    if s["alter_h"] is None or s["alter_h"] > k["max_alter_sammeln_h"]:
        rot = True
        gruende.append(f"letzter Sammellauf vor {s['alter_h']} Std")
    if s["ausgefallen"]:
        gelb = True
        gruende.append("ausgefallen: " + ", ".join(s["ausgefallen"]))
    if s["ohne_neue_meldungen"]:
        gelb = True
        gruende.append(f"ohne neue Meldung seit {k['leer_seit_h']} Std: " + ", ".join(s["ohne_neue_meldungen"]))
    for gruppe, a in (bericht.get("abdeckung") or {}).items():
        if a["quote"] is None:
            continue
        if a["quote"] < k["warnung_abdeckung"]:
            rot = True
        elif a["quote"] < k["ziel_abdeckung"]:
            gelb = True
        if a["quote"] < k["ziel_abdeckung"]:
            gruende.append(f"Abdeckung {gruppe} {a['quote']:.0%} (Ziel {k['ziel_abdeckung']:.0%})")
        # Drin ist es dann zwar (über Google), aber uns fehlt eine Quelle für Deutschland: Hinweis.
        if gruppe == "kern" and a.get("quote_eigene") is not None and a["quote_eigene"] < k["warnung_eigene_kern"]:
            gelb = True
            gruende.append(f"Kern aus eigenen Quellen nur {a['quote_eigene']:.0%}: Quelle fehlt (siehe nur_google)")
    if not bericht.get("abdeckung"):
        gelb = True
        gruende.append("keine Google-News-Listen gespeichert")
    p = bericht.get("pruefsets")
    echte = max(1, p["richtig"] + p["uebersehen"]) if p else 1
    if p and (p["falsch_zugeordnet"] > k["max_falsch_anteil"] * echte or p["uebersehen"] > k["max_uebersehen_anteil"] * echte):
        gelb = True
        gruende.append(f"Abgleich: {p['falsch_zugeordnet']} falsch zugeordnet, {p['uebersehen']} übersehen")
    return ("rot" if rot else "gelb" if gelb else "grün"), gruende


def kontrolle(daten: Path, config: Path, jetzt: datetime, pruefsets: Path | None) -> dict:
    k = yaml.safe_load((config / "kontrolle.yaml").read_text(encoding="utf-8"))
    konfig = lade_konfiguration(config / "sources.yaml")
    gruppe_von = {s.id: s.gruppe for s in konfig.signale if s.gruppe}
    st = berechne(daten, config, jetzt)
    a = abdeckung(st.google, st.in_reihenfolge, st.sortiert, gruppe_von, st.gewichte["google_news"], k["messen_bis"])
    bericht = {
        "zeit": iso(jetzt),
        "sammeln": pruefe_sammeln(daten, k, jetzt, [q.id for q in konfig.quellen], [s.id for s in konfig.signale]),
        "meldungen": len(st.meldungen),
        "geschichten": len(st.sortiert),
        "formate": {f: sum(1 for b in st.sortiert if b.format == f) for f in ("top", "karte", "kurz", "weg")},
        "abdeckung": a["gruppen"],
        "listen": {s: v["quote"] for s, v in a["listen"].items()},
        "nicht_abgedeckt": [x for x in a["liste"] if x["grund"] != "abgedeckt"],
        "pruefsets": pruefe_pruefsets(pruefsets, config) if pruefsets else None,
    }
    bericht["ampel"], bericht["ampel_gruende"] = ampel(bericht, k)
    return bericht


def kurzfassung(b: dict) -> dict:
    """Eine Zeile für den Verlauf (ohne Titel)."""
    return {"zeit": b["zeit"], "ampel": b["ampel"],
            "abdeckung": {g: {"quote": v["quote"], "eigene": v["quote_eigene"]} for g, v in b["abdeckung"].items()},
            "listen": b["listen"], "meldungen": b["meldungen"], "geschichten": b["geschichten"], "formate": b["formate"],
            "sammeln_alter_h": b["sammeln"]["alter_h"], "ausgefallen": b["sammeln"]["ausgefallen"],
            "pruefsets": {k: v for k, v in (b["pruefsets"] or {}).items() if k != "fehler"} or None}


def main() -> int:
    parser = argparse.ArgumentParser(description="Mitreden: Kontrolle, ob alles Relevante aufgenommen wird")
    parser.add_argument("--daten", type=Path, required=True, help="Checkout des Branchs rohdaten")
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--pruefsets", type=Path, help="Ordner mit handbewerteten Ständen (Standard: <daten>/pruefset)")
    parser.add_argument("--nur-lesen", action="store_true", help="nichts in metriken/ schreiben")
    parser.add_argument("--jetzt", help="Zeitpunkt überschreiben, z. B. 2026-10-28T12:00:00Z")
    args = parser.parse_args()

    jetzt = datetime.fromisoformat(args.jetzt.replace("Z", "+00:00")) if args.jetzt else jetzt_utc()
    b = kontrolle(args.daten, args.config, jetzt.astimezone(timezone.utc), args.pruefsets or args.daten / "pruefset")

    if not args.nur_lesen:
        ordner = args.daten / "metriken"
        ordner.mkdir(parents=True, exist_ok=True)
        verlauf = [z for z in lies_jsonl(ordner / "kontrolle.jsonl") if z["zeit"] >= iso(jetzt - timedelta(days=60))]
        verlauf.append(kurzfassung(b))
        (ordner / "kontrolle.jsonl").write_text("".join(json.dumps(z, ensure_ascii=False) + "\n" for z in verlauf), encoding="utf-8")
        (ordner / "kontrolle-letzte.json").write_text(json.dumps(b, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"Ampel: {b['ampel'].upper()}" + "".join(f"\n  · {g}" for g in b["ampel_gruende"]))
    s = b["sammeln"]
    print(f"Sammeln: letzter Lauf {s['letzter_lauf']} (vor {s['alter_h']} Std), ausgefallen: {', '.join(s['ausgefallen']) or '–'}")
    for gruppe, a in b["abdeckung"].items():
        print(f"Abdeckung {gruppe}: {a['quote']:.0%} (eigene Quellen {a['quote_eigene']:.0%}) von {a['anzahl']} · "
              + ", ".join(f"{g} {n}" for g, n in a["gruende"].items() if n))
    print("  je Liste: " + " · ".join(f"{s.removeprefix('google_news_')} {q:.0%}" for s, q in b["listen"].items() if q is not None))
    for x in b["nicht_abgedeckt"]:
        print(f"  {x['grund']:11} {x['signal'].removeprefix('google_news_')} {x['rang']}: {x['titel'][:80]}")
    p = b["pruefsets"]
    if p:
        print(f"Abgleich (Prüfsets): {p['richtig']} richtig, {p['falsch_zugeordnet']} falsch zugeordnet, {p['uebersehen']} übersehen")
        for f in p["fehler"]:
            print(f"  {f[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
