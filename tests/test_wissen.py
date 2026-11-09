import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from pipeline import wissen

QUELLE = ("Der Deutsche Bundestag ist das Parlament der Bundesrepublik Deutschland mit Sitz in Berlin. "
          "Er hat seit der Wahl 2025 genau 630 Abgeordnete. Der Mount Everest ist 8.849 Meter hoch. " * 5)


class Wissen(unittest.TestCase):
    def test_lehrplan_reihenfolge_und_verweise(self):
        themen = wissen.lehrplan(Path("config"))
        self.assertEqual([t["stufe"] for t in themen], sorted(t["stufe"] for t in themen))  # erst Basis
        ids = {t["id"] for t in themen}
        for t in themen:
            self.assertTrue(set(t["braucht"]) <= ids, t["id"])
        self.assertEqual(themen[0]["id"], "deutschland.grundgesetz")

    def test_zahlen(self):
        self.assertEqual(wissen.zahlen("8.849 Meter, 630 Abgeordnete, 2,5 Prozent, im Jahr 1949."), {8849.0, 630.0, 2.5, 1949.0})

    def test_fragen_bereinigen(self):
        roh = [
            {"art": "schaetzen", "frage": "Wie viele Abgeordnete?", "wert": 630, "einheit": "Abgeordnete", "toleranz": 10, "toleranz_art": "prozent"},
            {"art": "schaetzen", "frage": "Erfunden?", "wert": 712, "einheit": "x"},  # steht nicht in der Quelle
            {"art": "auswahl", "frage": "Wo sitzt er?", "optionen": ["Bonn", "Berlin", "Köln"], "richtig": 1},
            {"art": "auswahl", "frage": "Kaputt", "optionen": ["A", "B"], "richtig": 5},
            {"art": "wahr_falsch", "frage": "Der Everest ist 8.849 m hoch.", "richtig": True},
        ]
        f = wissen.fragen_bereinigen(roh, QUELLE)
        self.assertEqual([x["art"] for x in f], ["schaetzen", "auswahl", "wahr_falsch"])
        self.assertEqual(f[0]["toleranz_art"], "prozent")

    def test_bilder_und_englische_zahlen(self):
        kandidaten = [{"url": "https://upload.wikimedia.org/a.gif", "seite": "https://commons.wikimedia.org/wiki/File:A.gif"},
                      {"url": "https://upload.wikimedia.org/b.jpg", "seite": "https://commons.wikimedia.org/wiki/File:B.jpg"}]
        b = wissen.bilder_bereinigen([{"nr": 1, "punkt": 1, "unterschrift": "Die vier Takte."},
                                      {"nr": 1, "punkt": 0, "unterschrift": "doppelt"},
                                      {"nr": 7, "punkt": 0, "unterschrift": "gibt es nicht"},
                                      {"nr": 2, "punkt": 9, "unterschrift": "Punkt fehlt"}], kandidaten, 3)
        self.assertEqual([(x["url"][-5:], x["punkt"]) for x in b], [("a.gif", 1), ("b.jpg", None)])
        f = wissen.fragen_bereinigen([{"art": "schaetzen", "frage": "Wie hoch?", "wert": 8849}], "kein Wert hier " * 40,
                                     "Mount Everest is 8,849 metres high.")
        self.assertEqual(len(f), 1)  # Zahl steht nur in der englischen Quelle

    def test_pruefung_anwenden(self):
        karte = {"satz": "s", "punkte": ["a", "b", "c"], "mini": ["m"], "fragen": [{"art": "x"}, {"art": "y"}]}
        neu = wissen.pruefung_anwenden(karte, {"fragen_falsch": [0], "punkte_falsch": [2], "mini_falsch": [0]})
        self.assertEqual((neu["fragen"], neu["punkte"], neu["mini"]), ([{"art": "y"}], ["a", "b"], []))
        self.assertIsNone(wissen.pruefung_anwenden(karte, {"fragen_falsch": [0, 1]}))
        self.assertIsNone(wissen.pruefung_anwenden(karte, {"fragen_falsch": [], "satz_falsch": True}))

    def test_baue_speichert_nur_gepruefte(self):
        class FalscheKI:
            def frage(self, aufgabe, nutzer, pruefe=None):
                daten = json.loads(nutzer)
                if aufgabe == "wissen":
                    antwort = {"karten": [{"thema": t["thema"], "titel": t["titel"], "satz": "Ein Satz.",
                                           "punkte": ["Eins.", "Zwei.", "Drei."], "mini": ["Nett."],
                                           "fragen": [{"art": "schaetzen", "frage": "Wie viele?", "wert": 630,
                                                       "toleranz": 10, "toleranz_art": "prozent"}]}
                                          for t in daten["themen"]]}
                else:
                    antwort = {"satz_falsch": False, "punkte_falsch": [], "mini_falsch": [], "fragen_falsch": []}
                pruefe(antwort)
                return antwort, "test"

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(wissen, "quelle", return_value={"titel": "T", "url": "https://de.wikipedia.org/wiki/T",
                                                                  "text": QUELLE, "bild": None}):
            jetzt = datetime(2026, 11, 9, tzinfo=timezone.utc)
            stat = wissen.baue(Path(tmp), Path("config"), FalscheKI(), jetzt, 3)
            self.assertEqual(stat["gebaut"], 3)
            db = json.loads((Path(tmp) / "wissen" / "wissen.json").read_text(encoding="utf-8"))
            self.assertIn("deutschland.grundgesetz", db["karten"])
            # zweiter Lauf baut die nächsten, nicht dieselben noch einmal
            wissen.baue(Path(tmp), Path("config"), FalscheKI(), jetzt, 3)
            db = json.loads((Path(tmp) / "wissen" / "wissen.json").read_text(encoding="utf-8"))
            self.assertEqual(len(db["karten"]), 6)
            app = wissen.fuer_app(db)
            self.assertNotIn("_ki", app["karten"][0])


if __name__ == "__main__":
    unittest.main()
