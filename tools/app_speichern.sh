#!/usr/bin/env bash
# Karten für die App in den privaten Branch main speichern (aufgerufen im Ordner app/).
# Die Dateien entstehen vollständig aus daten/, bei Konflikten gilt deshalb unsere Fassung.
set -u
git config user.name "mitreden-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add karten/karten.json
if [ -f karten/wissen.json ]; then git add karten/wissen.json; fi
if git diff --cached --quiet; then
  exit 0
fi
git commit -q -m "$1 $(date -u +%Y-%m-%dT%H:%MZ)"
for versuch in 1 2 3; do
  git push -q origin HEAD:main && exit 0
  git pull -q --rebase -X theirs origin main || { git rebase --abort; exit 1; }
done
exit 1
