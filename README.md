# mitreden-pipeline

Pipeline für **Mitreden**, eine private News-App: dreimal täglich wissen, worüber die Leute reden.

Dieses Repo ist öffentlich und enthält **nur Code, Prompts und Konfiguration**. Gesammelte Teaser, fertige Karten und alle anderen Daten liegen ausschließlich in einem privaten Repo. Schlüssel kommen nur über GitHub Secrets in die Abläufe.

## Was hier läuft

| Ablauf | Wann | Was |
|---|---|---|
| `sammeln` | stündlich | lädt alle Feeds aus `config/sources.yaml` (ohne KI) und speichert neue Teaser im privaten Repo |
| `pruefen` | bei jedem Hochladen | Schutzprüfung (erlaubte Pfade, keine Schlüssel), gitleaks, Tests |
| `status` | monatlich | aktualisiert `STATUS.md`, damit GitHub die geplanten Abläufe nicht pausiert |

Geplant: `bauen` (dreimal täglich Karten bauen und prüfen).

## Werkbank (Phase 1)

Bündelt die gesammelten Meldungen zu Geschichten (Algorithmus A: Dubletten), berechnet ihre Wichtigkeit
(Algorithmus B) und schreibt eine Rangliste mit Begründung sowie die Abdeckung gegenüber Google News Top.

```bash
.venv/Scripts/python -m pipeline.werkbank --daten <checkout von rohdaten> --ausgabe <ordner außerhalb des repos>
```

Für die Ähnlichkeit nutzt sie das mehrsprachige Modell `paraphrase-multilingual-MiniLM-L12-v2` (lokal, ~220 MB,
wird beim ersten Lauf geladen). Schwellen und Gewichte stehen in `config/gewichte.yaml`, Themen und
Mitreden-Faktor in `config/topics.yaml`.

## Aufbau

```
config/sources.yaml   Quellen, Lager-Einordnung (Vorschlag, wird geprüft), Erkennungsregeln
config/topics.yaml    Themen, Mitreden-Faktor, Schlüsselwörter
config/gewichte.yaml  Gewichte und Schwellen für Dubletten und Wichtigkeit
pipeline/             Python-Code (collect.py = Sammeln, cluster.py = Dubletten, score.py = Wichtigkeit, werkbank.py)
tests/                Tests mit selbst geschriebenen Beispieldaten
tools/pruefe_repo.py  Schutzprüfung für dieses öffentliche Repo
.githooks/            Prüfung vor jedem Commit
```

## Lokal ausführen

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows (Linux/macOS: .venv/bin/python)
.venv/Scripts/python -m unittest discover tests
.venv/Scripts/python -m pipeline.collect --daten ../tmp-daten  # sammelt in einen lokalen Ordner
git config core.hooksPath .githooks                          # Prüfung vor jedem Commit einschalten
```

Der Datenordner liegt außerhalb dieses Repos und wird nie committet.

## Regeln

- Keine Artikeltexte, Teaser, Karten oder persönlichen Daten in dieses Repo.
- Keine Pressefotos. Bilder aus den Feeds werden nicht übernommen.
- Keine Schlüssel in Dateien. Lokal nur in `.env` (steht in `.gitignore`).
