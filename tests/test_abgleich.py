"""Tests für den Abgleich mit Google News Top und das Lesen der Signale.

Statt des Sprachmodells bekommen die Tests selbst gebaute Vektoren. Alle Meldungen sind ausgedacht.
"""

import unittest
from datetime import datetime, timezone

import numpy as np

from pipeline.abgleich import gleiche_ab, google_titel
from pipeline.cluster import baue_meldungen
from pipeline.teaser import lies_signal, ohne_quellname
from test_werkbank import QUELLEN, teaser  # unittest discover lädt die Tests als eigene Module

G = {"treffer": 0.85, "sicher_allein": 0.80, "mindest_aehnlichkeit": 0.5, "woerter_bonus": 0.3,
     "woerter_voll": 12, "max_haeufig": 0.02}

# SELBST GESCHRIEBEN: so sieht ein Google-News-Eintrag aus (Titel mit Redaktion, verwandte Schlagzeilen als Liste).
GOOGLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Schlagzeilen</title>
<item><title>Sturmtief Quirin legt Norden lahm - Beispiel-Zeitung - Beispiel-Zeitung</title>
<link>https://news.google.example/1</link><source url="https://beispiel.example">Beispiel-Zeitung</source>
<description>&lt;ol&gt;&lt;li&gt;&lt;a href="https://news.google.example/a"&gt;Sturmtief Quirin legt Norden lahm&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Beispiel-Zeitung&lt;/font&gt;&lt;/li&gt;&lt;li&gt;&lt;a href="https://news.google.example/b"&gt;Quirin: Bahn stoppt Z\xc3\xbcge - Nord - Testblatt&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Testblatt&lt;/font&gt;&lt;/li&gt;&lt;/ol&gt;</description>
</item></channel></rss>"""


def einheit(*indizes: int, dim: int = 5) -> np.ndarray:
    v = np.zeros((len(indizes), dim), dtype=np.float32)
    for zeile, i in enumerate(indizes):
        v[zeile, i] = 1
    return v


class Signal(unittest.TestCase):
    def test_redaktion_und_verwandte_schlagzeilen(self):
        (eintrag,) = lies_signal(GOOGLE_FEED, "google_news_top_de", datetime(2026, 10, 28, tzinfo=timezone.utc))
        self.assertEqual(eintrag["quelle"], "Beispiel-Zeitung")
        self.assertEqual(eintrag["verwandt"], ["Quirin: Bahn stoppt Züge - Nord"])
        self.assertEqual(google_titel(eintrag), ["Sturmtief Quirin legt Norden lahm", "Quirin: Bahn stoppt Züge - Nord"])

    def test_redaktion_mit_bindestrich(self):
        self.assertEqual(ohne_quellname("Rehlinger warnt bei Lanz - T-Online", "T-Online"), "Rehlinger warnt bei Lanz")

    def test_alte_signale_ohne_redaktion(self):
        self.assertEqual(google_titel({"titel": "(S+) Gipfel in Genf - Spiegel"}), ["Gipfel in Genf"])


class Abgleich(unittest.TestCase):
    def setUp(self):
        self.meldungen = baue_meldungen([teaser(1, "alpha", "Gipfel in Genf endet"),
                                         teaser(2, "beta", "Liveblog: alles zur Politik"),
                                         teaser(3, "gamma", "Börse schließt fester")], QUELLEN)
        self.vektoren = einheit(0, 1, 2)
        self.geschichten = [[0], [1], [2]]

    def gleiche(self, titel: list[str], formulierungen: np.ndarray):
        eintrag = {"signal": "google_news_top_de", "rang": 3, "titel": titel[0], "verwandt": titel[1:]}
        (t,) = gleiche_ab([eintrag], formulierungen, self.meldungen, self.vektoren, self.vektoren, self.geschichten, G)
        return t

    def test_hauptschlagzeile_reicht(self):
        t = self.gleiche(["Gipfel in Genf"], einheit(0))
        self.assertEqual((t.geschichte, t.land), (0, "DE"))

    def test_mehrheit_der_schlagzeilen_entscheidet(self):
        # Hauptschlagzeile passt zu nichts, ein Live-Blog passt zu Geschichte 1, zwei Schlagzeilen zu Geschichte 0.
        t = self.gleiche(["Neue Lage", "Liveblog", "Gipfel A", "Gipfel B"], einheit(4, 1, 0, 0))
        self.assertEqual(t.geschichte, 0)

    def test_eine_nebenschlagzeile_reicht_nicht(self):
        t = self.gleiche(["Neue Lage", "Liveblog"], einheit(4, 1))
        self.assertIsNone(t.geschichte)
        self.assertEqual(t.beste, 1)

    def test_kaum_aehnlich_hilft_auch_kein_wort(self):
        v = np.zeros((1, 5), dtype=np.float32)
        v[0, 0], v[0, 3] = 0.4, 0.92  # Ähnlichkeit 0,4 zu Geschichte 0, gemeinsame Wörter "gipfel", "genf"
        t = self.gleiche(["Gipfel in Genf"], v / np.linalg.norm(v))
        self.assertIsNone(t.geschichte)


if __name__ == "__main__":
    unittest.main()
