"""Bilder für Karten (PLAN.md 6D, Entscheidung des Nutzers vom 31.10.2026).

1. Verlinkt: das Artikelbild eines Verlags (Adresse aus dem Feed, sonst og:image der Artikelseite).
   Gespeichert wird nur die Adresse; die App lädt das Bild beim Anzeigen direkt beim Verlag.
2. Frei (Ersatz): das Bild des passenden Wikipedia-Artikels, nur wenn es auf Wikimedia Commons liegt
   (freie Lizenz), mit Lizenz und Urheber.
3. Sonst kein Bild: die App zeigt eine Themen-Grafik.
"""

import html
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

KENNUNG = "Mitreden-Pipeline/0.1 (+https://github.com/Pressluftheizung/mitreden-pipeline)"
OG_IMAGE = re.compile(r"<meta[^>]+(?:property|name)=[\"'](?:og:image|twitter:image)[\"'][^>]*>", re.I)
CONTENT = re.compile(r"content=[\"']([^\"']+)[\"']", re.I)


def _lade(url: str, max_bytes: int = 400_000) -> bytes:
    anfrage = urllib.request.Request(url, headers={"User-Agent": KENNUNG, "Accept-Language": "de,en;q=0.8"})
    with urllib.request.urlopen(anfrage, timeout=10) as antwort:
        return antwort.read(max_bytes)


def og_image(artikel_url: str) -> str | None:
    """Vorschaubild der Artikelseite (nur die Adresse, das Bild wird nicht geladen)."""
    try:
        seite = _lade(artikel_url).decode("utf-8", "replace")
    except Exception:
        return None
    for tag in OG_IMAGE.findall(seite):
        treffer = CONTENT.search(tag)
        if treffer and treffer.group(1).startswith("https://"):
            return html.unescape(treffer.group(1))
    return None


def commons_bild(begriff: str, sprache: str = "de") -> dict | None:
    """Bild des Wikipedia-Artikels, nur von Wikimedia Commons, mit Lizenz und Urheber."""
    try:
        titel = urllib.parse.quote(begriff.replace(" ", "_"))
        info = json.loads(_lade(f"https://{sprache}.wikipedia.org/api/rest_v1/page/summary/{titel}"))
        bild = (info.get("originalimage") or info.get("thumbnail") or {}).get("source", "")
        if "/wikipedia/commons/" not in bild:
            return None
        teile = urllib.parse.urlsplit(bild).path.split("/")  # …/commons[/thumb]/c/cc/Datei.jpg[/330px-Datei.jpg]
        datei = urllib.parse.unquote(teile[teile.index("commons") + (4 if "thumb" in teile else 3)])
        abfrage = urllib.parse.urlencode({"action": "query", "titles": f"File:{datei}", "prop": "imageinfo",
                                          "iiprop": "url|extmetadata", "iiurlwidth": 1200, "format": "json"})
        seiten = json.loads(_lade(f"https://commons.wikimedia.org/w/api.php?{abfrage}"))["query"]["pages"]
        ii = next(iter(seiten.values()))["imageinfo"][0]
        meta = ii.get("extmetadata", {})
        urheber = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
        return {"art": "frei", "url": ii.get("thumburl") or ii["url"], "quelle": ii.get("descriptionurl"),
                "lizenz": meta.get("LicenseShortName", {}).get("value"), "urheber": urheber[:120] or None}
    except Exception:
        return None


def waehle_bilder(auftraege: list[dict], parallel: int = 8) -> list[dict | None]:
    """auftraege: je Karte {"feed_bilder": [(bild_url, artikel_url)], "artikel": [url], "begriff": str|None,
    "alt": str, "seiten_laden": bool}. Gibt je Karte das Feld `bild` zurück (oder None)."""

    def eins(a: dict) -> dict | None:
        verlinkt, artikel = None, None
        if a["feed_bilder"]:
            verlinkt, artikel = a["feed_bilder"][0]
        elif a.get("seiten_laden"):
            for url in a["artikel"][:2]:
                verlinkt = og_image(url)
                if verlinkt:
                    artikel = url
                    break
        frei = commons_bild(a["begriff"]) if a.get("begriff") else None
        if verlinkt:
            return {"art": "verlinkt", "url": verlinkt, "alt": a["alt"], "quelle": artikel, "ersatz": frei}
        if frei:
            return {**frei, "alt": a["alt"]}
        return None

    with ThreadPoolExecutor(parallel) as pool:
        return list(pool.map(eins, auftraege))
