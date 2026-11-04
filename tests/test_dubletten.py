"""Tests für den Dubletten-Schritt mit KI (pipeline/dubletten.py). Alle Meldungen sind ausgedacht."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from pipeline.cluster import Meldung, Paar
from pipeline.dubletten import Lauf, Speicher, auswahl, lies_gruppen
from pipeline.quellen import Quelle
from pipeline.text import inhaltswoerter, produkte

JETZT = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
CFG = {"max_anfragen": 8, "speicher_tage": 2, "aufteilen_max": 40, "aufteilen_pro_anfrage": 8, "aufteilen_titel": 20,
       "einheitlich": 0.5, "vorauswahl": 0.4, "kandidat": 0.6, "woerter_bonus": 0.25, "woerter_voll": 12.0,
       "selten": 0.02, "max_paare": 120, "paare_pro_anfrage": 40}
Q = Quelle(id="test", name="Testblatt", url="https://test.example/feed", sprache="de", region="de", lager="mitte",
           art="zeitung", volltext_erlaubt=False, bezahlschranke="nein")


def meldung(nr: int, titel: str) -> Meldung:
    return Meldung({"id": f"t{nr}", "titel": titel, "teaser": ""}, Q, titel, inhaltswoerter(titel), produkte(titel), JETZT)


def vektor(*richtung: float) -> np.ndarray:
    v = np.zeros(8, dtype=np.float32)
    v[:len(richtung)] = richtung
    return v / np.linalg.norm(v)


class ScheinKI:
    """Antwortet nach Vorgabe und merkt sich, was gefragt wurde."""

    def __init__(self, antworten: dict):
        self.antworten, self.fragen = antworten, []

    def frage(self, aufgabe, nutzer, pruefe):
        self.fragen.append((aufgabe, json.loads(nutzer)))
        antwort = self.antworten[aufgabe](json.loads(nutzer))
        pruefe(antwort)
        return antwort, "test"


class Gruppen(unittest.TestCase):
    def test_fehlende_und_doppelte_nummern(self):
        self.assertEqual(lies_gruppen([[1, 2], [2, 3]], 4), [[0, 1, 3], [2]])
        self.assertIsNone(lies_gruppen("alles eins", 3))
        self.assertIsNone(lies_gruppen([[9]], 3))

    def test_auswahl_zeigt_den_ausreisser(self):
        ms = [meldung(i, t) for i, t in enumerate(["Sturm A", "Sturm B", "Sturm C", "Skatturnier"])]
        v = np.array([vektor(1, 0.1), vektor(1, 0.2), vektor(1, 0.15), vektor(0, 1)])
        self.assertEqual(auswahl([0, 1, 2, 3], ms, v, 2)[1], 3)


class Aufteilen(unittest.TestCase):
    def test_fremde_meldung_wird_abgetrennt(self):
        ms = [meldung(0, "Pokal: Wolfsburg siegt im Halbfinale"), meldung(1, "Frauen-Pokal: Wolfsburg weiter"),
              meldung(2, "Wolfsburgerinnen im Finale"), meldung(3, "Bundesliga: Gladbach verliert in Mainz"),
              meldung(4, "Pokal: Wolfsburg siegt im Halbfinale")]  # gleicher Titel wie 0: wird nicht doppelt gezeigt
        v = np.array([vektor(1, 0.3), vektor(1, 0.35), vektor(1, 0.25), vektor(0.6, 1), vektor(1, 0.3)])

        def antwort(daten):
            g = daten["geschichten"][0]
            fremd = [s["nr"] for s in g["schlagzeilen"] if "Gladbach" in s["titel"]]
            return {"geschichten": [{"nr": g["nr"], "gruppen": [[s["nr"] for s in g["schlagzeilen"] if s["nr"] not in fremd], fremd]}]}

        ki = ScheinKI({"aufteilen": antwort})
        lauf = Lauf(ki, CFG, Speicher(None, JETZT))
        neu, getrennt = lauf.aufteilen([[0, 1, 2, 3, 4]], ["karte"], ms, v)
        self.assertEqual(neu, [[0, 1, 2, 4], [3]])
        self.assertEqual(getrennt, [{0, 1}])
        self.assertEqual(len(ki.fragen[0][1]["geschichten"][0]["schlagzeilen"]), 4)

    def test_speicher_spart_die_zweite_frage(self):
        ms = [meldung(i, f"Gipfel in Berlin, Teil {i}") for i in range(4)]
        v = np.array([vektor(1, i / 10) for i in range(4)])
        with tempfile.TemporaryDirectory() as tmp:
            datei = Path(tmp) / "d.json"
            ki = ScheinKI({"aufteilen": lambda d: {"geschichten": [{"nr": 0, "gruppen": [[1, 2, 3, 4]]}]}})
            sp = Speicher(datei, JETZT)
            Lauf(ki, CFG, sp).aufteilen([[0, 1, 2, 3]], ["top"], ms, v)
            sp.speichere()
            ki2 = ScheinKI({})
            neu, _ = Lauf(ki2, CFG, Speicher(datei, JETZT)).aufteilen([[0, 1, 2, 3]], ["top"], ms, v)
            self.assertEqual((neu, ki2.fragen), ([[0, 1, 2, 3]], []))

    def test_weggefallene_geschichten_kosten_nichts(self):
        ms = [meldung(i, f"Meldung {i}") for i in range(4)]
        v = np.array([vektor(1, i) for i in range(4)])
        ki = ScheinKI({})
        Lauf(ki, CFG, Speicher(None, JETZT)).aufteilen([[0, 1, 2, 3]], ["weg"], ms, v)
        self.assertEqual(ki.fragen, [])


class Zusammenlegen(unittest.TestCase):
    def setUp(self):
        self.ms = [meldung(0, "Waffenruhe in Oleschky beendet"), meldung(1, "Evakuierung aus Oleschky abgeschlossen"),
                   meldung(2, "Neues Smartphone Pixel 10 Pro vorgestellt"), meldung(3, "Pixel Watch 4 vorgestellt"),
                   meldung(4, "Bahn streicht Züge wegen Sturm")]
        self.v = np.array([vektor(1, 0.2), vektor(1, 0.3), vektor(0, 1, 0.1), vektor(0, 1, 0.12), vektor(0, 0, 1)])

    def test_gleiche_werden_eine_geschichte(self):
        ki = ScheinKI({"dubletten": lambda d: {"paare": [{"nr": p["nr"], "gleich": "Oleschky" in str(p)} for p in d["paare"]]}})
        lauf = Lauf(ki, CFG, Speicher(None, JETZT))
        neu = lauf.zusammenlegen([[0], [1], [2], [3], [4]], ["karte", "kurz", "karte", "kurz", "kurz"], self.ms, self.v, [], [])
        self.assertIn([0, 1], neu)
        self.assertEqual(len(neu), 4)
        gefragt = [p for _, d in ki.fragen for p in d["paare"]]
        self.assertFalse(any("Watch" in str(p) for p in gefragt))  # Pixel-Regel: gar nicht erst gefragt
        self.assertEqual(lauf.bericht["zusammenlegen"]["gleich"], 1)

    def test_getrennte_teile_bleiben_getrennt(self):
        ki = ScheinKI({"dubletten": lambda d: {"paare": [{"nr": p["nr"], "gleich": True} for p in d["paare"]]}})
        neu = Lauf(ki, CFG, Speicher(None, JETZT)).zusammenlegen(
            [[0], [1], [4]], ["karte", "kurz", "kurz"], self.ms, self.v, [], [{0, 1}])
        self.assertEqual(len(neu), 3)

    def test_grauzone_wird_gefragt(self):
        v = np.array([vektor(1, 0), vektor(0, 1), vektor(0, 0, 1), vektor(0, 0, 0, 1), vektor(0, 0, 0, 0, 1)])
        ki = ScheinKI({"dubletten": lambda d: {"paare": [{"nr": 1, "gleich": True}]}})
        neu = Lauf(ki, CFG, Speicher(None, JETZT)).zusammenlegen(
            [[0], [4]], ["karte", "kurz"], self.ms, v, [Paar(0, 4, 0.5, 0.58, [], "grauzone")], [])
        self.assertEqual(neu, [[0, 4]])

    def test_obergrenze_der_anfragen(self):
        ki = ScheinKI({"dubletten": lambda d: {"paare": []}})
        lauf = Lauf(ki, {**CFG, "max_anfragen": 0}, Speicher(None, JETZT))
        neu = lauf.zusammenlegen([[0], [1]], ["karte", "kurz"], self.ms, self.v, [], [])
        self.assertEqual((len(neu), ki.fragen), (2, []))


if __name__ == "__main__":
    unittest.main()
