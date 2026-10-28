"""Schutzprüfung für das öffentliche Repo (PLAN.md 11.2, Punkte 4 und 5).

Prüft alle Dateien, die committet sind oder gerade committet werden:
  1. Nur erlaubte Pfade (Code, Prompts, Konfiguration, Vorlagen, Tests). Daten gehören ins private Repo.
  2. Keine Schlüssel oder Tokens im Text.
  3. Testdateien unter tests/fixtures/ müssen selbst geschrieben sein (Kennzeichnung im Text),
     damit nie echte Artikeltexte im öffentlichen Repo landen.

Aufruf:
  python tools/pruefe_repo.py           alle Dateien im Repo
  python tools/pruefe_repo.py --staged  nur die Dateien des nächsten Commits (Pre-Commit-Hook)
"""

import re
import subprocess
import sys
from pathlib import PurePosixPath

ERLAUBTE_ORDNER = (
    ".github/", ".githooks/", "config/", "pipeline/", "prompts/", "templates/",
    "schemas/", "tests/", "tools/", "werkbank/",
)
ERLAUBTE_DATEIEN = {
    "README.md", "STATUS.md", "LICENSE", ".gitignore", ".gitattributes", ".env.example", "requirements.txt",
}
VERBOTENE_ENDUNGEN = {".jsonl", ".env", ".pem", ".key", ".p12", ".sqlite", ".db"}
MAX_GROESSE = 300_000  # Bytes; größere Dateien sind fast immer Daten
FIXTURE_KENNZEICHEN = "SELBST GESCHRIEBEN"

SCHLUESSEL_MUSTER = {
    "Google-API-Schlüssel": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "Groq-Schlüssel": re.compile(r"gsk_[0-9A-Za-z]{40,}"),
    "GitHub-Token": re.compile(r"(?:github_pat_[0-9A-Za-z_]{40,}|gh[pousr]_[0-9A-Za-z]{36,})"),
    "Anthropic-Schlüssel": re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}"),
    "OpenAI-Schlüssel": re.compile(r"sk-(?:proj-)?[0-9A-Za-z]{32,}"),
    "Privater Schlüssel": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def git(*argumente: str) -> bytes:
    return subprocess.run(["git", *argumente], check=True, capture_output=True).stdout


def dateien(nur_staged: bool) -> list[str]:
    if nur_staged:
        ausgabe = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        ausgabe = git("ls-files", "-z")
    return [d for d in ausgabe.decode("utf-8").split("\0") if d]


def inhalt(pfad: str, nur_staged: bool) -> bytes:
    # Beim Commit zählt der Stand im Index, nicht der auf der Festplatte.
    return git("show", f":{pfad}") if nur_staged else open(pfad, "rb").read()


def pruefe(pfad: str, daten: bytes) -> list[str]:
    probleme = []
    p = PurePosixPath(pfad)
    if pfad not in ERLAUBTE_DATEIEN and not pfad.startswith(ERLAUBTE_ORDNER):
        probleme.append("Pfad nicht erlaubt (Daten gehören ins private Repo)")
    if p.suffix.lower() in VERBOTENE_ENDUNGEN or p.name.startswith(".env.") and p.name != ".env.example":
        probleme.append(f"Dateityp {p.suffix or p.name} ist verboten")
    if len(daten) > MAX_GROESSE:
        probleme.append(f"zu groß ({len(daten) // 1000} kB), vermutlich Daten")
    text = daten.decode("utf-8", errors="ignore")
    for name, muster in SCHLUESSEL_MUSTER.items():
        if muster.search(text):
            probleme.append(f"enthält vermutlich einen {name}")
    if pfad.startswith("tests/fixtures/") and FIXTURE_KENNZEICHEN not in text:
        probleme.append(f"Testdatei ohne Kennzeichen „{FIXTURE_KENNZEICHEN}“ (keine echten Artikel ins Repo)")
    return probleme


def main() -> int:
    nur_staged = "--staged" in sys.argv
    fehler = 0
    for pfad in dateien(nur_staged):
        for problem in pruefe(pfad, inhalt(pfad, nur_staged)):
            print(f"✗ {pfad}: {problem}")
            fehler += 1
    if fehler:
        print(f"\nSchutzprüfung: {fehler} Problem(e). Nichts committen, erst beheben.")
        return 1
    print("Schutzprüfung: alles in Ordnung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
