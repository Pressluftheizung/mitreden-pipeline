"""Tests für die Schutzprüfung des öffentlichen Repos (tools/pruefe_repo.py)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from pruefe_repo import pruefe  # noqa: E402


class Schutz(unittest.TestCase):
    def test_code_ist_erlaubt(self):
        self.assertEqual(pruefe("pipeline/score.py", b"x = 1\n"), [])
        self.assertEqual(pruefe(".env.example", b"GEMINI_API_KEY=\n"), [])

    def test_env_dateien_sind_verboten(self):
        for pfad in ("config/.env", ".env", "pipeline/.env.local"):
            self.assertTrue(pruefe(pfad, b"TOKEN=geheim\n"), pfad)

    def test_werkbank_ausgaben_sind_verboten(self):
        self.assertTrue(pruefe("werkbank/rangliste.json", b"{}"))
        self.assertTrue(pruefe("pipeline/werkbank.html", b"<html></html>"))
        self.assertEqual(pruefe("schemas/karte.json", b"{}"), [])

    def test_daten_und_schluessel(self):
        self.assertTrue(pruefe("teaser/2026-10-28.jsonl", b"{}"))
        self.assertTrue(pruefe("pipeline/x.py", b"k = 'AIza" + b"A" * 35 + b"'"))
        self.assertTrue(pruefe("tests/fixtures/feed.xml", b"<rss></rss>"))  # ohne Kennzeichen


if __name__ == "__main__":
    unittest.main()
