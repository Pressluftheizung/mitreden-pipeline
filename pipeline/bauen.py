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

from .bilder import waehle_bilder, wiki_abgleich
from .daten import ergebnis_karten, lade_ergebnisse, lade_zahlen, zahlen_karte
from .volltext import lies_volltext
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
             "titel": m.titel, "text": (m.teaser.get("teaser") or "")[:500], "url": m.teaser.get("url"),
             # Volltext (Feedback 06.11.2026): jede Quelle, deren Artikel nachweislich frei ist (Feed ohne Bezahlschranke
             # oder als frei markiert), nicht nur die alte Liste volltext_erlaubt
             "volltext_ok": (m.quelle.volltext_erlaubt or m.teaser.get("bezahlschranke") is False)
             and m.teaser.get("bezahlschranke") is not True
             and "news.google.com" not in (m.teaser.get("url") or ""),
             "neutral": m.quelle.art in FAKTENQUELLEN or m.quelle.lager in {"mitte", "international"}}
            for n, m in enumerate(gewaehlt, start=1)]


VOLLTEXT_VERSUCHE: list[str] = []  # je Versuch: Ergebnis, für die Kennzahlen des Laufs


def volltext_reihenfolge(quellen: list[dict]) -> list[dict]:
    """Welche Artikel zuerst ganz gelesen werden: erst neutrale (Agentur, öffentlich-rechtlich, Mitte), danach
    abwechselnd links und rechts, damit die Tiefe nicht aus einem Lager kommt.

    ⚠ Teil des Bias-Algorithmus (PLAN.md 8, B3), bis zum Review nur Testbetrieb.
    """
    frei = [q for q in quellen if q.get("volltext_ok") and q.get("url")]
    neutral = [q for q in frei if q.get("neutral")]
    links = [q for q in frei if not q.get("neutral") and q.get("lager") in ("links", "mitte_links")]
    rechts = [q for q in frei if not q.get("neutral") and q.get("lager") in ("rechts", "mitte_rechts")]
    seiten = [q for paar in zip(links, rechts) for q in paar]
    rest = [q for q in links + rechts if q not in seiten]
    return neutral + seiten + rest


def lade_volltexte(quellen: list[dict], anzahl: int = 2) -> None:
    """Volltext für bis zu `anzahl` frei zugängliche Quellen (nur im Arbeitsspeicher, nie gespeichert).
    Klappt ein Artikel nicht (nicht lesbar, nur Kurzmeldung), wird der nächste versucht."""
    kandidaten = volltext_reihenfolge(quellen)
    if not kandidaten:
        VOLLTEXT_VERSUCHE.append("keine freie Quelle")
    geladen = 0
    for q in kandidaten[:anzahl + 2]:  # höchstens zwei Fehlversuche
        if geladen >= anzahl:
            break
        text = lies_volltext(q["url"])
        VOLLTEXT_VERSUCHE.append("ok" if text and len(text) > len(q["text"]) + 300 else ("zu kurz" if text else "nicht lesbar"))
        if text and len(text) > len(q["text"]) + 300:  # nur wenn es wirklich mehr ist als der Anrisstext
            q["volltext"] = text
            geladen += 1


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
    if not isinstance(antwort, dict) or not isinstance(antwort.get("karten"), list) or not antwort["karten"]:
        raise ValueError("karten fehlt")
    for k in antwort["karten"]:
        for englisch, deutsch in ENGLISCH.items():
            if englisch in k and deutsch not in k:
                k[deutsch] = k.pop(englisch)
        if k.get("aktion") == "nichts_neues":
            continue
        if not (isinstance(k.get("titel"), str) and isinstance(k.get("punkte"), list) and k.get("satz")):
            raise ValueError(f"Karte unvollständig: {str(k)[:80]}")


