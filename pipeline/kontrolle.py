"""Kontrolle (PLAN.md 7 und 16): Nimmt unser Modell alles Relevante auf, und zwar richtig?

Drei Prüfungen, zusammengefasst in einer Ampel:
  1. Sammeln     Ist der letzte Sammellauf frisch? Welche Feeds fallen aus oder liefern nichts mehr?
  2. Abdeckung   Google-News-Schlagzeilen und die ersten Einträge jeder deutschen Rubrik, frisch geladen:
                 Haben wir jede Meldung als Top-Story, Karte oder Kurzmeldung? Wenn nicht: warum nicht?
  3. Abgleich    Prüfsets mit Handbewertung: Ordnet der Abgleich die Google-Meldungen richtig zu?
                 (Fängt Rückschritte ab, wenn Schwellen oder Code geändert werden.)

Schreibt in den Datenordner (privat, enthält Titel):
  metriken/kontrolle.jsonl        eine kurze Zeile je Lauf (Verlauf der Quoten)
  metriken/kontrolle-letzte.json  alle Details des letzten Laufs, auch jede fehlende Meldung

Aufruf:
  python -m pipeline.kontrolle --daten ../mitreden-rohdaten              mit frisch geladenen Google-Listen
  python -m pipeline.kontrolle --daten ../mitreden-rohdaten --nur-lesen  nichts schreiben, nur anzeigen
"""

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .abgleich import gleiche_ab, google_titel
from .cluster import Schwellen, baue_meldungen, bette_ein, finde_geschichten
from .collect import KENNUNG, lade_teaser, lies_jsonl, zeit
from .quellen import lade_konfiguration
from .score import Signale, jetzt_utc
from .teaser import iso, lies_signal
from .werkbank import Stand, berechne

GRUENDE = ("abgedeckt", "aussortiert", "unsicher", "fehlt")


def lade_referenzen(k: dict, jetzt: datetime) -> tuple[list[dict], list[str]]:
    """Google-News-Listen frisch laden: je Referenz die ersten Einträge mit verwandten Schlagzeilen."""
    eintraege, fehler = [], []
    for r in k["referenzen"]:
        try:
            anfrage = urllib.request.Request(r["url"], headers={"User-Agent": KENNUNG})
            with urllib.request.urlopen(anfrage, timeout=20) as antwort:
                liste = [e for e in lies_signal(antwort.read(), r["id"], jetzt) if e["titel"] != "undefined"][: r["anzahl"]]
        except Exception as e:  # Netz, Zeitüberschreitung
            fehler.append(f"{r['id']}: {type(e).__name__}: {e}"[:200])
            continue
        if not liste:
            fehler.append(f"{r['id']}: leer")
        eintraege += liste
    return eintraege, fehler


def pruefe_abdeckung(st: Stand, referenzen: list[dict], k: dict) -> dict:
    """Jeder Referenz-Eintrag bekommt einen Grund (abgedeckt, aussortiert, unsicher, fehlt)."""
    g = st.gewichte["google_news"]
    formulierungen = bette_ein([t for n in referenzen for t in google_titel(n)])
    treffer = gleiche_ab(referenzen, formulierungen, st.meldungen, st.vektoren, st.titel_vektoren,
                         st.ergebnis.geschichten, g)
    platz = {id(b): n for n, b in enumerate(st.sortiert, start=1)}
    gruppe = {r["id"]: r["gruppe"] for r in k["referenzen"]}
    liste = []
    for t in treffer:
        b = st.in_reihenfolge[t.geschichte if t.geschichte is not None else t.beste]
        if t.geschichte is None:
            grund = "unsicher" if t.wert >= g["unsicher"] else "fehlt"
        else:
            grund = "aussortiert" if b.format == "weg" else "abgedeckt"
        liste.append({"referenz": t.signal, "rang": t.rang, "titel": t.titel, "grund": grund,
                      "platz": platz[id(b)] if t.geschichte is not None else None, "format": b.format,
                      "wert": t.wert, "unser_titel": b.titel, "gruppe": gruppe[t.signal]})

    je_referenz = {}
    for r in k["referenzen"]:
        teil = [x for x in liste if x["referenz"] == r["id"]]
        je_referenz[r["id"]] = {gr: sum(1 for x in teil if x["grund"] == gr) for gr in GRUENDE}
        je_referenz[r["id"]]["quote"] = round(je_referenz[r["id"]]["abgedeckt"] / len(teil), 3) if teil else None
    def zusammen(name: str) -> dict:
        # Dieselbe Meldung steht oft in den Schlagzeilen und in einer Rubrik: nur einmal zählen.
        einmal: dict[str, dict] = {}
        for x in liste:
            if x["gruppe"] == name:
                einmal.setdefault(x["titel"].lower(), x)
        teil = list(einmal.values())
        return {"quote": round(sum(1 for x in teil if x["grund"] == "abgedeckt") / len(teil), 3) if teil else None,
                "anzahl": len(teil), "gruende": {gr: sum(1 for x in teil if x["grund"] == gr) for gr in GRUENDE}}

    kern = zusammen("kern")
    return {**kern, "rubriken": zusammen("rubrik"), "je_referenz": je_referenz,
            "nicht_abgedeckt": [x for x in liste if x["grund"] != "abgedeckt"]}


