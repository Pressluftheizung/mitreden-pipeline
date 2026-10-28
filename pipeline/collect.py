"""Sammellauf: lädt alle Feeds und Signale und speichert neue Teaser (stündlich, ohne KI).

Schreibt in den Datenordner (im Ablauf: Branch "rohdaten" des privaten Repos):
  teaser/JJJJ-MM-TT.jsonl    ein Teaser pro Zeile, Datei nach Tag des ersten Sehens
  signale/JJJJ-MM-TT.jsonl   Google Trends und Google News Top, eine Zeile pro Eintrag und Lauf
  sammeln/zustand.json       ETag/Last-Modified und letzter Status je Feed

Aufbewahrung: Teaser, die 48 Stunden nicht mehr im Feed standen oder vor mehr als 48 Stunden veröffentlicht
wurden, und Signale älter als 48 Stunden werden gelöscht. Volltexte werden hier nie geladen.

Aufruf:
  python -m pipeline.collect --daten <ordner>                         echte Feeds laden
  python -m pipeline.collect --daten <ordner> --aus-ordner <xml-ordner>  Feeds aus Dateien (<id>.xml), zum Testen
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .quellen import Konfiguration, lade_konfiguration
from .teaser import iso, lies_feed, lies_signal

AUFBEWAHRUNG = timedelta(hours=48)
ZEITLIMIT = 20  # Sekunden je Feed
GLEICHZEITIG = 12
KENNUNG = "Mitreden-Sammler/0.1 (+https://github.com/Pressluftheizung/mitreden-pipeline)"
# Fallen mehr Feeds aus, ist vermutlich etwas Grundsätzliches kaputt: Lauf als Fehler melden.
MAX_AUSFALL_ANTEIL = 0.5


@dataclass
class Abruf:
    id: str
    status: str  # "ok" | "unveraendert" | "fehler"
    daten: bytes = b""
    etag: str | None = None
    geaendert: str | None = None
    fehler: str | None = None


def lade(id_: str, url: str, zustand: dict) -> Abruf:
    kopf = {"User-Agent": KENNUNG, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}
    alt = zustand.get(id_, {})
    if alt.get("etag"):
        kopf["If-None-Match"] = alt["etag"]
    if alt.get("last_modified"):
        kopf["If-Modified-Since"] = alt["last_modified"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=kopf), timeout=ZEITLIMIT) as antwort:
            return Abruf(id_, "ok", antwort.read(), antwort.headers.get("ETag"), antwort.headers.get("Last-Modified"))
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return Abruf(id_, "unveraendert", etag=alt.get("etag"), geaendert=alt.get("last_modified"))
        return Abruf(id_, "fehler", fehler=f"HTTP {e.code}")
    except Exception as e:  # Zeitüberschreitung, DNS, TLS …
        return Abruf(id_, "fehler", fehler=f"{type(e).__name__}: {e}"[:200])


def lade_aus_ordner(id_: str, ordner: Path) -> Abruf:
    datei = ordner / f"{id_}.xml"
    if not datei.exists():
        return Abruf(id_, "fehler", fehler="Datei fehlt")
    return Abruf(id_, "ok", datei.read_bytes())


# ---------- Speicher ----------

def lies_jsonl(datei: Path) -> list[dict]:
    if not datei.exists():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]


def schreibe_jsonl(datei: Path, zeilen: list[dict]) -> None:
    datei.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(z, ensure_ascii=False) + "\n" for z in zeilen)
    datei.write_text(text, encoding="utf-8")


def zeit(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def lade_teaser(ordner: Path) -> dict[str, dict]:
    alle = {}
    for datei in sorted(ordner.glob("*.jsonl")):
        for t in lies_jsonl(datei):
            alle[t["id"]] = t
    return alle


def fuehre_zusammen(alle: dict[str, dict], neu: list[dict]) -> tuple[int, int]:
    """Neue Teaser aufnehmen, bekannte auf den aktuellen Stand bringen. Gibt (neu, aktualisiert) zurück."""
    anzahl_neu = anzahl_akt = 0
    for t in neu:
        alt = alle.get(t["id"])
        if alt is None:
            alle[t["id"]] = t
            anzahl_neu += 1
            continue
        # Erstes Sehen und Veröffentlichungszeit bleiben, der Rest zeigt den aktuellen Stand.
        if (alt["titel"], alt["teaser"]) != (t["titel"], t["teaser"]):
            anzahl_akt += 1
        alt.update({k: t[k] for k in ("titel", "teaser", "zuletzt_gesehen", "position_im_feed",
                                      "ist_meinung", "bezahlschranke", "rubrik")})
        alt["veroeffentlicht"] = alt.get("veroeffentlicht") or t["veroeffentlicht"]
    return anzahl_neu, anzahl_akt


def speichere_teaser(ordner: Path, alle: dict[str, dict], jetzt: datetime) -> int:
    """Nach Tag des ersten Sehens aufteilen, Altes löschen. Gibt die Zahl gelöschter Teaser zurück."""
    grenze = jetzt - AUFBEWAHRUNG
    nach_tag: dict[str, list[dict]] = {}
    geloescht = 0
    for t in alle.values():
        # Weg, wenn 48 Std nicht mehr im Feed oder vor mehr als 48 Std veröffentlicht
        # (manche Feeds führen alte Artikel tagelang weiter).
        if zeit(t["zuletzt_gesehen"]) < grenze or (t.get("veroeffentlicht") and zeit(t["veroeffentlicht"]) < grenze):
            geloescht += 1
            continue
        nach_tag.setdefault(t["gesammelt"][:10], []).append(t)
    for datei in ordner.glob("*.jsonl"):
        if datei.stem not in nach_tag:
            datei.unlink()
    for tag, zeilen in nach_tag.items():
        zeilen.sort(key=lambda t: (t["gesammelt"], t["quelle"], t["position_im_feed"]))
        schreibe_jsonl(ordner / f"{tag}.jsonl", zeilen)
    return geloescht


def speichere_signale(ordner: Path, neu: list[dict], jetzt: datetime) -> None:
    grenze = iso(jetzt - AUFBEWAHRUNG)
    # Von Google News zählt nur der letzte Stand: ältere Läufe derselben Liste werden ersetzt (hält die Dateien klein).
    ersetzt = {z["signal"] for z in neu if z["signal"].startswith("google_news")}
    tage = {d.stem for d in ordner.glob("*.jsonl")} | {jetzt.strftime("%Y-%m-%d")}
    for tag in sorted(tage):
        datei = ordner / f"{tag}.jsonl"
        zeilen = [z for z in lies_jsonl(datei) if z["zeit"] >= grenze and z["signal"] not in ersetzt]
        if tag == jetzt.strftime("%Y-%m-%d"):
            zeilen += neu
        if zeilen:
            schreibe_jsonl(datei, zeilen)
        elif datei.exists():
            datei.unlink()


# ---------- Ablauf ----------

def sammeln(konfig: Konfiguration, daten: Path, jetzt: datetime, aus_ordner: Path | None = None) -> dict:
    zustand_datei = daten / "sammeln" / "zustand.json"
    zustand = json.loads(zustand_datei.read_text(encoding="utf-8")) if zustand_datei.exists() else {}

    ziele = [(q.id, q.url) for q in konfig.quellen] + [(s.id, s.url) for s in konfig.signale]
    if aus_ordner:
        abrufe = [lade_aus_ordner(i, aus_ordner) for i, _ in ziele]
    else:
        with ThreadPoolExecutor(GLEICHZEITIG) as pool:
            abrufe = list(pool.map(lambda z: lade(z[0], z[1], zustand), ziele))
    nach_id = {a.id: a for a in abrufe}

    neue_teaser, neue_signale, fehler = [], [], []
    for q in konfig.quellen:
        a = nach_id[q.id]
        anzahl = None
        if a.status == "ok":
            eintraege = lies_feed(a.daten, q, konfig, jetzt)
            anzahl = len(eintraege)
            if anzahl == 0:
                a.status, a.fehler = "fehler", "Feed ohne lesbare Einträge"
            neue_teaser += eintraege
        if a.status == "fehler":
            fehler.append(f"{q.id}: {a.fehler}")
        zustand[q.id] = merke(zustand.get(q.id, {}), a, jetzt, anzahl)
    for s in konfig.signale:
        a = nach_id[s.id]
        if a.status == "ok":
            neue_signale += lies_signal(a.daten, s.id, jetzt)[: s.anzahl]
        elif a.status == "fehler":
            fehler.append(f"{s.id}: {a.fehler}")
        zustand[s.id] = merke(zustand.get(s.id, {}), a, jetzt, None)

    alle = lade_teaser(daten / "teaser")
    anzahl_neu, anzahl_akt = fuehre_zusammen(alle, neue_teaser)
    geloescht = speichere_teaser(daten / "teaser", alle, jetzt)
    speichere_signale(daten / "signale", neue_signale, jetzt)

    zustand_datei.parent.mkdir(parents=True, exist_ok=True)
    zustand_datei.write_text(json.dumps(zustand, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "zeit": iso(jetzt),
        "feeds": len(konfig.quellen),
        "feeds_fehler": sum(1 for q in konfig.quellen if nach_id[q.id].status == "fehler"),
        "teaser_neu": anzahl_neu,
        "teaser_geaendert": anzahl_akt,
        "teaser_geloescht": geloescht,
        "teaser_gesamt": len(alle) - geloescht,
        "signale": len(neue_signale),
        "fehler": fehler,
    }


def merke(alt: dict, a: Abruf, jetzt: datetime, anzahl: int | None) -> dict:
    neu = dict(alt)
    neu["letzter_versuch"] = iso(jetzt)
    neu["status"] = a.status
    if a.status == "fehler":
        neu["fehler"] = a.fehler
        neu["fehler_in_folge"] = alt.get("fehler_in_folge", 0) + 1
    else:
        neu.pop("fehler", None)
        neu["fehler_in_folge"] = 0
        neu["letzter_erfolg"] = iso(jetzt)
        neu["etag"] = a.etag
        neu["last_modified"] = a.geaendert
    if anzahl is not None:
        neu["eintraege"] = anzahl
    return neu


def main() -> int:
    parser = argparse.ArgumentParser(description="Mitreden: Feeds sammeln (ohne KI)")
    parser.add_argument("--daten", type=Path, required=True, help="Datenordner (Checkout des Branchs rohdaten)")
    parser.add_argument("--quellen", type=Path, default=Path("config/sources.yaml"))
    parser.add_argument("--aus-ordner", type=Path, help="Feeds aus <id>.xml-Dateien statt aus dem Netz (Test)")
    parser.add_argument("--jetzt", help="Zeitpunkt überschreiben, z. B. 2026-10-27T09:19:53Z (Test)")
    args = parser.parse_args()

    jetzt = datetime.now(timezone.utc) if not args.jetzt else datetime.fromisoformat(args.jetzt.replace("Z", "+00:00"))
    bericht = sammeln(lade_konfiguration(args.quellen), args.daten, jetzt.replace(microsecond=0), args.aus_ordner)

    print(f"Sammeln {bericht['zeit']}: {bericht['feeds'] - bericht['feeds_fehler']}/{bericht['feeds']} Feeds ok, "
          f"{bericht['teaser_neu']} neu, {bericht['teaser_geaendert']} geändert, "
          f"{bericht['teaser_geloescht']} gelöscht, {bericht['teaser_gesamt']} gespeichert, {bericht['signale']} Signale")
    for f in bericht["fehler"]:
        print(f"  ⚠ {f}")
    if bericht["feeds_fehler"] > bericht["feeds"] * MAX_AUSFALL_ANTEIL:
        print("Zu viele Feeds ausgefallen, Lauf gilt als fehlgeschlagen.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
