"""KI-Schicht (PLAN.md 9.3): austauschbare Anbieter, Kontingent-Zähler, Ersatzmodelle.

- Welche Aufgabe welches Modell nutzt, steht in config/ki.config.json (Kette: erstes Modell zuerst,
  bei Limit, Fehler oder ungültiger Antwort das nächste).
- Jede Antwort ist JSON. Ist sie kein gültiges JSON, zählt das als Fehler und das nächste Modell ist dran.
- Der Zähler merkt sich Anfragen und Tokens je Modell und Tag (UTC) und hält eine Reserve frei,
  damit das Gratis-Kontingent nie ganz aufgebraucht wird. Schlüssel kommen nur aus Umgebungsvariablen.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


class KeinModellFrei(Exception):
    """Alle Modelle der Kette sind ausgeschöpft oder fehlgeschlagen."""


class Zaehler:
    """Verbrauch je Modell und Tag, gespeichert in einer JSON-Datei (im privaten Repo)."""

    def __init__(self, datei: Path | None):
        self.datei = datei
        self.daten: dict = json.loads(datei.read_text(encoding="utf-8")) if datei and datei.exists() else {}
        self.minute: dict[str, deque] = {}  # Modell → (Zeit, Tokens) der letzten 60 Sekunden

    @staticmethod
    def tag() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def heute(self, modell: str) -> dict:
        return self.daten.setdefault(self.tag(), {}).setdefault(modell, {"anfragen": 0, "tokens": 0, "fehler": 0})

    def frei(self, modell: str, grenzen: dict, reserve: float, tokens: int) -> bool:
        h = self.heute(modell)
        if h["anfragen"] + 1 > grenzen["rpd"] * (1 - reserve):
            return False
        return not ("tpd" in grenzen and h["tokens"] + tokens > grenzen["tpd"] * (1 - reserve))

    def warte(self, modell: str, grenzen: dict, tokens: int) -> None:
        """Pausiert, bis Anfragen und Tokens pro Minute wieder Platz haben."""
        verlauf = self.minute.setdefault(modell, deque())
        while True:
            jetzt = time.monotonic()
            while verlauf and jetzt - verlauf[0][0] > 60:
                verlauf.popleft()
            if len(verlauf) < grenzen["rpm"] and sum(t for _, t in verlauf) + tokens <= grenzen["tpm"]:
                return
            time.sleep(max(1.0, 60 - (jetzt - verlauf[0][0])) if verlauf else 1.0)

    def buche(self, modell: str, tokens: int, fehler: bool = False, anfrage: bool = True) -> None:
        h = self.heute(modell)
        h["anfragen"] += anfrage
        h["tokens"] += tokens
        h["fehler"] += fehler
        self.minute.setdefault(modell, deque()).append((time.monotonic(), tokens))

    def speichere(self) -> None:
        if self.datei:
            alt = sorted(self.daten)[:-14]  # 14 Tage aufheben
            for tag in alt:
                del self.daten[tag]
            self.datei.parent.mkdir(parents=True, exist_ok=True)
            self.datei.write_text(json.dumps(self.daten, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def schaetze_tokens(text: str) -> int:
    return len(text) // 3 + 1  # grob: Deutsch hat etwa 3–4 Zeichen pro Token


def lese_json(text: str):
    """JSON aus einer Modellantwort (auch wenn sie in ```json … ``` steht)."""
    text = text.strip()
    treffer = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if treffer:
        text = treffer.group(1)
    return json.loads(text)


def _post(url: str, kopf: dict, daten: dict, zeitlimit: int = 180) -> dict:
    anfrage = urllib.request.Request(url, data=json.dumps(daten).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "mitreden-pipeline", **kopf})
    with urllib.request.urlopen(anfrage, timeout=zeitlimit) as antwort:
        return json.loads(antwort.read())


class HttpFehler(Exception):
    def __init__(self, code: int, text: str):
        super().__init__(f"HTTP {code}: {text[:200]}")
        self.code = code


def _rufe(url: str, kopf: dict, daten: dict) -> dict:
    try:
        return _post(url, kopf, daten)
    except urllib.error.HTTPError as e:
        raise HttpFehler(e.code, e.read().decode("utf-8", "replace")) from None


def frage_gemini(basis: str, schluessel: str, modell: str, system: str, nutzer: str,
                 max_ausgabe: int | None = None) -> tuple[str, int]:
    url = f"{basis}/models/{modell}:generateContent"
    daten = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": nutzer}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4,
                             "thinkingConfig": {"thinkingLevel": "low"}},
    }
    if max_ausgabe:
        daten["generationConfig"]["maxOutputTokens"] = max_ausgabe
    if modell.startswith("gemma"):  # Gemma kennt weder Systemanweisung noch JSON-Modus noch Denk-Stufen
        daten = {"contents": [{"role": "user", "parts": [{"text": system + "\n\n" + nutzer}]}]}
    try:
        antwort = _rufe(url, {"x-goog-api-key": schluessel}, daten)
    except HttpFehler as e:
        if e.code != 400 or "generationConfig" not in daten:
            raise
        del daten["generationConfig"]["thinkingConfig"]  # Modell ohne Denk-Stufen
        antwort = _rufe(url, {"x-goog-api-key": schluessel}, daten)
    teile = antwort["candidates"][0]["content"]["parts"]
    text = "".join(t.get("text", "") for t in teile if not t.get("thought"))
    return text, int(antwort.get("usageMetadata", {}).get("totalTokenCount", 0))


def frage_openai(basis: str, schluessel: str, modell: str, system: str, nutzer: str,
                 max_ausgabe: int | None = None) -> tuple[str, int]:
    daten = {"model": modell, "temperature": 0.2, "response_format": {"type": "json_object"},
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": nutzer}]}
    if max_ausgabe:  # Groq rechnet die erlaubte Antwortlänge schon vorab gegen das Minutenlimit
        daten["max_completion_tokens"] = max_ausgabe
    if "gpt-oss" in modell:
        daten["reasoning_effort"] = "low"  # Denken kostet Tokens; für die Prüfung reicht wenig
    elif "qwen" in modell:
        daten["reasoning_effort"] = "none"
    kopf = {"Authorization": f"Bearer {schluessel}"}
    try:
        antwort = _rufe(f"{basis}/chat/completions", kopf, daten)
    except HttpFehler as e:
        if e.code != 400 or "reasoning_effort" not in daten:
            raise
        del daten["reasoning_effort"]
        antwort = _rufe(f"{basis}/chat/completions", kopf, daten)
    return antwort["choices"][0]["message"]["content"], int(antwort.get("usage", {}).get("total_tokens", 0))


class KI:
    def __init__(self, config: Path, zaehler: Zaehler, basis: Path | None = None):
        self.k = json.loads(config.read_text(encoding="utf-8"))
        self.basis = basis or config.parent.parent
        self.zaehler = zaehler
        self.protokoll: list[dict] = []  # je Anfrage: Aufgabe, Modell, Tokens, Dauer, Fehler
        self._reihum = 0

    def prompt(self, aufgabe: str) -> str:
        return (self.basis / self.k["aufgaben"][aufgabe]["prompt"]).read_text(encoding="utf-8")

    def _frage(self, modell: str, system: str, nutzer: str, max_ausgabe: int | None = None) -> tuple[str, int]:
        m = self.k["modelle"][modell]
        a = self.k["anbieter"][m["anbieter"]]
        schluessel = os.environ.get(a["secret"], "")
        if not schluessel:
            raise HttpFehler(401, f"Umgebungsvariable {a['secret']} fehlt")
        if a["art"] == "gemini":
            return frage_gemini(a["url"], schluessel, modell, system, nutzer, max_ausgabe)
        return frage_openai(a["url"], schluessel, modell, system, nutzer, max_ausgabe)

    def frage(self, aufgabe: str, nutzer: str, pruefe=None):
        """Stellt die Frage an das erste freie Modell der Kette und gibt (JSON-Antwort, Modell) zurück.

        pruefe(antwort) darf eine Ausnahme werfen, wenn die Antwort nicht zum erwarteten Aufbau passt.
        """
        a = self.k["aufgaben"][aufgabe]
        system = self.prompt(aufgabe)
        kette = list(a["kette"])
        if a.get("verteilen"):  # mehrere gleichwertige Modelle reihum, damit beide Kontingente genutzt werden
            self._reihum += 1
            kette = kette[self._reihum % len(kette):] + kette[:self._reihum % len(kette)]
        letzte = None
        for modell in kette:
            grenzen = self.k["modelle"][modell]
            # Antwortlänge: Aufgabe, höchstens aber die Grenze des Modells (qwen bei Groq: 1000 Tokens pro Minute)
            max_ausgabe = min((x for x in (a.get("max_ausgabe"), grenzen.get("max_ausgabe")) if x), default=None)
            geschaetzt = schaetze_tokens(system + nutzer) + (max_ausgabe or schaetze_tokens(system + nutzer))  # Ein- plus Ausgabe
            if not self.zaehler.frei(modell, grenzen, self.k["je_bau"]["reserve_anteil"], geschaetzt):
                letzte = f"{modell}: Tageskontingent erreicht"
                continue
            if geschaetzt > grenzen["tpm"]:  # passt nie ins Minutenlimit: sonst wartet warte() endlos (15-Uhr-Bau 5.11.)
                letzte = f"{modell}: Anfrage zu groß fürs Minutenlimit ({geschaetzt} > {grenzen['tpm']})"
                self.protokoll.append({"aufgabe": aufgabe, "modell": modell, "fehler": letzte, "sekunden": 0})
                continue
            for versuch in (1, 2):
                self.zaehler.warte(modell, grenzen, geschaetzt)
                start = time.monotonic()
                try:
                    text, tokens = self._frage(modell, system, nutzer, max_ausgabe)
                    antwort = lese_json(text)
                    if pruefe:
                        pruefe(antwort)
                except HttpFehler as e:
                    # Überlastet (5xx) oder Minutenlimit (429) zählen nicht als Anfrage: kurz warten, ein zweiter Versuch
                    self.zaehler.buche(modell, 0, fehler=True, anfrage=False)
                    self.protokoll.append({"aufgabe": aufgabe, "modell": modell, "fehler": str(e)[:200],
                                           "sekunden": round(time.monotonic() - start, 1)})
                    letzte = f"{modell}: {e}"
                    pause = wartezeit(e)
                    if versuch == 1 and pause is not None:
                        time.sleep(pause)
                        continue
                    break
                except Exception as e:  # Netz, ungültiges JSON, falscher Aufbau: nächstes Modell, Bau läuft weiter
                    self.zaehler.buche(modell, geschaetzt // 2, fehler=True)
                    self.protokoll.append({"aufgabe": aufgabe, "modell": modell, "fehler": str(e)[:200],
                                           "sekunden": round(time.monotonic() - start, 1)})
                    letzte = f"{modell}: {e}"
                    break  # ungültige Antwort: nächstes Modell
                self.zaehler.buche(modell, tokens or geschaetzt)
                self.protokoll.append({"aufgabe": aufgabe, "modell": modell, "tokens": tokens,
                                       "sekunden": round(time.monotonic() - start, 1)})
                return antwort, modell
        raise KeinModellFrei(letzte or "keine Modelle konfiguriert")


def wartezeit(e: HttpFehler) -> float | None:
    """Wie lange vor einem zweiten Versuch warten? None = nicht noch einmal versuchen."""
    text = str(e)
    if e.code == 429 and "too large" not in text.lower():
        treffer = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", text)
        if treffer:
            sekunden = int(treffer.group(1) or 0) * 60 + float(treffer.group(2)) / (1000 if treffer.group(3) == "ms" else 1)
            return min(65.0, sekunden + 1)
        return 30.0
    if e.code >= 500:
        return 10.0
    return None
