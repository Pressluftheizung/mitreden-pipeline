"""Allgemeinwissen (PLAN.md 18): Wissenskarten aus dem Lehrplan, einmal gebaut und dauerhaft gespeichert.

Ablauf je Lauf (läuft nach jeder Ausgabe mit, ein paar Themen pro Lauf):
  1. Nächste offene Themen aus config/lehrplan.json (erst Stufe 1, dann 2, dann 3).
  2. Quellen: Text des deutschen Wikipedia-Artikels, als zweite Quelle der englische Artikel zum selben Thema
     (andere Autoren, Wunsch des Nutzers 09.11.2026), dazu erklärende Bilder aus dem Artikel (Schemata,
     Animationen, Karten; frei bei Wikimedia).
  3. KI schreibt die Karte: Satz, Punkte, Mini-Fakten und 2–3 Fragen (Auswahl, Schätzen, Wahr/Falsch).
  4. Programm prüft: Schätzwerte müssen als Zahl in der Quelle stehen, Antworten im gültigen Bereich.
  5. Zweite KI (andere Firma) prüft jede Aussage und Frage gegen die Quelle; ohne Prüfung wird nichts gespeichert.
  6. Speichern in daten/wissen/wissen.json (privat). Fertige Themen werden nie neu gebaut.
"""

import json
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

from .bauen import erklaere_bereinigen, wiki_ergaenzen, wort_im_text
from .daten import _json
from .ki import KeinModellFrei

WIKI = "https://de.wikipedia.org/w/api.php?"
WIKIMEDIA = re.compile(r"https://(upload|thumb)\.wikimedia\.org/")
QUELLE_ZEICHEN = 9000
QUELLE_EN_ZEICHEN = 6000
PRUEF_ZEICHEN = 4500
PRUEF_EN_ZEICHEN = 3000
# Dateien, die nichts erklären (Symbole der Schwesterprojekte, Flaggen, Logos)
KEIN_ERKLAERBILD = re.compile(r"commons-logo|wiki(books|quote|source|versity|data|species|voyage|tionary|news)|wiktfavicon|"
                              r"icon|flag|flagge|disambig|symbol_|pictogram|stub|logo|ambox|question_book|edit-clear|"
                              r"wappen|coat.of.arms|[ _]coa[ _.]|briefmarke|stamp|signatur|signature|unterschrift", re.I)


# ---------- Lehrplan ----------

def lehrplan(config: Path) -> list[dict]:
    """Alle Themen in Bau-Reihenfolge: Stufe, dann Modul, dann Position im Modul."""
    plan = json.loads((config / "lehrplan.json").read_text(encoding="utf-8"))
    themen = []
    for m_nr, m in enumerate(plan["module"]):
        for t_nr, t in enumerate(m["themen"]):
            themen.append({**t, "id": f"{m['id']}.{t['id']}", "modul": m["id"], "modul_titel": m["titel"],
                           "bereich": t.get("bereich") or m["bereich"],
                           "braucht": [b if "." in b else f"{m['id']}.{b}" for b in t.get("braucht", [])],
                           "vorrang": bool(m.get("vorrang")),
                           "_rang": (t["stufe"], m_nr, t_nr)})
    return sorted(themen, key=lambda t: t["_rang"])


# Bauversion der Regeln: Karten mit kleinerer Version werden neu gebaut (vor neuen Themen).
# 2 = 10.11.2026: mehr Bilder (Seitenbild als Kandidat), Überschriften ohne „X: Y“-Schema, Mini-Fakten nur zum Thema.
# 3 = 10.11.2026: Pop-up-Wörter (Begriffe wie „Bundesversammlung“ oder „Parlament“ antippbar).
# 4 = 10.11.2026: Abschnitte für Tiefe (jeder genannte Teil wird erklärt, mit Beispiel).
BAU = 4


def offene_themen(themen: list[dict], fertig: dict, anzahl: int) -> list[dict]:
    veraltet = [t for t in themen if t["id"] in fertig and fertig[t["id"]].get("bau", 1) < BAU]
    neu = [t for t in themen if t["id"] not in fertig]
    vorrang = [t for t in neu if t.get("vorrang")]  # z. B. Testauswahl zum Anschauen
    return (vorrang + veraltet + [t for t in neu if not t.get("vorrang")])[:anzahl]


