"""Prüft, ob die KI-Schlüssel funktionieren, und listet die verfügbaren Modelle.

Gibt nur Modellnamen und Statuscodes aus, nie Schlüssel oder Antworttexte (die Logs sind öffentlich).
Schlüssel kommen aus den Umgebungsvariablen GEMINI_API_KEY und PRUEFER_API_KEY.

Aufruf: python tools/ki_check.py [--ausgabe datei.json]   (Modellliste zusätzlich als Datei, z. B. ins private Repo)
"""

import json
import os
import sys
import urllib.error
import urllib.request


def anfrage(url: str, kopf: dict, daten: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(daten).encode() if daten is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **kopf})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def main() -> int:
    fehler = 0
    ergebnis: dict = {}
    gemini = os.environ.get("GEMINI_API_KEY", "")
    if not gemini:
        print("GEMINI_API_KEY fehlt")
        fehler += 1
    else:
        kopf = {"x-goog-api-key": gemini}
        status, daten = anfrage("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", kopf)
        print(f"Gemini Modellliste: HTTP {status}")
        namen = [m["name"].removeprefix("models/") for m in daten.get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        print("  " + ", ".join(sorted(namen)))
        ergebnis["gemini"] = sorted(namen)
        test = next((n for n in sorted(namen, reverse=True) if "flash-lite" in n and "preview" not in n), None)
        if test:
            status, _ = anfrage(f"https://generativelanguage.googleapis.com/v1beta/models/{test}:generateContent", kopf,
                                {"contents": [{"parts": [{"text": "Antworte nur mit: OK"}]}]})
            print(f"Gemini Testanfrage ({test}): HTTP {status}")
            fehler += status != 200
    groq = os.environ.get("PRUEFER_API_KEY", "")
    if not groq:
        print("PRUEFER_API_KEY fehlt")
        fehler += 1
    else:
        kopf = {"Authorization": f"Bearer {groq}", "User-Agent": "mitreden-pipeline"}
        status, daten = anfrage("https://api.groq.com/openai/v1/models", kopf)
        print(f"Groq Modellliste: HTTP {status}")
        print("  " + ", ".join(sorted(m["id"] for m in daten.get("data", []))))
        ergebnis["groq"] = sorted(m["id"] for m in daten.get("data", []))
        status, _ = anfrage("https://api.groq.com/openai/v1/chat/completions", kopf,
                            {"model": "openai/gpt-oss-120b", "max_tokens": 50,
                             "messages": [{"role": "user", "content": "Antworte nur mit: OK"}]})
        print(f"Groq Testanfrage (openai/gpt-oss-120b): HTTP {status}")
        fehler += status != 200
    if "--ausgabe" in sys.argv:
        ziel = sys.argv[sys.argv.index("--ausgabe") + 1]
        os.makedirs(os.path.dirname(ziel) or ".", exist_ok=True)
        with open(ziel, "w", encoding="utf-8") as f:
            json.dump(ergebnis, f, indent=1)
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
