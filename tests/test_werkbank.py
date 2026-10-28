"""Tests für Dubletten (Algorithmus A) und Wichtigkeit (Algorithmus B).

Statt des Sprachmodells bekommen die Tests selbst gebaute Vektoren mit bekannter Ähnlichkeit.
So laufen sie schnell und ohne Download. Alle Meldungen sind ausgedacht.
"""

import math
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from pipeline.cluster import Schwellen, baue_meldungen, bewerte_paare, bilde_geschichten
from pipeline.quellen import Quelle
from pipeline.score import Bewerter, Signale
from pipeline.text import produkte, produkte_widersprechen

CONFIG = Path(__file__).parent.parent / "config"
JETZT = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def quelle(id_: str, sprache: str = "de", lager: str = "mitte") -> Quelle:
    return Quelle(id=id_, name=id_.capitalize(), url=f"https://{id_}.example/feed", sprache=sprache,
                  region="deutschland", lager=lager, art="zeitung", volltext_erlaubt=False, bezahlschranke="nein")


QUELLEN = {q.id: q for q in [quelle("alpha"), quelle("beta", lager="links"), quelle("gamma", lager="rechts"),
                             quelle("delta", "en", "international")]}


def teaser(n: int, quelle_id: str, titel: str, text: str = "", stunden_alt: float = 1, position: int = 5) -> dict:
    zeit = (JETZT - timedelta(hours=stunden_alt)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": f"t{n}", "quelle": quelle_id, "url": f"https://{quelle_id}.example/{n}", "titel": titel,
            "teaser": text, "veroeffentlicht": zeit, "gesammelt": zeit, "zuletzt_gesehen": zeit,
            "ist_meinung": False, "bezahlschranke": False, "position_im_feed": position}


def vektoren(*aehnlichkeit_zur_ersten: float) -> np.ndarray:
    """Einheitsvektoren, deren Ähnlichkeit zum ersten Vektor genau die angegebenen Werte hat."""
    n = len(aehnlichkeit_zur_ersten) + 1
    v = np.zeros((n, n), dtype=np.float32)
    v[0, 0] = 1
    for i, s in enumerate(aehnlichkeit_zur_ersten, start=1):
        v[i, 0] = s
        v[i, i] = math.sqrt(max(0.0, 1 - s * s))
    return v


class Produkte(unittest.TestCase):
    def test_pixel_regel(self):
        a, b = produkte("Google stellt Pixel 10 Pro vor"), produkte("Google stellt Pixel Watch 4 vor")
        self.assertTrue(produkte_widersprechen(a, b))

    def test_gleiches_modell_kein_widerspruch(self):
        self.assertFalse(produkte_widersprechen(produkte("Pixel 10 im Test"), produkte("Pixel 10 Pro im Test")))
        self.assertFalse(produkte_widersprechen(produkte("Pixel 10 Pro"), set()))