# ---------- Quelle ----------

def quelle(titel: str) -> dict | None:
    """Text (ohne Wiki-Markup), Seitenbild und Erklärbilder des deutschen Artikels, dazu der englische Artikel."""
    try:
        d = _json(WIKI + urllib.parse.urlencode({
            "action": "query", "format": "json", "titles": titel, "redirects": 1,
            "prop": "extracts|pageimages|info|images|langlinks", "explaintext": 1, "exsectionformat": "plain",
            "piprop": "thumbnail|name", "pithumbsize": 800, "inprop": "url", "imlimit": 80, "lllang": "en"}))
    except Exception:
        return None
    for s in ((d.get("query") or {}).get("pages") or {}).values():
        text = (s.get("extract") or "").strip()
        if "missing" in s or len(text) < 400:
            return None
        bild = ((s.get("thumbnail") or {}).get("source") or "").split("?")[0]
        en_titel = ((s.get("langlinks") or [{}])[0]).get("*")
        dateien = [i["title"] for i in s.get("images") or [] if not KEIN_ERKLAERBILD.search(i.get("title", ""))]
        # Seitenbild zuerst (oft Karte, Diagramm oder das Gebäude), dann Schemata/Animationen (svg, gif, png) vor Fotos
        seite_bild = s.get("pageimage")
        if seite_bild:
            dateien = [f"Datei:{seite_bild}"] + [d for d in dateien if d.split(":", 1)[-1].replace(" ", "_") != seite_bild]
        dateien = dateien[:1] + sorted(dateien[1:], key=lambda d: 0 if re.search(r"\.(svg|gif|png)$", d, re.I) else 1)
        return {"titel": s.get("title") or titel, "url": s.get("fullurl") or "", "text": text[:QUELLE_ZEICHEN],
                "bild": bild if WIKIMEDIA.match(bild) else None,
                "en": englisch(en_titel) if en_titel else None,
                "bilder": erklaerbilder(dateien[:15])}
    return None


def englisch(titel: str) -> dict | None:
    """Zweite Quelle: der englische Wikipedia-Artikel zum selben Thema (andere Autoren)."""
    try:
        d = _json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "format": "json", "titles": titel, "redirects": 1,
            "prop": "extracts|info", "explaintext": 1, "exsectionformat": "plain", "inprop": "url"}))
    except Exception:
        return None
    for s in ((d.get("query") or {}).get("pages") or {}).values():
        text = (s.get("extract") or "").strip()
        if "missing" in s or len(text) < 300:
            return None
        return {"titel": s.get("title") or titel, "url": s.get("fullurl") or "", "text": text[:QUELLE_EN_ZEICHEN]}
    return None


