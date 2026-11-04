"""Tests für Großthemen und Gesprächswert (pipeline/themen.py) und die Altersgrenze. Alles ausgedacht."""

import json
import unittest
from types import SimpleNamespace

from pipeline.score import Bewertung
from pipeline.themen import buendeln, lies_antwort

CFG = {"max_geschichten": 200, "min_teile": 3, "max_teile": 12, "faktor": {1: 0.6, 2: 1.0, 3: 1.35}}


def bewertung(titel: str, w: float, fmt: str, mitglieder: list[int]) -> Bewertung:
    return Bewertung(wichtigkeit=w, format=fmt, titel=titel, thema="politik", unterthema=None, region="welt",
                     teile={}, faktoren={}, begruendung=[], quellen=["test"], mitglieder=mitglieder)


class ScheinKI:
    def __init__(self, antwort):
        self.antwort, self.fragen = antwort, []

    def frage(self, aufgabe, nutzer, pruefe):
        self.fragen.append((aufgabe, json.loads(nutzer)))
        pruefe(self.antwort)
        return self.antwort, "test"


class Bewerter:
    def formate_vergeben(self, bs):
        bs.sort(key=lambda b: -b.wichtigkeit)
        for b in bs:
            b.format = "karte" if b.wichtigkeit >= 5 else "kurz" if b.wichtigkeit >= 3 else "weg"


def stand(geschichten, werte):
    bs = [bewertung(f"G{n}", w, "karte" if w >= 5 else "kurz", g) for n, (g, w) in enumerate(zip(geschichten, werte))]
    return SimpleNamespace(eigene_geschichten=[list(g) for g in geschichten], in_reihenfolge=bs, sortiert=list(bs))


class Lesen(unittest.TestCase):
    def test_unbrauchbares_faellt_weg(self):
        themen, werte = lies_antwort({"grossthemen": [{"name": "Wahl", "nr": [1, 2, 2, 9]}, {"name": "X", "nr": [3, 4, 5]}],
                                      "gespraech": [3, 7, "x", 1]}, 5, CFG)
        self.assertEqual(themen, [("X", [2, 3, 4])])  # erstes Thema hat nur 2 gültige Nummern
        self.assertEqual(werte, [3, 2, 2, 1, 2])


    def test_nummern_als_text_und_werte_als_gruppen(self):
        themen, werte = lies_antwort({"grossthemen": [{"name": "Wahl", "nr": ["1", "s2", 3.0]}],
                                      "gespraech": {"3": [1], "1": ["4"], "7": [2]}}, 4, CFG)
        self.assertEqual(themen, [("Wahl", [0, 1, 2])])
        self.assertEqual(werte, [3, 2, 2, 1])


class Buendeln(unittest.TestCase):
    def test_grossthema_wird_eine_geschichte(self):
        geschichten = [[0], [1], [2], [3]]
        st = stand(geschichten, [8, 6, 4, 5])

        def auswerten(neu):
            bs = [bewertung(f"N{n}", 10.0 - n, "karte", g) for n, g in enumerate(neu)]
            return SimpleNamespace(eigene_geschichten=neu, in_reihenfolge=bs, sortiert=list(bs))

        # Reihenfolge für die KI nach Wichtigkeit: G0, G1, G3, G2
        ki = ScheinKI({"grossthemen": [{"name": "Wahlabend", "nr": [1, 2, 4]}], "gespraech": [3, 2, 1, 2]})
        neu = buendeln(st, ki, CFG, auswerten, Bewerter())
        self.assertEqual(neu.in_reihenfolge[0].mitglieder, [0, 1, 2])
        self.assertEqual(neu.in_reihenfolge[0].sammel["name"], "Wahlabend")
        self.assertEqual(neu.in_reihenfolge[0].sammel["teile"], ["G0", "G1", "G2"])  # wichtigste zuerst
        self.assertEqual(neu.in_reihenfolge[0].gespraech["wert"], 3)  # bester Wert der Teile
        self.assertEqual(neu.in_reihenfolge[1].wichtigkeit, round(9.0 * 0.6, 3))  # G3, Gesprächswert 1
        self.assertEqual(neu.themen["grossthemen"], [{"name": "Wahlabend", "teile": 3}])

    def test_ohne_thema_nur_gespraechswert(self):
        st = stand([[0], [1]], [6, 4])
        ki = ScheinKI({"grossthemen": [], "gespraech": [1, 3]})
        neu = buendeln(st, ki, CFG, lambda g: self.fail("darf nicht neu auswerten"), Bewerter())
        self.assertEqual([b.format for b in neu.in_reihenfolge], ["kurz", "karte"])  # 3,6 und 5,4

    def test_nur_kurzmeldungen_ergeben_kein_grossthema(self):
        st = stand([[0], [1], [2]], [4, 3.5, 3])
        ki = ScheinKI({"grossthemen": [{"name": "Klein", "nr": [1, 2, 3]}], "gespraech": [2, 2, 2]})
        neu = buendeln(st, ki, CFG, lambda g: self.fail("darf nicht neu auswerten"), Bewerter())
        self.assertEqual(neu.themen["grossthemen"], [])


class Altersgrenze(unittest.TestCase):
    def test_alte_geschichte_faellt_weg(self):
        from pathlib import Path
        from pipeline.score import Bewerter as Echt
        config = Path(__file__).resolve().parent.parent / "config"
        bewerter = Echt(config / "gewichte.yaml", config / "topics.yaml")
        alt, frisch = bewertung("alt", 20, "", [0]), bewertung("frisch", 6, "", [1])
        alt.gespraech, frisch.gespraech = {"alter_h": 60}, {"alter_h": 3}
        bs = [alt, frisch]
        bewerter.formate_vergeben(bs)
        self.assertEqual((alt.format, frisch.format), ("weg", "top"))


if __name__ == "__main__":
    unittest.main()
