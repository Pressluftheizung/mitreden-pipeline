"""Volltexte für Top-Storys (PLAN.md 7): nur frei zugängliche Artikel von Quellen mit volltext_erlaubt.

Der Text bleibt im Arbeitsspeicher und wird nie gespeichert (DATENFLUSS.md 1). Er geht nur an die KI,
die daraus die Karte schreibt, und an die Prüf-KI.
"""

import html
from html.parser import HTMLParser

from .bilder import _lade

MAX_ZEICHEN = 4000


class _Absaetze(HTMLParser):
    """Sammelt den Text aller <p>-Absätze innerhalb von <article> (sonst der ganzen Seite)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tiefe_article = 0
        self.in_p = 0
        self.ignorieren = 0
        self.absaetze: list[tuple[bool, str]] = []  # (im Artikel, Text)
        self._jetzt: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "article":
            self.tiefe_article += 1
        elif tag in ("script", "style", "figure", "aside", "nav", "footer"):
            self.ignorieren += 1
        elif tag == "p":
            self.in_p += 1
            self._jetzt = []

    def handle_endtag(self, tag):
        if tag == "article" and self.tiefe_article:
            self.tiefe_article -= 1
        elif tag in ("script", "style", "figure", "aside", "nav", "footer") and self.ignorieren:
            self.ignorieren -= 1
        elif tag == "p" and self.in_p:
            self.in_p -= 1
            text = " ".join("".join(self._jetzt).split())
            if len(text) >= 60:  # Bildunterschriften, Teilen-Knöpfe und Hinweise sind kürzer
                self.absaetze.append((self.tiefe_article > 0, text))

    def handle_data(self, data):
        if self.in_p and not self.ignorieren:
            self._jetzt.append(data)


def lies_volltext(url: str) -> str | None:
    try:
        seite = _lade(url, max_bytes=1_500_000).decode("utf-8", "replace")
    except Exception:
        return None
    p = _Absaetze()
    try:
        p.feed(seite)
    except Exception:
        return None
    im_artikel = [t for drin, t in p.absaetze if drin]
    text = " ".join(im_artikel or [t for _, t in p.absaetze])
    return html.unescape(text)[:MAX_ZEICHEN] or None