def kapitel_bereinigen(roh) -> list[dict]:
    """Kapitel einer Sammelkarte (Themen-Stapel): höchstens 4, je Titel und 1–5 Punkte. Unbrauchbares fällt weg."""
    kapitel = []
    for k in roh if isinstance(roh, list) else []:
        if not isinstance(k, dict) or not isinstance(k.get("titel"), str) or not isinstance(k.get("punkte"), list):
            continue
        punkte = [p.strip() for p in k["punkte"] if isinstance(p, str) and p.strip()][:5]
        if k["titel"].strip() and punkte:
            kapitel.append({"titel": k["titel"].strip()[:80], "punkte": punkte})
    return kapitel[:4]


def text_liste(roh, anzahl: int) -> list[str]:
    """Liste kurzer Texte (Vorgeschichte, Fakten): nur nicht leere Zeichenketten, höchstens anzahl."""
    return [x.strip()[:400] for x in roh if isinstance(x, str) and x.strip()][:anzahl] if isinstance(roh, list) else []


def zusammenhang_bereinigen(roh) -> list[dict]:
    """Verbindungen {text, thema (t5 oder None)} bzw. schon aufgelöst {text, karte}: höchstens 2."""
    liste = []
    for z in roh if isinstance(roh, list) else []:
        if isinstance(z, str):
            z = {"text": z}
        if isinstance(z, dict) and isinstance(z.get("text"), str) and z["text"].strip():
            eintrag = {"text": z["text"].strip()[:400]}
            for feld in ("thema", "karte"):
                if isinstance(z.get(feld), str) and z[feld].strip():
                    eintrag[feld] = z[feld].strip()
            liste.append(eintrag)
    return liste[:2]


def verweise_aufloesen(zusammenhang: list[dict], selbst: int, karten_voll: dict[int, dict]) -> list[dict]:
    """„thema“: "t5" → „karte“: ID der fünften Geschichte, wenn sie eine eigene Karte hat (nicht sich selbst)."""
    neu = []
    for z in zusammenhang:
        treffer = re.fullmatch(r"t(\d+)", str(z.get("thema") or "").strip())
        eintrag = {k: v for k, v in z.items() if k != "thema"}
        if treffer:
            ziel = karten_voll.get(int(treffer.group(1)) - 1)
            if ziel is not None and int(treffer.group(1)) - 1 != selbst:
                eintrag["karte"] = ziel["id"]
                eintrag["titel"] = ziel.get("titel") or ""
        neu.append(eintrag)
    return neu


def fragen_bereinigen(roh) -> list[dict]:
    """„Du fragst dich vielleicht“ (Feedback 06.11.2026): höchstens 3 {frage, antwort}."""
    liste = []
    for f in roh if isinstance(roh, list) else []:
        if isinstance(f, dict) and isinstance(f.get("frage"), str) and isinstance(f.get("antwort"), str):
            frage, antwort = f["frage"].strip()[:200], f["antwort"].strip()[:500]
            if frage and antwort:
                liste.append({"frage": frage, "antwort": antwort})
    return liste[:3]


ERKLAERE_ARTEN = {"person", "begriff", "ort", "organisation"}


def erklaere_bereinigen(roh) -> list[dict]:
    """Antippbare Wörter fürs Pop-up (Feedback 06.11.2026): höchstens 12 {wort, art, text, wiki?}, jedes Wort einmal."""
    liste, gesehen = [], set()
    for e in roh if isinstance(roh, list) else []:
        if not (isinstance(e, dict) and isinstance(e.get("wort"), str) and isinstance(e.get("text"), str)):
            continue
        wort, text = e["wort"].strip()[:60], e["text"].strip()[:400]
        if len(wort) < 2 or not text or wort.lower() in gesehen:
            continue
        gesehen.add(wort.lower())
        eintrag = {"wort": wort, "art": e.get("art") if e.get("art") in ERKLAERE_ARTEN else "begriff", "text": text}
        if isinstance(e.get("wiki"), dict) and str(e["wiki"].get("url", "")).startswith("https://"):
            eintrag["wiki"] = e["wiki"]  # schon nachgeschlagen (bisherige Karte)
        elif isinstance(e.get("wiki"), str) and e["wiki"].strip():
            eintrag["_wiki_titel"] = e["wiki"].strip()[:120]
        liste.append(eintrag)
    return liste[:12]


