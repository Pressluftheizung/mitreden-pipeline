#!/usr/bin/env bash
# Ergebnis eines Laufs in den privaten Branch rohdaten speichern (aufgerufen im Ordner daten/).
# Aufruf: bash ../tools/rohdaten_speichern.sh "Ausgabe"
# Wurden während des Laufs Wissenskarten von Hand gespeichert, kollidiert wissen/wissen.json (Lauf vom 14.11.
# verloren): dann unsere Seite nehmen und beide Wissens-Stände vereinen (tools/wissen_vereinen.py).
set -u
git config user.name "mitreden-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A metriken testkarten stories
if [ -d wissen ]; then git add -A wissen; fi
if git diff --cached --quiet; then
  exit 0
fi
git commit -q -m "$1 $(date -u +%Y-%m-%dT%H:%MZ)"
eigen="$RUNNER_TEMP/wissen-eigen.json"; fremd="$RUNNER_TEMP/wissen-fremd.json"
cp wissen/wissen.json "$eigen" 2>/dev/null || true
for versuch in 1 2 3; do
  git push -q origin HEAD:rohdaten && exit 0
  if ! git pull -q --rebase origin rohdaten; then
    git rebase --abort
    git pull -q --rebase -X theirs origin rohdaten || { git rebase --abort; exit 1; }
  fi
  if [ -f "$eigen" ] && git show FETCH_HEAD:wissen/wissen.json > "$fremd" 2>/dev/null; then
    python ../tools/wissen_vereinen.py "$fremd" "$eigen" wissen/wissen.json
    git add wissen/wissen.json
    git diff --cached --quiet || git commit -q --amend --no-edit
  fi
done
exit 1