class Dubletten(unittest.TestCase):
    def test_aehnlich_und_gemeinsamer_name_ist_zusammen(self):
        m = baue_meldungen([teaser(1, "alpha", "Sturmtief Quirin legt Norden lahm"),
                            teaser(2, "beta", "Quirin: Bahn stoppt Züge im Norden"),
                            teaser(3, "gamma", "Börse schließt fester")], QUELLEN)
        v = vektoren(0.75, 0.1)
        paare = bewerte_paare(m, v, Schwellen())
        urteile = {(p.a, p.b): p.urteil for p in paare}
        self.assertEqual(urteile.get((0, 1)) or urteile.get((1, 0)), "zusammen")
        self.assertFalse(any({p.a, p.b} == {0, 2} for p in paare))

    def test_produkte_bleiben_getrennt(self):
        m = baue_meldungen([teaser(1, "alpha", "Google stellt Pixel 10 Pro vor"),
                            teaser(2, "beta", "Google stellt Pixel Watch 4 vor")], QUELLEN)
        paare = bewerte_paare(m, vektoren(0.95), Schwellen())
        self.assertEqual(paare[0].urteil, "getrennt: Produkt")
        self.assertEqual(bilde_geschichten(m, vektoren(0.95), paare, Schwellen()), [[0], [1]])

    def test_deutsch_englisch_ohne_gemeinsame_woerter(self):
        m = baue_meldungen([teaser(1, "alpha", "Bundestag beschließt Wehrdienst"),
                            teaser(2, "delta", "German parliament passes military service law")], QUELLEN)
        self.assertEqual(bewerte_paare(m, vektoren(0.75), Schwellen())[0].urteil, "zusammen")

    def test_zeitfenster_36_stunden(self):
        m = baue_meldungen([teaser(1, "alpha", "Gipfel in Genf", stunden_alt=1),
                            teaser(2, "beta", "Gipfel in Genf beendet", stunden_alt=40)], QUELLEN)
        self.assertEqual(bewerte_paare(m, vektoren(0.95), Schwellen()), [])

    def test_keine_ketten(self):
        # A ähnelt B, B ähnelt C, aber A und C haben nichts miteinander zu tun.
        m = baue_meldungen([teaser(1, "alpha", "Thema Eins Zwei"), teaser(2, "beta", "Thema Zwei Drei"),
                            teaser(3, "gamma", "Thema Drei Vier")], QUELLEN)
        v = np.array([[1, 0, 0], [0.7, 0.714, 0], [0.0, 1, 0]], dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        s = replace(Schwellen(), sicher=0.69)
        geschichten = bilde_geschichten(m, v, bewerte_paare(m, v, s), s)
        self.assertEqual(len(geschichten), 2)


class Wichtigkeit(unittest.TestCase):
    def setUp(self):
        self.bewerter = Bewerter(CONFIG / "gewichte.yaml", CONFIG / "topics.yaml")
        self.leer = Signale([], [])

    def test_mehr_quellen_und_lager_ist_wichtiger(self):
        m = baue_meldungen([teaser(1, "alpha", "Bundestag streitet über Haushalt"),
                            teaser(2, "beta", "Haushaltsstreit im Bundestag eskaliert"),
                            teaser(3, "gamma", "Koalition uneinig beim Haushalt"),
                            teaser(4, "alpha", "Neuer Rekord beim Kürbiswiegen")], QUELLEN)
        v = vektoren(0.9, 0.9, 0.0)
        breit = self.bewerter.bewerte([0, 1, 2], m, v, self.leer, JETZT)
        schmal = self.bewerter.bewerte([3], m, v, self.leer, JETZT)
        self.assertGreater(breit.wichtigkeit, schmal.wichtigkeit)
        self.assertEqual(breit.thema, "politik")
        self.assertIn("3 Quellen", breit.begruendung[0])

    def test_kopierte_titel_zaehlen_halb(self):
        m = baue_meldungen([teaser(1, "alpha", "Gipfel in Genf endet ohne Einigung"),
                            teaser(2, "beta", "Gipfel in Genf endet ohne Einigung")], QUELLEN)
        b = self.bewerter.bewerte([0, 1], m, vektoren(0.99), self.leer, JETZT)
        self.assertAlmostEqual(b.teile["breite"], round(math.log2(1 + 1.5), 2))

    def test_alte_geschichten_verblassen(self):
        neu = baue_meldungen([teaser(1, "alpha", "Gipfel in Genf", stunden_alt=1)], QUELLEN)
        alt = baue_meldungen([teaser(1, "alpha", "Gipfel in Genf", stunden_alt=30)], QUELLEN)
        v = vektoren()
        self.assertGreater(self.bewerter.bewerte([0], neu, v, self.leer, JETZT).wichtigkeit,
                           self.bewerter.bewerte([0], alt, v, self.leer, JETZT).wichtigkeit)

    def test_formate(self):
        m = baue_meldungen([teaser(i, "alpha", f"Meldung {i}") for i in range(3)], QUELLEN)
        bewertungen = [self.bewerter.bewerte([i], m, vektoren(0, 0), self.leer, JETZT) for i in range(3)]
        for b, wert in zip(bewertungen, (5.0, 2.0, 0.5)):
            b.wichtigkeit = wert
        self.bewerter.formate_vergeben(bewertungen)
        self.assertEqual([b.format for b in bewertungen], ["top", "kurz", "weg"])


if __name__ == "__main__":
    unittest.main()
