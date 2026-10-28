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

from pipeline.abgleich import GoogleTreffer
from pipeline.cluster import Schwellen, baue_meldungen, bewerte_paare, bilde_geschichten
from pipeline.quellen import Quelle
from pipeline.score import Bewerter, Signale, Themen
from pipeline.text import Seltenheit, inhaltswoerter, produkte, produkte_widersprechen, woerter

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

    def test_andere_modellnummer_widerspricht(self):
        self.assertTrue(produkte_widersprechen(produkte("Pixel 9 im Test"), produkte("Pixel 9a im Test")))
        self.assertTrue(produkte_widersprechen(produkte("Tesla Model 3"), produkte("Model Y")))
        self.assertTrue(produkte_widersprechen(produkte("Neue PS5"), produkte("Sony zeigt PS6")))

    def test_schreibweisen(self):
        self.assertEqual(produkte("Pixel-10-Pro und GPT-5"), {"pixel 10 pro", "gpt 5"})
        self.assertEqual(produkte("Google gives Gemini a new voice"), set())  # "a" ist hier ein Artikel


class Themenwoerter(unittest.TestCase):
    def test_wortanfang(self):
        self.assertEqual(Themen.treffer(["ice-"], woerter("Zimbabwe's vice-president freed")), 0)
        self.assertEqual(Themen.treffer(["ice-"], woerter("Neue ICE-Strecke eröffnet")), 1)
        self.assertEqual(Themen.treffer(["us-"], woerter("Virus-Welle und Bus-Streik")), 0)

    def test_ganzes_wort_und_wortfolgen(self):
        self.assertEqual(Themen.treffer(["=fed"], woerter("Federer gewinnt")), 0)
        self.assertEqual(Themen.treffer(["=fed"], woerter("Fed senkt Zinsen")), 1)
        self.assertEqual(Themen.treffer(["trauer um"], woerter("Stars trauern um Tänzer")), 1)
        self.assertEqual(Themen.treffer(["frontstadt"], woerter("EU-Grenzbehörde Frontex")), 0)

    def test_bindestrich_teile_zaehlen_einzeln(self):
        self.assertTrue({"steinmeier", "nachfolge"} <= inhaltswoerter("Steinmeier-Nachfolge offen"))


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

    def test_google_rubrik_zaehlt_fast_voll(self):
        # Google sortiert nach Rubriken: Platz 25 ist "Unterhaltung", nicht "unwichtig".
        m = baue_meldungen([teaser(1, "alpha", "Sängerin sagt Tour ab")], QUELLEN)
        treffer = GoogleTreffer("google_news_top_de", 25, "Sängerin sagt Tour ab", 0, 0, 1.3, 1.0)
        b = self.bewerter.bewerte([0], m, vektoren(), self.leer, JETZT, [treffer])
        self.assertAlmostEqual(b.teile["aufmerksamkeit"], 1.5 * 0.7)
        self.assertIn("Google News DE Schlagzeilen Platz 25", b.begruendung)
        self.assertEqual(b.google_listen, 1)
        b.wichtigkeit = 0.1  # selbst ganz unten: was Google zeigt, fällt nicht weg
        self.bewerter.formate_vergeben([b])
        self.assertEqual(b.format, "kurz")

    def test_allgemeiner_trend_zaehlt_nur_im_titel(self):
        m = baue_meldungen([teaser(1, "alpha", "Neue Studie zu Krebs", "Forscher über cancer research")], QUELLEN)
        trend = {"signal": "google_trends_us", "titel": "cancer", "suchmenge": "200+"}
        b = self.bewerter.bewerte([0], m, vektoren(), Signale([], [trend]), JETZT, seltenheit=Seltenheit([x.woerter for x in m]))
        self.assertEqual(b.teile["aufmerksamkeit"], 0)

    def test_platzierung_nur_aus_dem_letzten_lauf(self):
        alt = teaser(1, "tagesschau", "Gipfel in Genf", position=1)
        alt["zuletzt_gesehen"] = (JETZT - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        m = baue_meldungen([alt], {"tagesschau": quelle("tagesschau")})
        self.assertEqual(self.bewerter.bewerte([0], m, vektoren(), self.leer, JETZT, stand=JETZT).teile["platzierung"], 0)

    def test_feeds_eines_hauses_zaehlen_einmal(self):
        ard = {q: replace(quelle(q), haus="ard") for q in ("tagesschau", "sportschau")}
        m = baue_meldungen([teaser(1, "tagesschau", "Gipfel in Genf endet"),
                            teaser(2, "sportschau", "Genfer Gipfel ohne Ergebnis")], ard)
        b = self.bewerter.bewerte([0, 1], m, vektoren(0.9), self.leer, JETZT)
        self.assertAlmostEqual(b.teile["breite"], 1.0)

    def test_formate(self):
        m = baue_meldungen([teaser(i, "alpha", f"Meldung {i}") for i in range(3)], QUELLEN)
        bewertungen = [self.bewerter.bewerte([i], m, vektoren(0, 0), self.leer, JETZT) for i in range(3)]
        for b, wert in zip(bewertungen, (5.0, 2.0, 0.5)):
            b.wichtigkeit = wert
        self.bewerter.formate_vergeben(bewertungen)
        self.assertEqual([b.format for b in bewertungen], ["top", "kurz", "weg"])


if __name__ == "__main__":
    unittest.main()
