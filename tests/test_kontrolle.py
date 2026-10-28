"""Tests für die Ampel der Kontrolle."""

import unittest
from pathlib import Path

import yaml

from pipeline.kontrolle import ampel

K = yaml.safe_load((Path(__file__).parent.parent / "config" / "kontrolle.yaml").read_text(encoding="utf-8"))


def bericht(quote: float, alter_h: float = 0.5, falsch: int = 0) -> dict:
    return {"sammeln": {"alter_h": alter_h, "ausgefallen": [], "ohne_neue_meldungen": []},
            "abdeckung": {"kern": {"quote": quote}},
            "pruefsets": {"richtig": 96, "falsch_zugeordnet": falsch, "uebersehen": 2}}


class Ampel(unittest.TestCase):
    def test_gruen(self):
        self.assertEqual(ampel(bericht(0.97), K), ("grün", []))

    def test_unter_ziel_gelb_unter_warnung_rot(self):
        self.assertEqual(ampel(bericht(0.90), K)[0], "gelb")
        self.assertEqual(ampel(bericht(0.70), K)[0], "rot")

    def test_alter_sammellauf_rot(self):
        farbe, gruende = ampel(bericht(0.97, alter_h=5), K)
        self.assertEqual(farbe, "rot")
        self.assertIn("letzter Sammellauf vor 5 Std", gruende)

    def test_abgleich_verschlechtert(self):
        self.assertEqual(ampel(bericht(0.97, falsch=2), K)[0], "grün")  # Stand der Eichung
        self.assertEqual(ampel(bericht(0.97, falsch=6), K)[0], "gelb")


if __name__ == "__main__":
    unittest.main()
