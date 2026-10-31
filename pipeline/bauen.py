"""Baulauf (PLAN.md 3 und 6D): aus der Rangliste werden Karten. Dreimal täglich.

Ablauf:
  1. Rangliste berechnen (Werkbank: Dubletten, Wichtigkeit, Google-Abgleich).
  2. Jede Geschichte mit der vom letzten Bau vergleichen (stories/stories.json):
       neu                → Gemini schreibt eine Karte (Top-Story mit dem besten Modell)
       bekannt, neue Infos → Gemini entscheidet: nichts Neues, ergänzen, korrigieren oder ersetzen
       bekannt, nichts neu → die Karte bleibt, nur Platz, Quellen und Format werden aktualisiert
       Kurz & knapp        → eine Zeile (gebündelt, viele pro Anfrage)
  3. Prüfen: Regeln im Code, dann die Prüf-KI einer anderen Firma (pipeline/pruefen.py).
  4. Bilder: verlinktes Artikelbild, sonst freies Bild von Wikimedia (pipeline/bilder.py).
  5. Ausgabe schreiben.

⚠ Bis zum Bias-Review des Nutzers (PLAN.md 8) entstehen nur Testkarten: testkarten/ im Branch rohdaten,
nicht cards/ im Branch main, den die App liest.

Schreibt in den Datenordner (privat):
  testkarten/JJJJ-MM-TT-HH.json    die Ausgabe
  testkarten/pruefansicht.html     Karte neben Quellen, zum Prüfen im Browser
  stories/stories.json             Gedächtnis: welche Geschichte welche Karte hat
  metriken/ki-verbrauch.json       Anfragen und Tokens je Modell und Tag
  metriken/bau-JJJJ-MM-TT-HH.json  Kennzahlen des Laufs

Aufruf:
  python -m pipeline.bauen --daten ../mitreden-rohdaten
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .bilder import waehle_bilder
from .ki import KI, KeinModellFrei, Zaehler
from .pruefen import anwenden, code_pruefung, pruef_anfrage, pruefe_antwort
from .pruefansicht import als_html
from .score import Bewertung, jetzt_utc
from .werkbank import Stand, berechne

AUSGABEN = (8, 15, 20)  # Uhrzeiten der Ausgaben (deutsche Zeit)
FAKTENQUELLEN = {"agentur", "oeffentlich_rechtlich"}


# ---------- Hilfen ----------

def berlin(jetzt: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return jetzt.astimezone(ZoneInfo("Europe/Berlin"))
    except Exception:  # ohne Zeitzonen-Daten (Windows): Winterzeit annehmen
        return jetzt.astimezone(timezone(timedelta(hours=1)))


def ausgabe_id(jetzt: datetime) -> tuple[str, str]:
    """Die nächste Ausgabe ab jetzt: ("2026-10-31-15", "Ausgabe 15:00")."""
    lokal = berlin(jetzt)
    for stunde in AUSGABEN:
        if lokal.hour < stunde:
            return f"{lokal:%Y-%m-%d}-{stunde:02d}", f"Ausgabe {stunde}:00"
    morgen = lokal + timedelta(days=1)
    return f"{morgen:%Y-%m-%d}-{AUSGABEN[0]:02d}", f"Ausgabe {AUSGABEN[0]}:00"


def slug(text: str) -> str:
    ersatz = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
    return re.sub(r"[^a-z0-9]+", "-", text.lower().translate(ersatz)).strip("-")[:24].strip("-")


def iso(zeit: datetime) -> str:
    return zeit.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def echte_ids(st: Stand, b: Bewertung) -> list[str]:
    return [st.meldungen[i].teaser["id"] for i in b.mitglieder if st.meldungen[i].quelle.id != "google_news"]


def nur_google(b: Bewertung) -> bool:
    return b.quellen == ["google_news"]


def google_schluessel(b: Bewertung) -> str:
    return "g:" + hashlib.sha1(b.titel.lower().encode()).hexdigest()[:12]


# ---------- Quellen für die KI (B3: ausgewogene Auswahl) ----------

def quellen_auswahl(st: Stand, b: Bewertung, anzahl: int = 6, mit_meinung: bool = False) -> list[dict]:
    """Bis zu `anzahl` Quellen, je Redaktion eine: zuerst Agentur oder öffentlich-rechtlich als Faktenbasis,
    dann je eine linke und eine rechte, dann der Rest nach Platzierung. Meinungen nur auf Wunsch (Positionen).

    ⚠ Teil des Bias-Algorithmus (PLAN.md 8, B3), bis zum Review nur Testbetrieb.
    """
    ms = sorted((st.meldungen[i] for i in b.mitglieder), key=lambda m: (m.teaser.get("position_im_feed") or 99, -m.zeit.timestamp()))
    berichte = [m for m in ms if not m.teaser.get("ist_meinung")]
    meinungen = [m for m in ms if m.teaser.get("ist_meinung")]
    gewaehlt, redaktionen = [], set()

    def nimm(m) -> None:
        if m.quelle.redaktion not in redaktionen and len(gewaehlt) < anzahl:
            redaktionen.add(m.quelle.redaktion)
            gewaehlt.append(m)

    for bedingung in (lambda m: m.quelle.art in FAKTENQUELLEN,
                      lambda m: m.quelle.lager in {"links", "mitte_links"},
                      lambda m: m.quelle.lager in {"rechts", "mitte_rechts"}):
        treffer = next((m for m in berichte if bedingung(m) and m.quelle.redaktion not in redaktionen), None)
        if treffer:
            nimm(treffer)
    for m in berichte:
        nimm(m)
    if mit_meinung:
        anzahl += 2
        for m in meinungen[:2]:
            nimm(m)
    return [{"id": f"q{n}", "redaktion": m.quelle.name, "lager": m.quelle.lager,
             "art": "meinung" if m.teaser.get("ist_meinung") else "bericht",
             "titel": m.titel, "text": (m.teaser.get("teaser") or "")[:500], "url": m.teaser.get("url")}
            for n, m in enumerate(gewaehlt, start=1)]


def quellen_liste(st: Stand, b: Bewertung) -> list[dict]:
    """Quellen für die Karte (Ebene „Quellen“), je Redaktion die am besten platzierte Meldung."""
    gesehen, liste = set(), []
    for i in sorted(b.mitglieder, key=lambda i: st.meldungen[i].teaser.get("position_im_feed") or 99):
        m = st.meldungen[i]
        if m.quelle.redaktion in gesehen:
            continue
        gesehen.add(m.quelle.redaktion)
        liste.append({"name": m.quelle.name, "lager": m.quelle.lager if m.quelle.lager != "unbekannt" else "international",
                      "url": m.teaser.get("url"), "art": "meinung" if m.teaser.get("ist_meinung") else "bericht",
                      "bezahlschranke": bool(m.teaser.get("bezahlschranke"))})
    return liste[:12]


def lager_mix(quellen: list[dict]) -> tuple[dict, str | None]:
    """Quellen je Lager und Hinweis bei Schieflage (B6, > 75 % aus einem Lager). ⚠ Bias-Review ausstehend."""
    mix: dict[str, int] = {}
    for q in quellen:
        if q["name"] != "Google News":
            mix[q["lager"]] = mix.get(q["lager"], 0) + 1
    links = mix.get("links", 0) + mix.get("mitte_links", 0)
    rechts = mix.get("rechts", 0) + mix.get("mitte_rechts", 0)
    gesamt = sum(mix.values())
    hinweis = None
    if gesamt >= 4 and links / gesamt > 0.75:
        hinweis = "Vor allem von linken Medien aufgegriffen"
    elif gesamt >= 4 and rechts / gesamt > 0.75:
        hinweis = "Vor allem von rechten Medien aufgegriffen"
    return mix, hinweis


# ---------- Gedächtnis: welche Geschichte hatte welche Karte ----------

def lade_stories(datei: Path) -> dict[str, dict]:
    if not datei.exists():
        return {}
    return {s["story_id"]: s for s in json.loads(datei.read_text(encoding="utf-8"))["stories"]}


def ordne_zu(st: Stand, aktiv: list[Bewertung], alt: dict[str, dict]) -> dict[int, str]:
    """Aktuelle Geschichte → Story-ID vom letzten Mal (gemeinsame Meldungen, jede alte Story höchstens einmal)."""
    besitzer: dict[str, str] = {}
    for sid, s in alt.items():
        for tid in s.get("teaser_ids", []):
            besitzer[tid] = sid
        if s.get("google_schluessel"):
            besitzer[s["google_schluessel"]] = sid
    kandidaten = []
    for n, b in enumerate(aktiv):
        ids = [google_schluessel(b)] if nur_google(b) else echte_ids(st, b)
        zaehler: dict[str, int] = {}
        for tid in ids:
            if tid in besitzer:
                zaehler[besitzer[tid]] = zaehler.get(besitzer[tid], 0) + 1
        for sid, gemeinsam in zaehler.items():
            alt_anzahl = max(1, len(alt[sid].get("teaser_ids", [])) or 1)
            if gemeinsam >= 3 or gemeinsam / alt_anzahl >= 0.3 or nur_google(b):
                kandidaten.append((gemeinsam, n, sid))
    zuordnung, vergeben = {}, set()
    for gemeinsam, n, sid in sorted(kandidaten, reverse=True):
        if n not in zuordnung and sid not in vergeben:
            zuordnung[n] = sid
            vergeben.add(sid)
    return zuordnung


def neue_story_id(st: Stand, b: Bewertung, jetzt: datetime) -> str:
    grund = (echte_ids(st, b) or [google_schluessel(b)])[0]
    return f"s-{jetzt:%Y%m%d}-{slug(b.titel) or 'geschichte'}-{hashlib.sha1(grund.encode()).hexdigest()[:4]}"


# ---------- KI-Aufträge ----------

# Manche Modelle antworten mit englischen Feldnamen. Die werden übersetzt statt verworfen.
ENGLISCH = {"title": "titel", "sentence": "satz", "summary": "satz", "points": "punkte", "bullets": "punkte",
            "facts": "faktenbox", "factbox": "faktenbox", "layers": "ebenen", "action": "aktion", "change": "aenderung"}


def pruefe_karten(antwort: dict) -> None:
    if not isinstance(antwort.get("karten"), list) or not antwort["karten"]:
        raise ValueError("karten fehlt")
    for k in antwort["karten"]:
        for englisch, deutsch in ENGLISCH.items():
            if englisch in k and deutsch not in k:
                k[deutsch] = k.pop(englisch)
        if k.get("aktion") == "nichts_neues":
            continue
        if not (isinstance(k.get("titel"), str) and isinstance(k.get("punkte"), list) and k.get("satz")):
            raise ValueError(f"Karte unvollständig: {str(k)[:80]}")


def pruefe_zeilen(antwort: dict) -> None:
    if not isinstance(antwort.get("zeilen"), list) or not all(isinstance(z.get("text"), str) for z in antwort["zeilen"]):
        raise ValueError("zeilen fehlt")


def in_portionen(liste: list, groesse: int) -> list[list]:
    return [liste[i:i + groesse] for i in range(0, len(liste), groesse)]


# ---------- Bau ----------

def bauen(daten: Path, config: Path, jetzt: datetime, ki: KI | None = None, st: Stand | None = None) -> dict:
    start = time.monotonic()
    kc = json.loads((config / "ki.config.json").read_text(encoding="utf-8"))
    jb = kc["je_bau"]
    themen = yaml.safe_load((config / "topics.yaml").read_text(encoding="utf-8"))["themen"]
    ausgabe, ausgabe_label = ausgabe_id(jetzt)
    ki = ki or KI(config / "ki.config.json", Zaehler(daten / "metriken" / "ki-verbrauch.json"))

    st = st or berechne(daten, config, jetzt)
    aktiv = [b for b in st.sortiert if b.format != "weg"]
    stories_datei = daten / "stories" / "stories.json"
    alt = lade_stories(stories_datei)
    zuordnung = ordne_zu(st, aktiv, alt)

    # Was ist zu tun?
    neu_voll, updates, kurz_neu = [], [], []  # Indizes in `aktiv`
    plan: dict[int, str] = {}
    budget = jb["max_neue_karten"]
    for n, b in enumerate(aktiv):
        vorher = alt.get(zuordnung.get(n, ""), {})
        karte_vorher = vorher.get("karte")
        voll = b.format in ("top", "karte") and not nur_google(b)
        if not voll:
            if vorher.get("zeile") or (karte_vorher and karte_vorher.get("satz")):
                plan[n] = "kurz_behalten"
            else:
                plan[n] = "kurz_neu"
                kurz_neu.append(n)
        elif karte_vorher and karte_vorher.get("titel"):
            neue = set(echte_ids(st, b)) - set(vorher.get("teaser_ids", []))
            if len(neue) >= 2 and budget > 0:
                plan[n] = "update"
                updates.append(n)
                budget -= 1
            else:
                plan[n] = "behalten"
        elif budget > 0:
            plan[n] = "neu"
            neu_voll.append(n)
            budget -= 1
        else:  # Budget dieses Laufs erschöpft: vorerst als Kurzmeldung
            plan[n] = "kurz_neu"
            kurz_neu.append(n)

    texte: dict[int, dict] = {}   # n → KI-Text der Karte
    quellen_ki: dict[int, list[dict]] = {}
    fehler: list[str] = []

    def schreibe(aufgabe: str, auswahl: list[int], groesse: int) -> None:
        for portion in in_portionen(auswahl, groesse):
            eingabe = []
            for n in portion:
                b = aktiv[n]
                qs = quellen_auswahl(st, b, 8 if b.format == "top" else 6, mit_meinung=b.format == "top")
                quellen_ki[n] = qs
                eintrag = {"story": f"s{n}", "format": b.format, "thema": b.thema, "region": b.region,
                           "quellen": [{k: q[k] for k in ("id", "redaktion", "lager", "art", "titel", "text")} for q in qs]}
                if aufgabe == "update":
                    k = alt[zuordnung[n]]["karte"]
                    eintrag["bisherige_karte"] = {f: k.get(f) for f in ("titel", "satz", "punkte", "mitreden", "faktenbox")}
                    neue = set(echte_ids(st, b)) - set(alt[zuordnung[n]].get("teaser_ids", []))
                    eintrag["quellen"] = [q for q in eintrag["quellen"]
                                          if any(st.meldungen[i].teaser["id"] in neue and st.meldungen[i].teaser.get("url") == q.get("url")
                                                 for i in b.mitglieder)] or eintrag["quellen"]
                eingabe.append(eintrag)
            try:
                antwort, modell = ki.frage(aufgabe, json.dumps({"geschichten": eingabe}, ensure_ascii=False), pruefe_karten)
            except KeinModellFrei as e:
                fehler.append(f"{aufgabe}: {e}")
                continue
            for k in antwort["karten"]:
                treffer = re.fullmatch(r"s(\d+)", str(k.get("story", "")))
                if treffer and int(treffer.group(1)) in portion:
                    k["_modell"] = modell
                    texte[int(treffer.group(1))] = k

    schreibe("top", [n for n in neu_voll if aktiv[n].format == "top"], jb["top_pro_anfrage"])
    schreibe("karte", [n for n in neu_voll if aktiv[n].format == "karte"], jb["karten_pro_anfrage"])
    schreibe("update", updates, 3)

    # Ohne KI-Text keine volle Karte: dann vorerst Kurzmeldung.
    for n in neu_voll:
        if n not in texte:
            plan[n] = "kurz_neu"
            kurz_neu.append(n)
    for n in updates:
        if n not in texte or texte[n].get("aktion") == "nichts_neues":
            plan[n] = "behalten"

    zeilen: dict[int, str] = {}
    for portion in in_portionen(sorted(set(kurz_neu)), jb["kurz_pro_anfrage"]):
        eingabe = [{"story": f"s{n}", "schlagzeilen": list(dict.fromkeys(
            st.meldungen[i].titel for i in aktiv[n].mitglieder))[:3]} for n in portion]
        try:
            antwort, _ = ki.frage("kurz", json.dumps({"geschichten": eingabe}, ensure_ascii=False), pruefe_zeilen)
            for z in antwort["zeilen"]:
                treffer = re.fullmatch(r"s(\d+)", str(z.get("story", "")))
                if treffer:
                    zeilen[int(treffer.group(1))] = z["text"].strip()
        except KeinModellFrei as e:
            fehler.append(f"kurz: {e}")
    for n in kurz_neu:
        zeilen.setdefault(n, aktiv[n].titel)  # ohne KI: der beste Originaltitel

    # Karten zusammensetzen
    karten_voll: dict[int, dict] = {}
    for n, b in enumerate(aktiv):
        if plan[n] not in ("neu", "update", "behalten"):
            continue
        sid = zuordnung.get(n) or neue_story_id(st, b, jetzt)
        vorher = alt.get(sid, {}).get("karte") or {}
        qliste = quellen_liste(st, b)
        mix, hinweis = lager_mix(qliste)
        karte = dict(vorher)
        if plan[n] in ("neu", "update"):
            t = texte[n]
            for feld in ("titel", "satz", "punkte", "mitreden", "mitreden_art", "faktenbox", "ebenen"):
                if feld in t:
                    karte[feld] = t[feld]
            karte["punkte"] = (karte.get("punkte") or [])[:3]
            karte["version"] = vorher.get("version", 0) + 1
            karte["aktualisiert"] = iso(jetzt)
            karte["aenderung"] = t.get("aenderung") if plan[n] == "update" else None
            karte["_neu_geschrieben"] = True
            karte["_ki"] = {"modell": t.get("_modell"), "fehlt": t.get("fehlt") or [],
                            "bildbegriff": t.get("bildbegriff"), "bild_alt": t.get("bild_alt")}
            karte["_quellen_ki"] = quellen_ki.get(n, [])
            if plan[n] == "update":
                karte["_aktion"] = t.get("aktion")
        redaktionen = {q["name"] for q in qliste if q["name"] != "Google News"}
        labels = []
        if len(redaktionen) == 1:
            labels.append(f"Bisher nur von {next(iter(redaktionen))} berichtet")
        if qliste and all(q["bezahlschranke"] for q in qliste):
            labels.append("Bezahlschranke")
        if karte.get("version", 1) > 1:
            labels.append("Korrigiert" if karte.get("_aktion") == "korrigieren" else "Aktualisiert")
        karte.update({
            "id": f"k-{sid}", "story_id": sid, "format": b.format, "punktzahl": round(b.wichtigkeit, 2),
            "thema": b.thema, "unterthema": b.unterthema or "", "region": b.region,
            "labels": labels, "quellen": qliste, "lager_mix": mix, "lager_hinweis": hinweis,
            "erstellt": vorher.get("erstellt") or iso(jetzt),
            "begruendung": b.begruendung,
        })
        karte.setdefault("version", 1)
        karte.setdefault("aktualisiert", iso(jetzt))
        karte.setdefault("aenderung", None)
        karte.setdefault("ebenen", [])
        karten_voll[n] = karte

    # Prüfen (nur neu geschriebene Karten; die übrigen behalten ihre Prüfung)
    pruefungen = 0
    for n, karte in karten_voll.items():
        if not karte.get("_neu_geschrieben"):
            continue
        b = aktiv[n]
        alle_texte = [f"{st.meldungen[i].titel} {st.meldungen[i].teaser.get('teaser') or ''}" for i in b.mitglieder]
        code = code_pruefung(karte, alle_texte)
        urteil, anbieter, modell = None, None, None
        if pruefungen < jb["max_pruefungen"]:
            qs = karte["_quellen_ki"] or quellen_auswahl(st, b, 8)
            try:
                urteil, modell = ki.frage("pruefen", pruef_anfrage(karte, qs), pruefe_antwort)
                anbieter = kc["modelle"].get(modell, {}).get("anbieter", modell)
                pruefungen += 1
            except KeinModellFrei as e:
                fehler.append(f"pruefen: {e}")
        karte["pruefung"] = anwenden(karte, code, urteil, anbieter, modell)

    # Bilder (neue Karten und Karten, die noch keins haben)
    auftraege, fuer = [], []
    for n, karte in karten_voll.items():
        if karte.get("bild") and not karte.get("_neu_geschrieben"):
            continue
        b = aktiv[n]
        ms = sorted((st.meldungen[i] for i in b.mitglieder), key=lambda m: m.teaser.get("position_im_feed") or 99)
        ki_info = karte.get("_ki") or {}
        auftraege.append({
            "feed_bilder": [(m.teaser["bild"], m.teaser.get("url")) for m in ms if m.teaser.get("bild")],
            "artikel": [m.teaser["url"] for m in ms if m.teaser.get("url") and not m.teaser.get("bezahlschranke")
                        and m.quelle.id != "google_news"],
            "begriff": ki_info.get("bildbegriff"), "alt": ki_info.get("bild_alt") or karte.get("titel", ""),
            "seiten_laden": True,
        })
        fuer.append(n)
    for n, bild in zip(fuer, waehle_bilder(auftraege)):
        karten_voll[n]["bild"] = bild

    # Kurz & knapp: je 5 Zeilen eine Karte, in Reihenfolge der Wichtigkeit
    eintraege = []
    for n, b in enumerate(aktiv):
        if plan[n] == "kurz_behalten":
            vorher = alt[zuordnung[n]]
            text = vorher.get("zeile") or vorher["karte"]["satz"]
        elif plan[n] == "kurz_neu":
            text = zeilen[n]
        else:
            continue
        sid = zuordnung.get(n) or neue_story_id(st, b, jetzt)
        url = next((st.meldungen[i].teaser.get("url") for i in b.mitglieder), None)
        eintraege.append((n, sid, {"thema": b.thema, "text": text, "url": url}))
    kurz_karten = []
    for nr, portion in enumerate(in_portionen(eintraege, 5), start=1):
        erste = aktiv[portion[0][0]]
        kurz_karten.append({"id": f"kurz-{ausgabe}-{nr}", "format": "kurz", "thema": erste.thema, "region": erste.region,
                            "titel": "Kurz & knapp", "labels": [], "quellen": [], "lager_mix": {},
                            "punktzahl": round(erste.wichtigkeit, 2), "meldungen": [e[2] for e in portion],
                            "story_ids": [e[1] for e in portion]})

    # Reihenfolge: volle Karten nach Wichtigkeit, nach jeder 5. eine Kurz-Karte
    voll_sortiert = [karten_voll[n] for n in sorted(karten_voll)]
    karten = []
    for i, k in enumerate(voll_sortiert, start=1):
        karten.append(k)
        if i % 5 == 0 and kurz_karten:
            karten.append(kurz_karten.pop(0))
    karten += kurz_karten

    # Gedächtnis aktualisieren
    neue_stories: dict[str, dict] = {}
    for n, b in enumerate(aktiv):
        sid = zuordnung.get(n) or (karten_voll[n]["story_id"] if n in karten_voll else neue_story_id(st, b, jetzt))
        vorher = alt.get(sid, {})
        eintrag = {
            "story_id": sid, "status": "aktiv", "thema": b.thema, "unterthema": b.unterthema,
            "teaser_ids": sorted(set(vorher.get("teaser_ids", [])) | set(echte_ids(st, b))),
            "google_schluessel": google_schluessel(b) if nur_google(b) else None,
            "erste_meldung": vorher.get("erste_meldung") or iso(min(st.meldungen[i].zeit for i in b.mitglieder)),
            "letzte_meldung": iso(max(st.meldungen[i].zeit for i in b.mitglieder)),
            "format": b.format, "letzte_ausgabe": ausgabe, "punkte": {**b.teile, **b.faktoren, "gesamt": b.wichtigkeit},
            "verlauf": vorher.get("verlauf", []),
        }
        if n in karten_voll:
            k = karten_voll[n]
            # Quellen und Begründung werden bei jedem Bau neu berechnet und müssen nicht ins Gedächtnis
            eintrag["karte"] = {f: v for f, v in k.items() if not f.startswith("_") and f not in ("quellen", "begruendung")}
            if k.get("_neu_geschrieben"):
                eintrag["verlauf"] = eintrag["verlauf"] + [{"zeit": iso(jetzt), "version": k["version"],
                                                            "aktion": "neu" if plan[n] == "neu" else k.get("_aktion")}]
        else:
            eintrag["karte"] = vorher.get("karte")
            eintrag["zeile"] = zeilen.get(n) or vorher.get("zeile")
        neue_stories[sid] = eintrag
    grenze = iso(jetzt - timedelta(days=7))
    for sid, s in alt.items():  # beendete Geschichten noch 7 Tage aufheben (Updates, Archiv)
        if sid not in neue_stories and s.get("letzte_meldung", "") >= grenze:
            neue_stories[sid] = {**s, "status": "beendet"}

    themen_ausgabe = {k: {"name": v["name"], "farbe": v["farbe"]} for k, v in themen.items()}
    ergebnis = {
        "hinweis": "Testkarten. Der Bias-Algorithmus (PLAN.md 8) ist noch nicht vom Nutzer freigegeben.",
        "ausgabe": ausgabe, "ausgabe_label": ausgabe_label, "erzeugt": iso(jetzt),
        "stand_text": f"Das war die {ausgabe_label}.",
        "themen": themen_ausgabe,
        "karten": [{f: v for f, v in k.items() if not f.startswith("_")} for k in karten],
    }
    zaehle = lambda wert: sum(1 for p in plan.values() if p == wert)  # noqa: E731
    pruef = [k["pruefung"]["ergebnis"] for k in karten_voll.values() if k.get("_neu_geschrieben") and k.get("pruefung")]
    metriken = {
        "ausgabe": ausgabe, "zeit": iso(jetzt), "dauer_s": round(time.monotonic() - start),
        "geschichten": len(aktiv), "karten_voll": len(karten_voll), "kurz_zeilen": len(eintraege),
        "plan": {p: zaehle(p) for p in ("neu", "update", "behalten", "kurz_neu", "kurz_behalten")},
        "pruefung": {e: pruef.count(e) for e in ("ok", "korrigiert", "warnung", "ungeprueft")},
        "bilder": {"verlinkt": sum(1 for k in karten_voll.values() if (k.get("bild") or {}).get("art") == "verlinkt"),
                   "frei": sum(1 for k in karten_voll.values() if (k.get("bild") or {}).get("art") == "frei"),
                   "keins": sum(1 for k in karten_voll.values() if not k.get("bild"))},
        # beobachten (Wunsch des Nutzers 01.11.2026): wie oft ordnet der Mitreden-Satz selbst ein, wie oft fliegt er raus
        "mitreden": {"einordnung": sum(1 for k in karten_voll.values() if k.get("_neu_geschrieben") and k.get("mitreden_art") == "einordnung"),
                     "fakt": sum(1 for k in karten_voll.values() if k.get("_neu_geschrieben") and k.get("mitreden_art") == "fakt"),
                     "gestrichen": sum(1 for k in karten_voll.values() if k.get("_neu_geschrieben") and not k.get("mitreden"))},
        "ki": ki.protokoll, "fehler": fehler,
    }

    # Schreiben
    ordner = daten / "testkarten"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"{ausgabe}.json").write_text(json.dumps(ergebnis, ensure_ascii=False, separators=(",", ":")) + "\n",
                                           encoding="utf-8")
    (ordner / "pruefansicht.html").write_text(als_html(ergebnis, karten_voll, metriken), encoding="utf-8")
    for datei in ordner.glob("*.json"):  # Testausgaben 3 Tage aufheben
        if datei.stem < (jetzt - timedelta(days=3)).strftime("%Y-%m-%d"):
            datei.unlink()
    stories_datei.parent.mkdir(parents=True, exist_ok=True)
    zeilen_json = ",\n".join(json.dumps(s, ensure_ascii=False, separators=(",", ":")) for s in neue_stories.values())
    stories_datei.write_text(f'{{"stand": "{iso(jetzt)}", "stories": [\n{zeilen_json}\n]}}\n', encoding="utf-8")
    (daten / "metriken").mkdir(parents=True, exist_ok=True)
    (daten / "metriken" / f"bau-{ausgabe}.json").write_text(json.dumps(metriken, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    ki.zaehler.speichere()
    return metriken


def main() -> int:
    parser = argparse.ArgumentParser(description="Mitreden: Karten bauen")
    parser.add_argument("--daten", type=Path, required=True, help="Checkout des Branchs rohdaten")
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--jetzt", help="Zeitpunkt überschreiben, z. B. 2026-10-31T06:30:00Z")
    args = parser.parse_args()
    jetzt = datetime.fromisoformat(args.jetzt.replace("Z", "+00:00")) if args.jetzt else jetzt_utc()
    drucke(bauen(args.daten, args.config, jetzt.astimezone(timezone.utc)))
    return 0


def drucke(m: dict) -> None:
    print(f"Bau {m['ausgabe']}: {m['geschichten']} Geschichten → {m['karten_voll']} Karten, {m['kurz_zeilen']} Kurzmeldungen "
          f"in {m['dauer_s']} s")
    print("  Plan: " + ", ".join(f"{k} {v}" for k, v in m["plan"].items()))
    print("  Prüfung: " + ", ".join(f"{k} {v}" for k, v in m["pruefung"].items()))
    print("  Bilder: " + ", ".join(f"{k} {v}" for k, v in m["bilder"].items()))
    print("  Mitreden-Satz: " + ", ".join(f"{k} {v}" for k, v in m["mitreden"].items()))
    anfragen: dict[str, int] = {}
    for p in m["ki"]:
        anfragen[p["modell"]] = anfragen.get(p["modell"], 0) + 1
    print("  KI-Anfragen: " + (", ".join(f"{k} {v}" for k, v in anfragen.items()) or "keine"))
    for f in m["fehler"]:
        print(f"  ⚠ {f}")


if __name__ == "__main__":
    sys.exit(main())
