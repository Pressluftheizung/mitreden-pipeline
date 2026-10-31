# mitreden-pipeline

Pipeline für **Mitreden**, eine private News-App: dreimal täglich wissen, worüber die Leute reden.

Dieses Repo ist öffentlich und enthält **nur Code, Prompts und Konfiguration**. Gesammelte Teaser, fertige Karten und alle anderen Daten liegen ausschließlich in einem privaten Repo. Schlüssel kommen nur über GitHub Secrets in die Abläufe.

## Was hier läuft

| Ablauf | Wann | Was |
|---|---|---|
| `sammeln` | stündlich (:23, gestartet vom Cloudflare-Worker `cloudflare/takt.js`) | lädt alle Feeds aus `config/sources.yaml` (ohne KI) und speichert neue Teaser im privaten Repo |
| `pruefen` | bei jedem Hochladen | Schutzprüfung (erlaubte Pfade, keine Schlüssel), gitleaks, Tests |
| `ausgabe` (Datei `kontrolle.yml`) | 3× täglich (7:23, 14:23, 19:23) | baut die Karten (`pipeline/bauen.py`: Gemini schreibt, Groq prüft, Bilder) und kontrolliert, ob alles Relevante drin ist; bis zum Bias-Review nur Testkarten im privaten Repo (`testkarten/`) |
| `status` | monatlich | aktualisiert `STATUS.md`, damit GitHub die geplanten Abläufe nicht pausiert |


## Werkbank (Phase 1)

Bündelt die gesammelten Meldungen zu Geschichten (Algorithmus A: Dubletten), berechnet ihre Wichtigkeit
(Algorithmus B) und schreibt eine Rangliste mit Begründung sowie die Abdeckung gegenüber Google News Top
(je fehlender Meldung mit Grund: aussortiert, unsicher oder fehlt).

```bash
.venv/Scripts/python -m pipeline.werkbank --daten <checkout von rohdaten> --ausgabe <ordner außerhalb des repos>
```

Für die Ähnlichkeit nutzt sie das mehrsprachige Modell `paraphrase-multilingual-MiniLM-L12-v2` (lokal, ~220 MB,
wird beim ersten Lauf geladen). Schwellen und Gewichte stehen in `config/gewichte.yaml`, Themen und
Mitreden-Faktor in `config/topics.yaml`.

## Nichts soll fehlen: Google News als Maßstab und Netz

Der Sammellauf speichert stündlich die ersten 15 Einträge von 18 Google-News-Listen (Schlagzeilen und Rubriken
für Deutschland und die USA, Schlagzeilen UK), je mit den verwandten Schlagzeilen anderer Redaktionen.

- **Aufmerksamkeit:** Steht eine Geschichte dort, steigt ihre Wichtigkeit, und sie fällt nie weg (mindestens Kurz & knapp).
- **Netz:** Findet sich zu einem Google-Eintrag keine eigene Geschichte, wird der Eintrag selbst zur Geschichte
  (nur Schlagzeilen und Link, deshalb höchstens Kurz & knapp).
- **Maßstab:** Die Kontrolle misst daran, ob alles drin ist und wie viel davon aus eigenen Quellen kommt.

## Karten bauen (Phase 2)

- **Neu:** Gemini schreibt die Karte aus 3–8 Quellen (Top-Storys mit dem besten Modell). Erst eine Faktenliste,
  dann die Karte daraus (PLAN.md 8.0, B7).
- **Bekannt:** Die Karte bleibt. Kommen mindestens zwei neue Meldungen dazu, entscheidet Gemini: nichts Neues,
  ergänzen, korrigieren oder ersetzen.
- **Kurz & knapp:** eine Zeile je Geschichte, 20 Geschichten pro Anfrage.
- **Prüfen:** Zahlen prüft der Code, jede Aussage prüft Groq (anderes Unternehmen). Nicht Belegtes fliegt raus.
- **Bilder:** Artikelbild nur verlinkt, sonst freies Bild von Wikimedia.
- Modelle, Reihenfolge und Grenzen: `config/ki.config.json`; Prompts: `prompts/`. Verbrauch je Modell und Tag
  steht im privaten Repo unter `metriken/ki-verbrauch.json`.
- Probebau ohne Zeitplan: `config/probebau.txt` ändern und hochladen.

## Kontrolle

Prüft dreimal täglich, ob die Pipeline alles Relevante aufnimmt, und zeigt eine Ampel:

- **Sammeln:** letzter Lauf frisch? Feeds oder Google-Listen ausgefallen? Quellen ohne neue Meldungen?
- **Abdeckung:** die ersten 10 jeder Google-Liste, je Gruppe (kern = Deutschland, rubrik = deutsche Rubriken,
  global = USA und UK). Für jede Meldung: abgedeckt (eigene Quellen), nur über Google, aussortiert, unsicher, fehlt.
  Ziel über 95 %; in den Deutschland-Listen sollen mindestens 90 % aus eigenen Quellen kommen.
- **Abgleich:** handbewertete Prüfsets (im privaten Repo unter `pruefset/`) fangen Rückschritte ab.

```bash
.venv/Scripts/python -m pipeline.kontrolle --daten <checkout von rohdaten> --nur-lesen
```

## Aufbau

```
config/sources.yaml   Quellen, Lager-Einordnung (Vorschlag, wird geprüft), Erkennungsregeln
config/topics.yaml    Themen, Mitreden-Faktor, Schlüsselwörter
config/gewichte.yaml  Gewichte und Schwellen für Dubletten, Google-Abgleich und Wichtigkeit
config/kontrolle.yaml Vergleichslisten und Ampel-Grenzen der Kontrolle
pipeline/             Python-Code (collect.py = Sammeln, cluster.py = Dubletten, abgleich.py = Google News Top,
                      score.py = Wichtigkeit, werkbank.py)
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
- Keine Bilder in dieses Repo. Von Artikelbildern speichert die Pipeline nur die Adresse (im privaten Repo),
  die App lädt sie beim Anzeigen direkt beim Verlag. Sonst freie Bilder (Wikimedia).
- Keine Schlüssel in Dateien. Lokal nur in `.env` (steht in `.gitignore`).
