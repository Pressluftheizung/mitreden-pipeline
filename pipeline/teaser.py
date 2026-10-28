"""Wandelt einen geladenen Feed in Teaser-Einträge um (Format: docs/DATENFLUSS.md Abschnitt 2).

Reine Funktionen ohne Netz und ohne Dateien, damit sie sich leicht testen lassen.
Bilder aus den Feeds (meist Pressefotos) werden bewusst nicht übernommen.
"""

import calendar
import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

from .quellen import Konfiguration, Quelle

MAX_TEASER = 500
# Parameter, die nur der Reichweitenmessung dienen. Sie machen aus einem Artikel sonst mehrere Adressen.
TRACKING = re.compile(r"^(utm_|wt_|at_|mc_|fbclid$|gclid$|traffic_source$|ref$|cmpid$|ns_|xtor$|icid$)", re.I)
TAG = re.compile(r"<[^>]+>")
LEERRAUM = re.compile(r"\s+")


def iso(zeit: datetime) -> str:
    return zeit.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalisiere_url(url: str) -> str:
    teile = urlsplit(url.strip())
    abfrage = [(k, v) for k, v in parse_qsl(teile.query, keep_blank_values=True) if not TRACKING.match(k)]
    pfad = teile.path.rstrip("/") or "/"
    return urlunsplit((teile.scheme.lower(), teile.netloc.lower(), pfad, urlencode(abfrage), ""))


def teaser_id(url: str) -> str:
    return hashlib.sha1(normalisiere_url(url).encode("utf-8")).hexdigest()


def reiner_text(roh: str | None) -> str:
    """HTML entfernen, Sonderzeichen auflösen, Leerraum zusammenziehen."""
    if not roh:
        return ""
    text = html.unescape(TAG.sub(" ", roh))
    return LEERRAUM.sub(" ", text).strip()


def kuerze(text: str, laenge: int = MAX_TEASER) -> str:
    if len(text) <= laenge:
        return text
    schnitt = text[: laenge - 1]
    leer = schnitt.rfind(" ")
    return (schnitt[:leer] if leer > laenge * 0.6 else schnitt).rstrip(" ,.;:") + "…"


def zeitpunkt(eintrag) -> str | None:
    for feld in ("published_parsed", "updated_parsed"):
        wert = eintrag.get(feld)
        if wert:
            # feedparser liefert die Zeit bereits in UTC.
            return iso(datetime.fromtimestamp(calendar.timegm(wert), tz=timezone.utc))
    return None


def stichwoerter(eintrag) -> list[str]:
    return [t.get("term", "") for t in eintrag.get("tags", []) if t.get("term")]


def rubrik(eintrag) -> str | None:
    # Manche Feeds liefern interne Kennungen statt Rubriken (z. B. Fox News), die überspringen wir.
    for wort in stichwoerter(eintrag):
        if not re.fullmatch(r"[0-9a-f\-]{20,}|[A-Z_]+|[a-z\-]+/[a-z\-/]+", wort):
            return wort
    return None


def ist_meinung(url: str, titel: str, woerter: list[str], quelle: Quelle, konfig: Konfiguration) -> bool:
    """Erste, einfache Stufe von B1 (PLAN.md 8). Teil des Bias-Reviews, noch nicht freigegeben."""
    pfad = urlsplit(url).path.lower()
    if any(m in pfad for m in (*konfig.meinung_muster, *quelle.meinung_muster)):
        return True
    praefixe = "|".join(re.escape(t) for t in konfig.meinung_titel)
    if praefixe and re.match(rf"^\s*(?:{praefixe})\b\s*[:|–-]", titel, re.I):
        return True
    return any(w in konfig.meinung_stichwoerter for w in woerter)


def bezahlschranke(eintrag, url: str, woerter: list[str], quelle: Quelle) -> bool | None:
    """True = Bezahlartikel, False = frei, None = unbekannt (Feed sagt es nicht)."""
    if quelle.bezahlschranke == "nein":
        return False
    if quelle.bezahlschranke == "ja":
        return True
    if any(re.search(m, url, re.I) for m in quelle.bezahl_muster):
        return True
    if any(w in quelle.bezahl_stichwoerter for w in woerter):
        return True
    if quelle.bezahl_feld:
        wert = str(eintrag.get(quelle.bezahl_feld, "")).strip().lower()
        if wert in {"true", "1", "yes"}:
            return True
        if wert in {"false", "0", "no"}:
            return False
    return False if quelle.bezahl_markiert else None


def lies_feed(daten: bytes, quelle: Quelle, konfig: Konfiguration, jetzt: datetime) -> list[dict]:
    feed = feedparser.parse(daten)
    ergebnis = []
    gesehen = set()
    for position, eintrag in enumerate(feed.entries, start=1):
        url = (eintrag.get("link") or "").strip()
        titel = reiner_text(eintrag.get("title"))
        if not url.startswith(("http://", "https://")) or not titel:
            continue
        if any(w.lower() in titel.lower() for w in konfig.werbung_titel):
            continue  # bezahlte Werbung, keine Nachricht
        tid = teaser_id(url)
        if tid in gesehen:
            continue
        gesehen.add(tid)
        woerter = stichwoerter(eintrag)
        ergebnis.append({
            "id": tid,
            "quelle": quelle.id,
            "url": url,
            "titel": titel,
            "teaser": kuerze(reiner_text(eintrag.get("summary"))),
            "veroeffentlicht": zeitpunkt(eintrag),
            "gesammelt": iso(jetzt),
            "zuletzt_gesehen": iso(jetzt),
            "sprache": quelle.sprache,
            "rubrik": rubrik(eintrag),
            "ist_meinung": ist_meinung(url, titel, woerter, quelle, konfig),
            "bezahlschranke": bezahlschranke(eintrag, url, woerter, quelle),
            "position_im_feed": position,
        })
    return ergebnis


def lies_signal(daten: bytes, signal_id: str, jetzt: datetime) -> list[dict]:
    """Google Trends / Google News Top: nur Titel, Rang und (bei Trends) die ungefähre Suchmenge."""
    feed = feedparser.parse(daten)
    ergebnis = []
    for rang, eintrag in enumerate(feed.entries, start=1):
        titel = reiner_text(eintrag.get("title"))
        if not titel:
            continue
        ergebnis.append({
            "signal": signal_id,
            "zeit": iso(jetzt),
            "rang": rang,
            "titel": titel,
            "url": eintrag.get("link"),
            "suchmenge": eintrag.get("ht_approx_traffic"),
        })
    return ergebnis