def wiki_ergaenzen(karten: list[dict]) -> None:
    """Wikipedia-Titel der Pop-up-Wörter nachschlagen: Link und Kurzbeschreibung, sonst ohne Link."""
    titel = [e["_wiki_titel"] for k in karten for e in k.get("erklaere") or [] if e.get("_wiki_titel")]
    if not titel:
        return
    gefunden = wiki_abgleich(titel)
    for k in karten:
        for e in k.get("erklaere") or []:
            t = e.pop("_wiki_titel", None)
            if t and gefunden.get(t):
                e["wiki"] = gefunden[t]


STIMMEN_ARTEN = {"umfrage", "ausland", "fachleute", "politik", "betroffene"}


# Sammelabsender ohne Namen (Feedback 06.11.2026: „wer genau?“) fliegen raus
VAGER_ABSENDER = re.compile(r"^(viele |einige |manche |andere |zahlreiche |die |internationale |führende )?"
                            r"[\wäöüß-]*(experten|expertinnen|fachleute|beobachter|kritiker|analysten|ökonomen|kenner|"
                            r"insider|kreise|stimmen|befürworter|gegner|verbündete|investoren|anleger)$", re.I)


# Feedback 07.11.2026: Das Modell rutscht gelegentlich in chinesische Schriftzeichen („Präsidentschafts美国的“)
FREMDSCHRIFT = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]+")
# Kurz-Zeilen ohne Nachricht („Die Schlagzeilen behandeln verschiedene, nicht zusammenhängende Themen …“)
LEERE_ZEILE = re.compile(r"Schlagzeilen|nicht zusammenhängend|verschiedene,? .{0,30}Themen|keine gemeinsame", re.I)


def ohne_fremdschrift(wert):
    """Entfernt CJK-Zeichen aus allen Texten einer KI-Antwort (die App ist deutsch)."""
    if isinstance(wert, str):
        return re.sub(r"  +", " ", FREMDSCHRIFT.sub("", wert)) if FREMDSCHRIFT.search(wert) else wert
    if isinstance(wert, list):
        return [ohne_fremdschrift(x) for x in wert]
    if isinstance(wert, dict):
        return {k: ohne_fremdschrift(v) for k, v in wert.items()}
    return wert


def stimmen_bereinigen(roh) -> list[dict]:
    """„Was sagen andere“ (Feedback 06.11.2026): höchstens 4 {art, wer, text}, immer mit Absender."""
    liste = []
    for s in roh if isinstance(roh, list) else []:
        if isinstance(s, dict) and isinstance(s.get("wer"), str) and isinstance(s.get("text"), str):
            wer, text = s["wer"].strip()[:120], s["text"].strip()[:400]
            art = s.get("art") if s.get("art") in STIMMEN_ARTEN else "politik"
            if wer and text and not VAGER_ABSENDER.match(wer):
                liste.append({"art": art, "wer": wer, "text": text})
    return liste[:4]


def grundwissen_bereinigen(roh) -> list[dict]:
    """Grundbegriffe für die „Kurz erklärt“-Karte (Feedback 05.11.2026): höchstens 3, je Begriff und Text."""
    liste = []
    for g in roh if isinstance(roh, list) else []:
        if isinstance(g, dict) and isinstance(g.get("begriff"), str) and isinstance(g.get("text"), str):
            begriff, text = g["begriff"].strip()[:60], g["text"].strip()[:400]
            if begriff and text:
                liste.append({"begriff": begriff, "text": text})
    return liste[:3]


