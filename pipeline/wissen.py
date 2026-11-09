"""Allgemeinwissen (PLAN.md 18): Wissenskarten aus dem Lehrplan, einmal gebaut und dauerhaft gespeichert.

Ablauf je Lauf (läuft nach jeder Ausgabe mit, ein paar Themen pro Lauf):
  1. Nächste offene Themen aus config/lehrplan.json (erst Stufe 1, dann 2, dann 3).
  2. Quelle: Text und freies Bild des deutschen Wikipedia-Artikels (Wikipedia/Wikimedia, frei).
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

from .daten import _json
from .ki import KeinModellFrei

WIKI = "https://de.wikipedia.org/w/api.php?"
WIKIMEDIA = re.compile(r"https://(upload|thumb)\.wikimedia\.org/")
QUELLE_ZEICHEN = 9000
PRUEF_ZEICHEN = 7000


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
                           "_rang": (t["stufe"], m_nr, t_nr)})
    return sorted(themen, key=lambda t: t["_rang"])


def offene_themen(themen: list[dict], fertig: set[str], anzahl: int) -> list[dict]:
    return [t for t in themen if t["id"] not in fertig][:anzahl]


# ---------- Quelle ----------

def quelle(titel: str) -> dict | None:
    """Text (ohne Wiki-Markup) und Seitenbild des deutschen Wikipedia-Artikels."""
    try:
        d = _json(WIKI + urllib.parse.urlencode({
            "action": "query", "format": "json", "titles": titel, "redirects": 1,
            "prop": "extracts|pageimages|info", "explaintext": 1, "exsectionformat": "plain",
            "piprop": "thumbnail", "pithumbsize": 800, "inprop": "url"}))
    except Exception:
        return None
    for s in ((d.get("query") or {}).get("pages") or {}).values():
        text = (s.get("extract") or "").strip()
        if "missing" in s or len(text) < 400:
            return None
        bild = ((s.get("thumbnail") or {}).get("source") or "").split("?")[0]
        return {"titel": s.get("title") or titel, "url": s.get("fullurl") or "", "text": text[:QUELLE_ZEICHEN],
                "bild": bild if WIKIMEDIA.match(bild) else None}
    return None


# ---------- Prüfen im Code ----------

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


def fragen_bereinigen(roh, quelltext: str) -> list[dict]:
    """Nur vollständige Fragen; Schätzwerte müssen als Zahl in der Quelle stehen (sonst könnte die KI sie erfinden)."""
    in_quelle = zahlen(quelltext)
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
    fragen = fragen_bereinigen(roh.get("fragen"), q["text"])
    if not satz or len(punkte) < 2 or not fragen:
        return None
    return {
        "id": thema["id"], "modul": thema["modul"], "modul_titel": thema["modul_titel"], "bereich": thema["bereich"],
        "auch": thema.get("auch", []), "stufe": thema["stufe"], "braucht": thema["braucht"],
        "titel": str(roh.get("titel") or thema["titel"]).strip()[:120], "satz": satz, "punkte": punkte,
        "mini": texte(roh.get("mini"), 2, 250), "fragen": fragen,
        "bild": {"url": q["bild"], "alt": thema["titel"], "art": "frei"} if q.get("bild") else None,
        "quelle": {"name": "Wikipedia", "titel": q["titel"], "url": q["url"]},
    }


def pruefung_anwenden(karte: dict, urteil: dict) -> dict | None:
    """Was die Prüf-KI nicht in der Quelle findet, fliegt raus; ohne Fragen oder mit falschem Satz keine Karte."""
    falsche_fragen = {i for i in urteil.get("fragen_falsch") or [] if isinstance(i, int)}
    falsche_punkte = {i for i in urteil.get("punkte_falsch") or [] if isinstance(i, int)}
    falsche_mini = {i for i in urteil.get("mini_falsch") or [] if isinstance(i, int)}
    if urteil.get("satz_falsch") is True:
        return None
    karte = {**karte,
             "fragen": [f for i, f in enumerate(karte["fragen"]) if i not in falsche_fragen],
             "punkte": [p for i, p in enumerate(karte["punkte"]) if i not in falsche_punkte],
             "mini": [m for i, m in enumerate(karte["mini"]) if i not in falsche_mini]}
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
    offen = offene_themen(themen, set(db["karten"]), anzahl)
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
                               "quelle": q["text"]} for t, q in portion]}
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
            pruef = {"quelle": q["text"][:PRUEF_ZEICHEN], "satz": karte["satz"], "punkte": karte["punkte"],
                     "mini": karte["mini"], "fragen": karte["fragen"]}
            try:
                urteil, pruefer = ki.frage("wissen_pruefen", json.dumps(pruef, ensure_ascii=False), pruefe_urteil)
            except KeinModellFrei as e:  # ohne Prüfung nichts speichern; das Thema kommt im nächsten Lauf wieder
                stat["fehler"].append(f"wissen_pruefen: {e}")
                continue
            karte = pruefung_anwenden(karte, urteil)
            if not karte:
                stat["verworfen"] += 1
                continue
            karte.update({"erstellt": jetzt.isoformat(timespec="seconds"), "version": 1,
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
