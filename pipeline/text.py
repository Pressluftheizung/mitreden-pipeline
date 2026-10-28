"""Textwerkzeuge für Dubletten und Wichtigkeit: Wörter, seltene Wörter, Produktnamen."""

import math
import re
from collections import Counter

WORT = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿĀ-žА-я]+(?:[-'’][0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ž]+)*")

# Häufige Wörter ohne Aussage (deutsch und englisch). Seltene Wörter tragen die Bedeutung.
STOPPWOERTER = set("""
aber alle allem allen aller alles als also am an ans auch auf aus bei beim bin bis bisher bitte da dabei damit dann
darf darum das dass dem den denen denn der deren des dessen die dies diese diesem diesen dieser dieses doch dort du
durch ein eine einem einen einer eines einige er es etwa euch fast für gab gegen geht gibt hat hatte hätte hier hin
ich ihm ihn ihr ihre im immer in ins ist ja jahr jahre jahren jede jedem jeden jeder jetzt kann kein keine können
konnte könnte machen macht man mehr mit muss nach neue neuen neuer neues nicht noch nun nur ob oder ohne seit sein
seine sich sie sind so soll sollen sollte sondern sowie um und uns unter viel viele vom von vor war waren was weil
weiter weitere wenn wer werden wie wieder will wir wird wo worden wurde wurden zu zum zur zwei zwischen über
heute gestern morgen uhr laut neu erst rund mehrere jahr prozent euro
a about after again against all also an and any are as at be because been before being but by can could did do does
for from had has have he her here his how i if in into is it its just more most new no not now of on one only or
other our out over said says she so some than that the their them then there these they this to too under up very
was we were what when where which while who why will with would year years you your news live update latest
""".split())

# Produktnamen für die Pixel-Regel (PLAN.md 6A): unterschiedliche Modelle = getrennte Geschichten.
MARKEN = (
    "iphone|ipad|macbook|imac|airpods|apple watch|vision pro|pixel|galaxy|xperia|oneplus|redmi|xiaomi|nothing phone|"
    "surface|thinkpad|playstation|xbox|switch|steam deck|kindle|echo|fire tv|gopro|"
    "geforce|radeon|rtx|ryzen|core ultra|snapdragon|tensor|gemini|gpt|claude|llama|grok"
)
# Marke plus mindestens ein Modellteil ("Pixel 10 Pro", "GPT-5"); Konsolen und Automodelle auch allein ("PS5", "Model Y").
PRODUKT = re.compile(
    rf"\b(?:(?:{MARKEN})(?:[\s-]+(?:[0-9][0-9a-z]*|pro|max|plus|ultra|mini|air|fold|flip|watch|buds|tab|lite|se|xl))+"
    r"|ps[3-6](?:[\s-]+(?:pro|slim|portal))?|(?:tesla[\s-]+)?model[\s-]+[3sxy])\b",
    re.I,
)


def woerter(text: str) -> list[str]:
    return [w.lower() for w in WORT.findall(text)]


def inhaltswoerter(text: str) -> set[str]:
    """Wörter mit Aussage. Bindestrich-Wörter zählen ganz und in Teilen ("steinmeier-nachfolge" auch als
    "steinmeier" und "nachfolge"), damit sie zu Meldungen passen, die die Teile einzeln nennen."""
    ergebnis = set()
    for w in woerter(text):
        for teil in (w, *re.split(r"[-'’]", w)) if "-" in w else (w,):
            if len(teil) >= 3 and teil not in STOPPWOERTER and not teil.isdigit():
                ergebnis.add(teil)
    return ergebnis


def produkte(text: str) -> set[str]:
    """Erkannte Produktbezeichnungen, normalisiert ("Pixel-10-Pro" → "pixel 10 pro", "Tesla Model Y" → "model y")."""
    return {re.sub(r"[\s-]+", " ", m.group(0).lower()).removeprefix("tesla ") for m in PRODUKT.finditer(text)}


def produkte_widersprechen(a: set[str], b: set[str]) -> bool:
    """Beide nennen Produkte, aber keines stimmt überein, z. B. Pixel 10 Pro gegen Pixel Watch 4."""
    if not a or not b or a & b:
        return False
    # "pixel 10" und "pixel 10 pro" widersprechen sich nicht (alle Wörter des einen stecken im anderen),
    # "pixel 9" und "pixel 9a" schon.
    return not any(set(x.split()) <= set(y.split()) or set(y.split()) <= set(x.split()) for x in a for y in b)


def bereinige_titel(titel: str, quellname: str | None = None) -> str:
    """Entfernt angehängte Quellennamen wie " - GameStar" (Google-News-Feeds)."""
    if quellname:
        titel = re.sub(rf"\s+[-–|]\s+{re.escape(quellname)}\s*$", "", titel, flags=re.I)
    return re.sub(r"\s+[-–|]\s+(?:AP News|Associated Press|ESPN|GameStar)\s*$", "", titel).strip()


class Seltenheit:
    """Wie selten ein Wort im aktuellen Bestand ist (IDF). Namen und Fachbegriffe sind selten, Füllwörter häufig."""

    def __init__(self, dokumente: list[set[str]]):
        self.n = max(1, len(dokumente))
        self.zaehler = Counter(w for d in dokumente for w in d)

    def gewicht(self, wort: str) -> float:
        return math.log((self.n + 1) / (self.zaehler.get(wort, 0) + 1))

    def gemeinsam(self, a: set[str], b: set[str], max_haeufig: float = 0.02) -> tuple[float, list[str]]:
        """Summe der Seltenheit gemeinsamer Wörter, die in höchstens max_haeufig aller Meldungen stehen."""
        grenze = max(2, self.n * max_haeufig)
        treffer = [w for w in a & b if self.zaehler.get(w, 0) <= grenze]
        return sum(self.gewicht(w) for w in treffer), sorted(treffer, key=self.gewicht, reverse=True)