def erklaerbilder(dateien: list[str]) -> list[dict]:
    """Bilder des Artikels mit Beschreibung (Wikimedia, frei): Kandidaten, aus denen die KI erklärende auswählt."""
    if not dateien:
        return []
    try:
        d = _json(WIKI + urllib.parse.urlencode({
            "action": "query", "format": "json", "titles": "|".join(dateien), "prop": "imageinfo",
            "iiprop": "url|extmetadata", "iiurlwidth": 720, "iiextmetadatafilter": "ImageDescription"}))
    except Exception:
        return []
    bilder = []
    for s in ((d.get("query") or {}).get("pages") or {}).values():
        info = (s.get("imageinfo") or [{}])[0]
        url = (info.get("thumburl") or info.get("url") or "").split("?")[0]
        if not WIKIMEDIA.match(url):
            continue
        roh = ((info.get("extmetadata") or {}).get("ImageDescription") or {}).get("value") or ""
        beschreibung = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", roh)).strip()[:300]
        bilder.append({"datei": s.get("title", "").split(":", 1)[-1], "url": url, "beschreibung": beschreibung,
                       "seite": "https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(s.get("title", "").split(":", 1)[-1].replace(" ", "_"))})
    return bilder


# ---------- Prüfen im Code ----------

def zahlen_en(text: str) -> set[float]:
    """Zahlen eines englischen Textes („8,849“, „2.5“)."""
    werte = set()
    for roh in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        z = roh.rstrip(",")
        if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", z):
            z = z.replace(",", "")
        try:
            werte.add(float(z))
        except ValueError:
            pass
    return werte


def zahlen(text: str) -> set[float]:
    """Alle Zahlen eines deutschen Textes („8.849“, „6.650“, „2,5“, „1949“)."""
    werte = set()
    for roh in re.findall(r"\d[\d.]*(?:,\d+)?", text):
        z = roh.rstrip(".")
        if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", z):  # Tausenderpunkte
            z = z.replace(".", "")
        try:
            werte.add(float(z.replace(",", ".")))
        except ValueError:
            pass
    return werte


def fragen_bereinigen(roh, quelltext: str, quelltext_en: str = "") -> list[dict]:
    """Nur vollständige Fragen; Schätzwerte müssen als Zahl in einer der Quellen stehen (sonst könnte die KI sie erfinden)."""
    in_quelle = zahlen(quelltext) | zahlen_en(quelltext_en)
    fragen = []
    for f in roh if isinstance(roh, list) else []:
        if not isinstance(f, dict) or not isinstance(f.get("frage"), str) or not f["frage"].strip():
            continue
        art, frage = f.get("art"), f["frage"].strip()[:200]
        erklaerung = str(f.get("erklaerung") or "").strip()[:300]
        if art == "auswahl":
            optionen = [str(o).strip()[:80] for o in f.get("optionen") or [] if str(o).strip()]
            richtig = f.get("richtig")
            if 3 <= len(optionen) <= 4 and isinstance(richtig, int) and 0 <= richtig < len(optionen) \
                    and len(set(optionen)) == len(optionen):
                fragen.append({"art": art, "frage": frage, "optionen": optionen, "richtig": richtig, "erklaerung": erklaerung})
        elif art == "schaetzen":
            wert, toleranz = f.get("wert"), f.get("toleranz")
            toleranz_art = f.get("toleranz_art") if f.get("toleranz_art") in ("absolut", "prozent") else "prozent"
            if isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert > 0 and float(wert) in in_quelle:
                if not isinstance(toleranz, (int, float)) or toleranz <= 0:
                    toleranz, toleranz_art = 10, "prozent"
                fragen.append({"art": art, "frage": frage, "wert": wert, "einheit": str(f.get("einheit") or "").strip()[:30],
                               "toleranz": toleranz, "toleranz_art": toleranz_art, "erklaerung": erklaerung})
        elif art == "wahr_falsch":
            if isinstance(f.get("richtig"), bool):
                fragen.append({"art": art, "frage": frage, "richtig": f["richtig"], "erklaerung": erklaerung})
    return fragen[:3]


def karte_bereinigen(roh: dict, thema: dict, q: dict) -> dict | None:
    texte = lambda xs, n, z: [str(x).strip()[:z] for x in xs if str(x).strip()][:n] if isinstance(xs, list) else []
    satz = str(roh.get("satz") or "").strip()[:400]
    punkte = texte(roh.get("punkte"), 6, 300)
    abschnitte = abschnitte_bereinigen(roh.get("abschnitte"))
    fragen = fragen_bereinigen(roh.get("fragen"), q["text"], (q.get("en") or {}).get("text", ""))
    if not satz or len(punkte) < 2 or not fragen:
        return None
    return {
        "id": thema["id"], "modul": thema["modul"], "modul_titel": thema["modul_titel"], "bereich": thema["bereich"],
        "auch": thema.get("auch", []), "stufe": thema["stufe"], "braucht": thema["braucht"],
        "titel": str(roh.get("titel") or thema["titel"]).strip()[:120], "satz": satz, "punkte": punkte,
        "abschnitte": abschnitte,
        "mini": texte(roh.get("mini"), 2, 250), "fragen": fragen,
        "erklaere": [e for e in erklaere_bereinigen(roh.get("erklaere"))
                     if wort_im_text(e["wort"], " ".join([satz, *punkte, *texte(roh.get("mini"), 2, 250),
                                                         *(f["frage"] for f in fragen),
                                                         *(f.get("erklaerung", "") for f in fragen)]))],
        "bilder": bilder_bereinigen(roh.get("bilder"), q.get("bilder") or [], len(punkte)),
        "bild": {"url": q["bild"], "alt": thema["titel"], "art": "frei"} if q.get("bild") else None,
        "quelle": {"name": "Wikipedia", "titel": q["titel"], "url": q["url"]},
        "quelle_2": {"name": "Wikipedia (englisch)", "titel": q["en"]["titel"], "url": q["en"]["url"]} if q.get("en") else None,
    }


def abschnitte_bereinigen(roh) -> list[dict]:
    """Tiefe (Feedback 10.11.2026): 2–4 Abschnitte je {titel, punkte}, z. B. „So funktioniert es“, „Ein Beispiel“."""
    abschnitte = []
    for a in roh if isinstance(roh, list) else []:
        if not isinstance(a, dict) or not isinstance(a.get("titel"), str):
            continue
        punkte = [str(p).strip()[:400] for p in a.get("punkte") or [] if str(p).strip()][:6]
        if a["titel"].strip() and len(punkte) >= 2:
            abschnitte.append({"titel": a["titel"].strip()[:60], "punkte": punkte})
    return abschnitte[:4]


def bilder_bereinigen(roh, kandidaten: list[dict], anzahl_punkte: int) -> list[dict]:
    """Erklärbilder (Wunsch des Nutzers 09.11.2026): höchstens 2, je an einen Punkt gehängt, mit Bildunterschrift."""
    bilder, schon = [], set()
    for b in roh if isinstance(roh, list) else []:
        if not isinstance(b, dict) or not isinstance(b.get("nr"), int):
            continue
        nr, punkt = b["nr"], b.get("punkt")
        if not 1 <= nr <= len(kandidaten) or nr in schon:
            continue
        unterschrift = str(b.get("unterschrift") or "").strip()[:200]
        if not unterschrift:
            continue
        k = kandidaten[nr - 1]
        schon.add(nr)
        bilder.append({"url": k["url"], "seite": k["seite"], "unterschrift": unterschrift,
                       "punkt": punkt if isinstance(punkt, int) and 0 <= punkt < anzahl_punkte else None})
    return bilder[:3]


def pruefung_anwenden(karte: dict, urteil: dict) -> dict | None:
    """Was die Prüf-KI nicht in der Quelle findet, fliegt raus; ohne Fragen oder mit falschem Satz keine Karte."""
    falsche_fragen = {i for i in urteil.get("fragen_falsch") or [] if isinstance(i, int)}
    falsche_punkte = {i for i in urteil.get("punkte_falsch") or [] if isinstance(i, int)}
    falsche_abschnitte = {str(i) for i in urteil.get("abschnitte_falsch") or [] if isinstance(i, (str, int))}
    falsche_mini = {i for i in urteil.get("mini_falsch") or [] if isinstance(i, int)}
    falsche_bilder = {i for i in urteil.get("bilder_falsch") or [] if isinstance(i, int)}
    falsche_woerter = {i for i in urteil.get("erklaere_falsch") or [] if isinstance(i, int)}
    if urteil.get("satz_falsch") is True:
        return None
    karte = {**karte,
             "fragen": [f for i, f in enumerate(karte["fragen"]) if i not in falsche_fragen],
             "punkte": [p for i, p in enumerate(karte["punkte"]) if i not in falsche_punkte],
             "abschnitte": [a for a in ({"titel": a["titel"], "punkte": [p for j, p in enumerate(a["punkte"]) if f"{i}.{j}" not in falsche_abschnitte]}
                                        for i, a in enumerate(karte.get("abschnitte") or [])) if len(a["punkte"]) >= 2],
             "mini": [m for i, m in enumerate(karte["mini"]) if i not in falsche_mini],
             "bilder": [b for i, b in enumerate(karte.get("bilder") or []) if i not in falsche_bilder],
             "erklaere": [e for i, e in enumerate(karte.get("erklaere") or []) if i not in falsche_woerter]}
    return karte if karte["fragen"] and len(karte["punkte"]) >= 2 else None


def pruefe_karten(antwort: dict) -> None:
    if not isinstance(antwort, dict) or not isinstance(antwort.get("karten"), list):
        raise ValueError("karten fehlt")


def pruefe_urteil(antwort: dict) -> None:
    if not isinstance(antwort, dict) or "fragen_falsch" not in antwort:
        raise ValueError("fragen_falsch fehlt")


# ---------- Lauf ----------

def baue(daten: Path, config: Path, ki, jetzt: datetime, anzahl: int, pro_anfrage: int = 2) -> dict:
    """Baut bis zu `anzahl` neue Wissenskarten und hängt sie an die Datenbank an. Gibt Kennzahlen zurück."""
    pfad = daten / "wissen" / "wissen.json"
    db = json.loads(pfad.read_text(encoding="utf-8")) if pfad.exists() else {"karten": {}}
    themen = lehrplan(config)
    offen = offene_themen(themen, db["karten"], anzahl)
    stat = {"versucht": len(offen), "gebaut": 0, "ohne_quelle": 0, "verworfen": 0, "fehler": []}
    for start in range(0, len(offen), pro_anfrage):
        portion = []
        for t in offen[start:start + pro_anfrage]:
            q = quelle(t["wiki"])
            if q:
                portion.append((t, q))
            else:
                stat["ohne_quelle"] += 1
        if not portion:
            continue
        eingabe = {"themen": [{"thema": t["id"], "titel": t["titel"], "stufe": t["stufe"], "modul": t["modul_titel"],
                               "quelle": q["text"], "quelle_en": (q.get("en") or {}).get("text", ""),
                               "bilder": [{"nr": i + 1, "datei": b["datei"], "beschreibung": b["beschreibung"]}
                                          for i, b in enumerate(q.get("bilder") or [])]}
                              for t, q in portion]}
        try:
            antwort, modell = ki.frage("wissen", json.dumps(eingabe, ensure_ascii=False), pruefe_karten)
        except KeinModellFrei as e:
            stat["fehler"].append(f"wissen: {e}")
            break
        roh = {str(k.get("thema")): k for k in antwort["karten"] if isinstance(k, dict)}
        for t, q in portion:
            karte = karte_bereinigen(roh.get(t["id"]) or {}, t, q)
            if not karte:
                stat["verworfen"] += 1
                continue
            pruef = {"quelle": q["text"][:PRUEF_ZEICHEN], "quelle_en": (q.get("en") or {}).get("text", "")[:PRUEF_EN_ZEICHEN],
                     "satz": karte["satz"], "punkte": karte["punkte"], "mini": karte["mini"], "fragen": karte["fragen"],
                     "abschnitte": {f"{i}.{j}": f"{a['titel']}: {p}" for i, a in enumerate(karte["abschnitte"])
                                    for j, p in enumerate(a["punkte"])},
                     "bilder": [b["unterschrift"] for b in karte["bilder"]],
                     "erklaere": [f"{e['wort']}: {e['text']}" for e in karte["erklaere"]]}
            try:
                urteil, pruefer = ki.frage("wissen_pruefen", json.dumps(pruef, ensure_ascii=False), pruefe_urteil)
            except KeinModellFrei as e:  # ohne Prüfung nichts speichern; das Thema kommt im nächsten Lauf wieder
                stat["fehler"].append(f"wissen_pruefen: {e}")
                continue
            karte = pruefung_anwenden(karte, urteil)
            if karte:
                wiki_ergaenzen([karte])  # Pop-ups: Wikipedia-Link, Auszug und Bild
            if not karte:
                stat["verworfen"] += 1
                continue
            alt = db["karten"].get(t["id"]) or {}
            karte.update({"erstellt": jetzt.isoformat(timespec="seconds"), "version": alt.get("version", 0) + 1, "bau": BAU,
                          "_ki": {"modell": modell, "pruefer": pruefer}})
            db["karten"][t["id"]] = karte
            stat["gebaut"] += 1
    db["stand"] = jetzt.isoformat(timespec="seconds")
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(db, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    stat["gesamt"] = len(db["karten"])
    stat["lehrplan"] = len(themen)
    return stat


def fuer_app(db: dict) -> dict:
    """Nur was die App braucht (ohne Modell-Protokoll)."""
    return {"stand": db.get("stand"),
            "karten": [{k: v for k, v in karte.items() if not k.startswith("_")} for karte in db.get("karten", {}).values()]}