def pruefe_sammeln(daten: Path, k: dict, jetzt: datetime, quellen_ids: list[str]) -> dict:
    """Frische des letzten Laufs, ausgefallene Feeds, Quellen ohne neue Meldungen."""
    zustand_datei = daten / "sammeln" / "zustand.json"
    zustand = json.loads(zustand_datei.read_text(encoding="utf-8")) if zustand_datei.exists() else {}
    versuche = [zeit(z["letzter_versuch"]) for z in zustand.values() if z.get("letzter_versuch")]
    letzter = max(versuche, default=None)
    alter_h = round((jetzt - letzter).total_seconds() / 3600, 1) if letzter else None
    ausgefallen = sorted(q for q in quellen_ids if zustand.get(q, {}).get("fehler_in_folge", 0) >= k["max_fehler_in_folge"])
    nie_gesammelt = sorted(q for q in quellen_ids if q not in zustand)
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
    konfig = lade_konfiguration(config / "sources.yaml")
    quellen = {q.id: q for q in konfig.quellen}
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
        gruende.append("Feeds ausgefallen: " + ", ".join(s["ausgefallen"]))
    if s["ohne_neue_meldungen"]:
        gelb = True
        gruende.append(f"ohne neue Meldung seit {k['leer_seit_h']} Std: " + ", ".join(s["ohne_neue_meldungen"]))
    a = bericht.get("abdeckung")
    if a and a["quote"] is not None:
        if a["quote"] < k["warnung_abdeckung"]:
            rot = True
        elif a["quote"] < k["ziel_abdeckung"]:
            gelb = True
        if a["quote"] < k["ziel_abdeckung"]:
            gruende.append(f"Abdeckung Kern {a['quote']:.0%} (Ziel {k['ziel_abdeckung']:.0%})")
    if bericht.get("referenzen_fehler"):
        gelb = True
        gruende.append("Google-Listen nicht geladen: " + "; ".join(bericht["referenzen_fehler"]))
    p = bericht.get("pruefsets")
    echte = max(1, p["richtig"] + p["uebersehen"]) if p else 1
    if p and (p["falsch_zugeordnet"] > k["max_falsch_anteil"] * echte or p["uebersehen"] > k["max_uebersehen_anteil"] * echte):
        gelb = True
        gruende.append(f"Abgleich: {p['falsch_zugeordnet']} falsch zugeordnet, {p['uebersehen']} übersehen")
    return ("rot" if rot else "gelb" if gelb else "grün"), gruende


def kontrolle(daten: Path, config: Path, jetzt: datetime, pruefsets: Path | None) -> dict:
    k = yaml.safe_load((config / "kontrolle.yaml").read_text(encoding="utf-8"))
    quellen_ids = [q.id for q in lade_konfiguration(config / "sources.yaml").quellen]
    st = berechne(daten, config, jetzt)
    referenzen, referenzen_fehler = lade_referenzen(k, jetzt)
    bericht = {
        "zeit": iso(jetzt),
        "sammeln": pruefe_sammeln(daten, k, jetzt, quellen_ids),
        "meldungen": len(st.meldungen),
        "geschichten": len(st.sortiert),
        "formate": {f: sum(1 for b in st.sortiert if b.format == f) for f in ("top", "karte", "kurz", "weg")},
        "abdeckung": pruefe_abdeckung(st, referenzen, k) if referenzen else None,
        "referenzen_fehler": referenzen_fehler,
        "pruefsets": pruefe_pruefsets(pruefsets, config) if pruefsets else None,
    }
    bericht["ampel"], bericht["ampel_gruende"] = ampel(bericht, k)
    return bericht


def kurzfassung(b: dict) -> dict:
    """Eine Zeile für den Verlauf (ohne Titel)."""
    a = b["abdeckung"] or {}
    return {"zeit": b["zeit"], "ampel": b["ampel"], "quote": a.get("quote"), "gruende": a.get("gruende"),
            "quote_rubriken": a.get("rubriken", {}).get("quote"),
            "je_referenz": {r: v["quote"] for r, v in a.get("je_referenz", {}).items()},
            "meldungen": b["meldungen"], "geschichten": b["geschichten"], "formate": b["formate"],
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

    a = b["abdeckung"]
    print(f"Ampel: {b['ampel'].upper()}" + "".join(f"\n  · {g}" for g in b["ampel_gruende"]))
    s = b["sammeln"]
    print(f"Sammeln: letzter Lauf {s['letzter_lauf']} (vor {s['alter_h']} Std), ausgefallen: {', '.join(s['ausgefallen']) or '–'}")
    if a:
        for name, teil in (("Kern", a), ("Rubriken", a["rubriken"])):
            if teil["quote"] is not None:
                print(f"Abdeckung {name}: {teil['quote']:.0%} von {teil['anzahl']} Meldungen · "
                      + ", ".join(f"{k} {v}" for k, v in teil["gruende"].items()))
        print("  je Liste: " + " · ".join(f"{r} {v['quote']:.0%}" for r, v in a["je_referenz"].items() if v["quote"] is not None))
        for x in a["nicht_abgedeckt"]:
            if x["gruppe"] != "info":
                print(f"  {x['grund']:11} {x['referenz']} {x['rang']}: {x['titel'][:80]}")
    p = b["pruefsets"]
    if p:
        print(f"Abgleich (Prüfsets): {p['richtig']} richtig, {p['falsch_zugeordnet']} falsch zugeordnet, {p['uebersehen']} übersehen")
        for f in p["fehler"]:
            print(f"  {f[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
