"""Tests für die Übergabe der Ausgabe an die App. Alle Inhalte sind ausgedacht."""

import unittest

from pipeline.veroeffentlichen import fuer_app


class FuerApp(unittest.TestCase):
    def test_nur_angezeigte_felder(self):
        ausgabe = {"ausgabe": "2026-11-04-20", "hinweis": "Testkarten", "themen": {}, "stand_text": "x",
                   "ausgabe_label": "Ausgabe 20:00", "karten": [{
                       "id": "k1", "format": "karte", "thema": "politik", "region": "deutschland", "titel": "T",
                       "begruendung": ["intern"], "pruefung": {"ergebnis": "ok"}, "punktzahl": 7.1, "story_id": "s1",
                       "faktenbox": [], "erklaerung": None,
                       "bild": {"art": "verlinkt", "url": "https://bild.example/a.jpg", "alt": "A", "quelle": "https://x.example"},
                       "quellen": [{"name": "Blatt", "lager": "mitte", "url": "https://x.example/1", "intern": 1}]}]}
        k = fuer_app(ausgabe)["karten"][0]
        for intern in ("begruendung", "pruefung", "punktzahl", "story_id", "faktenbox", "erklaerung"):
            self.assertNotIn(intern, k)
        self.assertEqual(k["bild"], {"url": "https://bild.example/a.jpg", "alt": "A", "art": "verlinkt"})
        self.assertEqual(k["quellen"], [{"name": "Blatt", "lager": "mitte", "url": "https://x.example/1"}])
        self.assertEqual((k["labels"], k["lager_mix"]), ([], {}))


if __name__ == "__main__":
    unittest.main()
