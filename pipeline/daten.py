"""Datenkarten (PLAN.md 4.1 und 6D): Zahlen (Börse) und Ergebnisse (Fußball), gebaut vom Programm.

- Zahlen: DAX, S&P 500, Bitcoin, Gold, Öl, Euro/Dollar mit Veränderung zum letzten Schlusskurs.
  Quelle: Yahoo Finance (Kursdaten), Ersatz: EZB-Referenzkurs (Euro/Dollar), CoinGecko (Bitcoin).
  Den Satz „warum“ schreibt die KI aus den Schlagzeilen der Ausgabe; ein Programm verwirft ihn, wenn er
  Zahlen enthält, die weder in den Daten noch in den Schlagzeilen stehen.
- Ergebnisse: Bundesliga, Champions League, Europa League, DFB-Pokal, Premier League, La Liga und 2. Bundesliga
  von OpenLigaDB (frei, ohne Schlüssel). Je Wettbewerb der ganze Spieltag (gespielte Spiele mit Ergebnis,
  offene mit Anstoßzeit), wenn in den letzten 36 Stunden ein Spiel zu Ende ging. Logos nur von Wikimedia.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .pruefen import zahlen

KENNUNG = "Mozilla/5.0 (compatible; Mitreden-Pipeline/0.1; +https://github.com/Pressluftheizung/mitreden-pipeline)"

WERTE = [  # Name, Yahoo-Symbol, Einheit, Nachkommastellen
    ("DAX", "^GDAXI", "Punkte", 0),
    ("S&P 500", "^GSPC", "Punkte", 0),
    ("Bitcoin", "BTC-EUR", "€", 0),
    ("Gold", "GC=F", "$ je Unze", 0),
    ("Öl (Brent)", "BZ=F", "$ je Barrel", 2),
    ("Euro/Dollar", "EURUSD=X", "$", 4),
]

# Reihenfolge = Reihenfolge der Seiten in der App (erste Liga mit neuen Ergebnissen ist die Hauptkarte)
LIGEN = [("bl1", "1. Bundesliga", "deutschland"), ("ucl", "Champions League", "europa"),
         ("uel2026", "Europa League", "europa"), ("dfb", "DFB-Pokal", "deutschland"),
         ("pl", "Premier League", "europa"), ("la1", "La Liga", "europa"), ("bl2", "2. Bundesliga", "deutschland")]
WOCHENTAG = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _json(url: str):
    anfrage = urllib.request.Request(url, headers={"User-Agent": KENNUNG, "Accept": "application/json"})
    with urllib.request.urlopen(anfrage, timeout=15) as antwort:
        return json.loads(antwort.read())


def deutsch(zahl: float, stellen: int) -> str:
    return f"{zahl:,.{stellen}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def yahoo(symbol: str) -> tuple[float, float] | None:
    """(aktueller Kurs, letzter Schlusskurs davor)."""
    try:
        d = _json(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d")
        r = d["chart"]["result"][0]
        schluss = [x for x in r["indicators"]["quote"][0]["close"] if x]
        jetzt = r["meta"].get("regularMarketPrice") or schluss[-1]
        # Ist der letzte Tageswert schon der aktuelle Kurs, zählt der Schlusskurs des Vortags.
        vorher = schluss[-2] if len(schluss) >= 2 and abs(schluss[-1] - jetzt) <= abs(jetzt) * 0.0005 else schluss[-1]
        return float(jetzt), float(vorher)
    except Exception:
        return None


def ersatz(name: str) -> tuple[float, float] | None:
    try:
        if name == "Euro/Dollar":
            zeilen = urllib.request.urlopen(urllib.request.Request(
                "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?lastNObservations=2&format=csvdata",
                headers={"User-Agent": KENNUNG}), timeout=15).read().decode().strip().splitlines()[1:]
            kurse = [float(z.split(",")[7]) for z in zeilen]
            return kurse[-1], kurse[-2]
        if name == "Bitcoin":
            d = _json("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=eur&include_24hr_change=true")
            jetzt = float(d["bitcoin"]["eur"])
            return jetzt, jetzt / (1 + d["bitcoin"]["eur_24h_change"] / 100)
    except Exception:
        return None
    return None


def lade_zahlen() -> list[dict]:
    werte = []
    for name, symbol, einheit, stellen in WERTE:
        kurs = yahoo(symbol) or ersatz(name)
        if not kurs:
            continue
        jetzt, vorher = kurs
        werte.append({"name": name, "wert": f"{deutsch(jetzt, stellen)} {einheit}",
                      "veraenderung_prozent": round((jetzt / vorher - 1) * 100, 2) if vorher else 0.0})
    return werte


def vorlage_warum(w: dict) -> str:
    """Satz ohne KI: Richtung und Stärke der Bewegung."""
    p = w["veraenderung_prozent"]
    if abs(p) < 0.2:
        return "Kaum verändert."
    staerke = " deutlich" if abs(p) >= 2 else ""
    richtung = f"legt{staerke} zu" if p > 0 else f"gibt{staerke} nach"
    return f"{w['name']} {richtung}: {'+' if p > 0 else '−'}{deutsch(abs(p), 1)} %."


# Ohne geprüften KI-Grund kein Satz: die Vorlage wiederholte nur die Zahl (Design-Review 09.11.2026)
def zahlen_karte(werte: list[dict], schlagzeilen: list[str], ki, ausgabe: str) -> dict | None:
    """Karte „Zahlen“: Werte vom Programm, „warum“ von der KI mit Prüfung, sonst aus der Vorlage."""
    if not werte:
        return None
    warum: dict[str, str] = {}
    if ki is not None:
        try:
            antwort, _ = ki.frage("zahlen", json.dumps({"werte": werte, "schlagzeilen": schlagzeilen[:40]}, ensure_ascii=False),
                                  lambda a: isinstance(a.get("warum"), dict) or (_ for _ in ()).throw(ValueError("warum fehlt")))
            erlaubt = set().union(*(zahlen(t) for t in schlagzeilen)) | set().union(
                *(zahlen(f"{w['wert']} {w['veraenderung_prozent']}") for w in werte))
            for name, satz in antwort["warum"].items():
                if isinstance(satz, str) and satz.strip() and zahlen(satz) <= erlaubt:
                    warum[name] = satz.strip()  # Zahlen, die nirgends stehen: Satz verwerfen, Vorlage nehmen
        except Exception:
            pass
    return {"id": f"zahlen-{ausgabe}", "format": "zahlen", "thema": "geld", "region": "welt", "titel": "Die Zahlen",
            "labels": [], "quellen": [{"name": "Yahoo Finance", "lager": "international", "url": "https://finance.yahoo.com"}],
            "lager_mix": {}, "werte": [{**w, "warum": warum.get(w["name"])} for w in werte]}


def _logo(team: dict) -> str | None:
    """Vereinslogo nur von Wikimedia (freie Lizenz, CLAUDE.md: Bilder nur frei); sonst zeigt die App ein Kürzel."""
    url = team.get("teamIconUrl") or ""
    return url if url.startswith("https://upload.wikimedia.org/") else None


def berlin(utc: datetime) -> datetime:
    """Deutsche Uhrzeit ohne Zeitzonen-Datenbank (fehlt unter Windows): Sommerzeit vom letzten Sonntag im März
    bis zum letzten Sonntag im Oktober, jeweils 01:00 UTC."""
    def letzter_sonntag(monat: int) -> datetime:
        tag = datetime(utc.year, monat, 31, 1, tzinfo=timezone.utc)
        return tag - timedelta(days=(tag.weekday() + 1) % 7)
    sommer = letzter_sonntag(3) <= utc < letzter_sonntag(10)
    return (utc + timedelta(hours=2 if sommer else 1)).replace(tzinfo=None)


def _anstoss(s: dict) -> str:
    beginn = berlin(datetime.fromisoformat(s["matchDateTimeUTC"].replace("Z", "+00:00")))
    return f"{WOCHENTAG[beginn.weekday()]} {beginn:%H:%M}"


def lade_ergebnisse(jetzt: datetime, stunden: int = 36) -> list[dict]:
    """Je Wettbewerb der ganze aktuelle Spieltag, wenn darin in den letzten `stunden` ein Spiel zu Ende ging
    (Feedback 07.11.2026: nicht nur ein Ergebnis und sonst leer; auch Champions League, Premier League, La Liga)."""
    ergebnis = []
    for kurz, name, region in LIGEN:
        try:
            spiele = _json(f"https://api.openligadb.de/getmatchdata/{kurz}")
        except Exception:
            continue
        alle, frisch = [], False
        for s in sorted(spiele, key=lambda s: s.get("matchDateTimeUTC") or ""):
            if not s.get("matchDateTimeUTC"):
                continue
            beginn = datetime.fromisoformat(s["matchDateTimeUTC"].replace("Z", "+00:00"))
            end = None
            if s.get("matchIsFinished"):
                end = next((r for r in s.get("matchResults", []) if r.get("resultTypeID") == 2), None) or \
                    (s.get("matchResults") or [None])[-1]
            if end and timedelta(0) <= jetzt - beginn <= timedelta(hours=stunden):
                frisch = True
            alle.append({"heim": s["team1"]["shortName"] or s["team1"]["teamName"],
                         "gast": s["team2"]["shortName"] or s["team2"]["teamName"],
                         "tore": (end["pointsTeam1"], end["pointsTeam2"]) if end else None,
                         "anstoss": _anstoss(s), "heim_logo": _logo(s["team1"]), "gast_logo": _logo(s["team2"]),
                         "runde": (s.get("group") or {}).get("groupName", "")})
        if frisch:
            ergebnis.append({"liga": kurz, "wettbewerb": name, "region": region, "runde": alle[0]["runde"], "spiele": alle})
    return ergebnis


def vorlage_titel(spiele: list[dict]) -> str:
    """Titel aus Regeln (PLAN.md 6D): höchster Sieg, sonst Torfestival, sonst Spieltag."""
    groesster = max(spiele, key=lambda s: abs(s["tore"][0] - s["tore"][1]))
    a, b = groesster["tore"]
    if abs(a - b) >= 3:
        sieger, verlierer = (groesster["heim"], groesster["gast"]) if a > b else (groesster["gast"], groesster["heim"])
        return f"{sieger} schießt {verlierer} ab"
    tore = max(spiele, key=lambda s: sum(s["tore"]))
    if sum(tore["tore"]) >= 6:
        return f"Torfestival: {tore['heim']} gegen {tore['gast']}"
    return "Die Ergebnisse"


def ergebnis_karten(ligen: list[dict], ausgabe: str, schon_gezeigt: set[str]) -> list[dict]:
    karten = []
    for liga in ligen:
        gespielt = [s for s in liga["spiele"] if s.get("tore") is not None]
        neu = [s for s in gespielt if f"{liga['liga']}:{s['heim']}:{s['gast']}:{s['runde']}" not in schon_gezeigt]
        if not neu:
            continue
        hoehepunkt = vorlage_titel(gespielt)
        karten.append({
            "id": f"ergebnisse-{liga['liga']}-{liga['runde'].lower().replace('.', '').replace(' ', '-')}", "format": "ergebnisse", "thema": "sport", "unterthema": "fussball",
            # Feedback 07.11.2026: Wettbewerb als Überschrift, Höhepunkt darunter
            "region": liga.get("region", "deutschland"), "titel": liga["wettbewerb"], "labels": [],
            "satz": hoehepunkt if hoehepunkt != "Die Ergebnisse" else None,
            "quellen": [{"name": "OpenLigaDB", "lager": "international", "url": "https://www.openligadb.de"}], "lager_mix": {},
            "tabelle": {"wettbewerb": liga["runde"],
                        "spiele": [{"heim": s["heim"], "gast": s["gast"],
                                    "ergebnis": f"{s['tore'][0]}:{s['tore'][1]}" if s.get("tore") is not None else None,
                                    "anstoss": s.get("anstoss"), "heim_logo": s.get("heim_logo"), "gast_logo": s.get("gast_logo")}
                                   for s in liga["spiele"]]},
            "_gezeigt": [f"{liga['liga']}:{s['heim']}:{s['gast']}:{s['runde']}" for s in gespielt],
        })
    return karten