def pruefe_zeilen(antwort: dict) -> None:
    if not isinstance(antwort, dict) or not isinstance(antwort.get("zeilen"), list) or not all(isinstance(z.get("text"), str) for z in antwort["zeilen"]):
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

    st = st or berechne(daten, config, jetzt, ki=ki)
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
        # Neues Großthema (oder ein anderes als bisher): Sammelkarte neu schreiben statt die alte Einzelkarte fortzuführen
        neues_thema = bool(b.sammel) and (karte_vorher or {}).get("sammel") != b.sammel["name"]
        if not voll:
            if vorher.get("zeile") or (karte_vorher and karte_vorher.get("satz")):
                plan[n] = "kurz_behalten"
            else:
                plan[n] = "kurz_neu"
                kurz_neu.append(n)
        elif karte_vorher and karte_vorher.get("titel") and not neues_thema:
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

    # Schlagzeilen der wichtigsten Geschichten: damit die KI im Mitreden-Satz Verbindungen herstellen kann
    # Mit Kennung (t1 …): die KI kann im „Zusammenhang“ auf eine andere Karte verweisen, die App springt dorthin
    andere_themen = [{"nr": f"t{i + 1}", "titel": b.titel} for i, b in enumerate(aktiv[:30])]

    def schreibe(aufgabe: str, auswahl: list[int], groesse: int) -> None:
        for portion in in_portionen(auswahl, groesse):
            eingabe = []
            for n in portion:
                b = aktiv[n]
                # Sammelkarte: mehr Quellen, damit die Teile des Großthemas belegt sind (Auswahl-Regeln unverändert)
                anzahl = 14 if b.sammel else 8 if b.format == "top" else 6
                qs = quellen_auswahl(st, b, anzahl, mit_meinung=b.format == "top")
                # Volltexte für alle großen Karten (Feedback 06.11.2026: Teaser allein sind zu dünn für Tiefe)
                lade_volltexte(qs, 3 if b.format == "top" or b.sammel else 2)
                quellen_ki[n] = qs
                if aufgabe == "update":  # nur die neuen Meldungen schicken
                    neue_urls = {st.meldungen[i].teaser.get("url") for i in b.mitglieder
                                 if st.meldungen[i].teaser["id"] not in set(alt[zuordnung[n]].get("teaser_ids", []))}
                    qs = [q for q in qs if q.get("url") in neue_urls] or qs
                felder = ("id", "redaktion", "lager", "art", "titel", "text", "volltext")
                eintrag = {"story": f"s{n}", "format": b.format, "thema": b.thema, "region": b.region,
                           "quellen": [{k: q[k] for k in felder if q.get(k)} for q in qs]}
                if b.sammel:  # Sammelkarte: Schlagzeilen der Teilgeschichten, damit alle Teile vorkommen
                    eintrag["sammelthema"] = {"name": b.sammel["name"], "teile": b.sammel["teile"][:50]}
                if aufgabe == "update":
                    k = alt[zuordnung[n]]["karte"]
                    eintrag["bisherige_karte"] = {f: k.get(f) for f in ("titel", "satz", "punkte", "mitreden", "faktenbox",
                                                                          "ebenen", "erklaerung", "kapitel", "grundwissen",
                                                                          "vorgeschichte", "zusammenhang", "fakten",
                                                                          "fragen", "stimmen", "erklaere") if k.get(f)}
                eingabe.append(eintrag)
            daten = {"geschichten": eingabe, "andere_themen": andere_themen}
            try:
                antwort, modell = ki.frage(aufgabe, json.dumps(daten, ensure_ascii=False), pruefe_karten)
            except KeinModellFrei as e:
                fehler.append(f"{aufgabe}: {e}")
                continue
            for k in antwort["karten"]:
                treffer = re.fullmatch(r"s(\d+)", str(k.get("story", "")))
                if treffer and int(treffer.group(1)) in portion:
                    k["_modell"] = modell
                    texte[int(treffer.group(1))] = ohne_fremdschrift(k)

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
                    text = ohne_fremdschrift(z["text"]).strip()
                    zeilen[int(treffer.group(1))] = "" if LEERE_ZEILE.search(text) else text  # leer: Zeile fällt weg
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
            for feld in ("titel", "satz", "punkte", "mitreden", "mitreden_art", "faktenbox", "ebenen", "erklaerung",
                         "kapitel", "grundwissen", "nebenbei", "vorgeschichte", "zusammenhang", "fakten",
                         "fragen", "stimmen", "erklaere"):
                if feld in t:
                    karte[feld] = t[feld]
            if plan[n] == "update":  # nicht mitgeliefertes Hintergrundwissen könnte dem neuen Stand widersprechen
                if "ebenen" not in t:
                    karte["ebenen"] = [e for e in karte.get("ebenen") or [] if e.get("typ") != "hintergrund"]
                if "erklaerung" not in t:
                    karte["erklaerung"] = None
                if "kapitel" not in t and not b.sammel:
                    karte["kapitel"] = []
            karte["punkte"] = (karte.get("punkte") or [])[:3]
            karte["kapitel"] = kapitel_bereinigen(karte.get("kapitel"))
            karte["grundwissen"] = grundwissen_bereinigen(karte.get("grundwissen"))
            karte["vorgeschichte"] = text_liste(karte.get("vorgeschichte"), 3)
            karte["zusammenhang"] = zusammenhang_bereinigen(karte.get("zusammenhang"))
            karte["fakten"] = text_liste(karte.get("fakten"), 3)
            karte["fragen"] = fragen_bereinigen(karte.get("fragen"))
            karte["stimmen"] = stimmen_bereinigen(karte.get("stimmen"))
            karte["erklaere"] = erklaere_bereinigen(karte.get("erklaere"))
            nb = karte.get("nebenbei")  # früheres Einzelfeld: wird zum ersten Fakt
            if isinstance(nb, str) and nb.strip() and not karte["fakten"]:
                karte["fakten"] = [nb.strip()[:400]]
            karte["nebenbei"] = None
            if karte["kapitel"]:  # Themen-Stapel ersetzt die frühere Ebene „Im Überblick“
                karte["ebenen"] = [e for e in karte.get("ebenen") or [] if e.get("titel") != "Im Überblick"]
            karte["version"] = vorher.get("version", 0) + 1
            karte["aktualisiert"] = iso(jetzt)
            karte["aenderung"] = t.get("aenderung") if plan[n] == "update" else None
            # Kleines Update (weitere Reaktion, Wiederholung): die App zeigt die Karte nicht noch einmal (Feedback 05.11.2026)
            karte["neuigkeit"] = ("klein" if plan[n] == "update" and t.get("neuigkeit") == "klein"
                                  and t.get("aktion") != "korrigieren" else "gross")
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
            "sammel": (b.sammel or {}).get("name"),
        })
        karte.setdefault("version", 1)
        karte.setdefault("aktualisiert", iso(jetzt))
        karte.setdefault("aenderung", None)
        karte.setdefault("ebenen", [])
        karte.setdefault("erklaerung", None)
        karte.setdefault("kapitel", [])
        karten_voll[n] = karte

    # Zusammenhang: Kennung t5 → Karten-ID, wenn die andere Geschichte in dieser Ausgabe eine eigene Karte hat
    for n, karte in karten_voll.items():
        if karte.get("_neu_geschrieben"):
            karte["zusammenhang"] = verweise_aufloesen(karte.get("zusammenhang") or [], n, karten_voll)
    # Pop-up-Wörter: Wikipedia-Link und Kurzbeschreibung (kostenlos, parallel)
    wiki_ergaenzen([k for k in karten_voll.values() if k.get("_neu_geschrieben")])

    # Prüfen (nur neu geschriebene Karten; die übrigen behalten ihre Prüfung)
    pruefungen = 0
    for n, karte in karten_voll.items():
        if not karte.get("_neu_geschrieben"):
            continue
        b = aktiv[n]
        alle_texte = [f"{st.meldungen[i].titel} {st.meldungen[i].teaser.get('teaser') or ''}" for i in b.mitglieder]
        alle_texte += [q["volltext"] for q in karte["_quellen_ki"] if q.get("volltext")]
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
        if not text or LEERE_ZEILE.search(text):
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

    # Datenkarten: Zahlen in jeder Ausgabe (an dritter Stelle), neue Fußballergebnisse (an sechster Stelle)
    daten_datei = daten / "stories" / "datenkarten.json"
    gezeigt = json.loads(daten_datei.read_text(encoding="utf-8")) if daten_datei.exists() else {}
    schlagzeilen = [b.titel for b in aktiv if b.thema == "geld"][:25] + andere_themen[:15]
    zahlen = zahlen_karte(lade_zahlen(), schlagzeilen, ki, ausgabe)
    # Feste Karten-ID je Spieltag: die App zeigt sie einmal und bei neuen Spielen als „Aktualisiert“ (höhere Version)
    ergebnisse = ergebnis_karten(lade_ergebnisse(jetzt), ausgabe, set())
    for k in ergebnisse:
        alt_k = gezeigt.setdefault("karten", {}).get(k["id"], {"version": 0, "spiele": []})
        neu = sorted(k["_gezeigt"])
        k["version"] = alt_k["version"] + (neu != alt_k["spiele"])
        gezeigt["karten"][k["id"]] = {"version": k["version"], "spiele": neu, "zeit": iso(jetzt)}
    karten[5:5] = ergebnisse
    if zahlen:
        karten.insert(2, zahlen)
    grenze_daten = iso(jetzt - timedelta(days=7))
    gezeigt = {"karten": {k: v for k, v in gezeigt.get("karten", {}).items() if v["zeit"] >= grenze_daten}}

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
        "mitreden": {art: sum(1 for k in karten_voll.values() if k.get("_neu_geschrieben") and k.get("mitreden")
                              and k.get("mitreden_art") == art) for art in ("fakt", "wissen", "spekulation")}
                    | {"gestrichen": sum(1 for k in karten_voll.values() if k.get("_neu_geschrieben") and not k.get("mitreden"))},
        "datenkarten": {"zahlen": bool(zahlen), "ergebnisse": len(ergebnisse)},
        "volltext_versuche": {e: VOLLTEXT_VERSUCHE.count(e) for e in set(VOLLTEXT_VERSUCHE)},
        "volltexte": sum(1 for k in karten_voll.values() for q in k.get("_quellen_ki") or [] if q.get("volltext")),
        "dubletten": st.dubletten,
        "themen": st.themen,
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
    daten_datei.write_text(json.dumps(gezeigt, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
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
    print("  Mitreden-Satz: " + ", ".join(f"{k} {v}" for k, v in m["mitreden"].items()) + f" · Volltexte {m['volltexte']}")
    d = m.get("dubletten") or {}
    if d.get("aufteilen"):
        a, z = d["aufteilen"], d["zusammenlegen"]
        print(f"  Dubletten (KI, {d['anfragen']} Anfragen): {a['aufgeteilt']} Geschichten aufgeteilt "
              f"({a['abgetrennt']} Meldungen abgetrennt), {z['gleich']} von {z['kandidaten']} Paaren zusammengelegt "
              f"({z['aus_speicher']} aus dem Speicher)")
    for f in d.get("fehler", []):
        print(f"  ⚠ Dubletten: {f}")
    t = m.get("themen") or {}
    if t.get("gefragt"):
        g = ", ".join(f"„{x['name']}“ ({x['teile']})" for x in t.get("grossthemen", [])) or "keine"
        print(f"  Großthemen: {g} · Gesprächswert " + ", ".join(f"{k}: {v}" for k, v in t.get("gespraech", {}).items()))
    for f in t.get("fehler", []):
        print(f"  ⚠ Großthemen: {f}")
    anfragen: dict[str, int] = {}
    for p in m["ki"]:
        anfragen[p["modell"]] = anfragen.get(p["modell"], 0) + 1
    print("  KI-Anfragen: " + (", ".join(f"{k} {v}" for k, v in anfragen.items()) or "keine"))
    for f in m["fehler"]:
        print(f"  ⚠ {f}")


if __name__ == "__main__":
    sys.exit(main())
