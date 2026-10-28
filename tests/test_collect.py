"""Tests für den Sammellauf. Aufruf: python -m unittest discover tests"""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.collect import fuehre_zusammen, sammeln, speichere_teaser
from pipeline.quellen import Quelle, lade_konfiguration
from pipeline.teaser import kuerze, lies_feed, normalisiere_url, teaser_id

HIER = Path(__file__).parent
FEED = (HIER / "fixtures" / "beispiel_feed.xml").read_bytes()
KONFIG = lade_konfiguration(HIER.parent / "config" / "sources.yaml")
JETZT = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
QUELLE = Quelle(
    id="beispiel", name="Beispielzeitung", url="https://beispiel.example/feed", sprache="de",
    region="deutschland", lager="mitte", art="zeitung", volltext_erlaubt=False,
    bezahlschranke="teilweise", bezahl_muster=("/plus[0-9a-f]",), bezahl_markiert=True,
)


class Adressen(unittest.TestCase):
    def test_tracking_und_schreibweise_egal(self):
        a = "https://beispiel.example/politik/testgesetz-123.html?utm_source=rss&wt_mc=feed"
        b = "https://BEISPIEL.example/politik/testgesetz-123.html"
        self.assertEqual(teaser_id(a), teaser_id(b))

    def test_echte_parameter_bleiben(self):
        self.assertIn("id=7", normalisiere_url("https://x.example/a?id=7&utm_medium=rss"))

    def test_kuerzen_am_wortende(self):
        text = kuerze("wort " * 200, 50)
        self.assertLessEqual(len(text), 50)
        self.assertTrue(text.endswith("…"))


class FeedLesen(unittest.TestCase):
    def setUp(self):
        self.teaser = lies_feed(FEED, QUELLE, KONFIG, JETZT)
        self.nach_titel = {t["titel"]: t for t in self.teaser}

    def test_werbung_und_dubletten_fliegen_raus(self):
        self.assertEqual(len(self.teaser), 4)
        self.assertFalse(any("Promoted" in t["titel"] for t in self.teaser))

    def test_text_ohne_html(self):
        t = self.nach_titel["Bundestag stimmt über Testgesetz ab"]
        self.assertEqual(t["teaser"], 'Der Bundestag entscheidet am Freitag über ein "Testgesetz".')
        self.assertEqual(t["veroeffentlicht"], "2026-10-27T07:30:00Z")
        self.assertEqual(t["rubrik"], "Politik")
        self.assertEqual(t["position_im_feed"], 1)

    def test_meinung_ueber_titel_und_adresse(self):
        self.assertTrue(self.nach_titel["Kommentar: Das Testgesetz kommt zu spät"]["ist_meinung"])
        self.assertTrue(self.nach_titel["Warum Beispiele wichtig sind"]["ist_meinung"])
        self.assertFalse(self.nach_titel["Bundestag stimmt über Testgesetz ab"]["ist_meinung"])

    def test_bezahlschranke(self):
        self.assertTrue(self.nach_titel["Exklusive Recherche zum Testgesetz"]["bezahlschranke"])
        self.assertFalse(self.nach_titel["Bundestag stimmt über Testgesetz ab"]["bezahlschranke"])
        unbekannt = lies_feed(FEED, replace(QUELLE, bezahl_markiert=False), KONFIG, JETZT)
        self.assertIsNone(next(t for t in unbekannt if t["position_im_feed"] == 1)["bezahlschranke"])

    def test_keine_bilder(self):
        self.assertFalse(any("bild" in k or "media" in k for t in self.teaser for k in t))


class Speichern(unittest.TestCase):
    def test_zusammenfuehren_behaelt_erstes_sehen(self):
        alle = {t["id"]: t for t in lies_feed(FEED, QUELLE, KONFIG, JETZT)}
        spaeter = JETZT + timedelta(hours=1)
        neu, geaendert = fuehre_zusammen(alle, lies_feed(FEED, QUELLE, KONFIG, spaeter))
        self.assertEqual((neu, geaendert), (0, 0))
        t = next(iter(alle.values()))
        self.assertEqual(t["gesammelt"], "2026-10-27T09:00:00Z")
        self.assertEqual(t["zuletzt_gesehen"], "2026-10-27T10:00:00Z")

    def test_nach_48_stunden_geloescht(self):
        with tempfile.TemporaryDirectory() as ordner:
            alle = {t["id"]: t for t in lies_feed(FEED, QUELLE, KONFIG, JETZT)}
            for t in alle.values():
                t["veroeffentlicht"] = "2026-10-27T08:00:00Z"
            self.assertEqual(speichere_teaser(Path(ordner), alle, JETZT + timedelta(hours=47)), 0)
            self.assertTrue((Path(ordner) / "2026-10-27.jsonl").exists())
            self.assertEqual(speichere_teaser(Path(ordner), alle, JETZT + timedelta(hours=49)), 4)
            self.assertFalse((Path(ordner) / "2026-10-27.jsonl").exists())

    def test_alte_artikel_trotz_feed_geloescht(self):
        # Manche Feeds führen Artikel tagelang weiter: nach 48 Std seit Veröffentlichung fliegen sie trotzdem raus.
        with tempfile.TemporaryDirectory() as ordner:
            alle = {t["id"]: t for t in lies_feed(FEED, QUELLE, KONFIG, JETZT)}
            alt = next(iter(alle.values()))
            alt["veroeffentlicht"] = "2026-10-24T08:00:00Z"
            for t in alle.values():
                t["zuletzt_gesehen"] = "2026-10-27T09:00:00Z"
            self.assertEqual(speichere_teaser(Path(ordner), alle, JETZT), 1)

    def test_ganzer_lauf_aus_dateien(self):
        konfig = replace(KONFIG, quellen=[QUELLE], signale=[])
        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / "xml"
            xml.mkdir()
            (xml / "beispiel.xml").write_bytes(FEED)
            daten = Path(tmp) / "daten"
            bericht = sammeln(konfig, daten, JETZT, aus_ordner=xml)
            self.assertEqual((bericht["teaser_neu"], bericht["feeds_fehler"]), (4, 0))
            zustand = json.loads((daten / "sammeln" / "zustand.json").read_text(encoding="utf-8"))
            self.assertEqual(zustand["beispiel"]["status"], "ok")
            zeilen = (daten / "teaser" / "2026-10-27.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(zeilen), 4)


class Konfiguration(unittest.TestCase):
    def test_alle_quellen_gueltig(self):
        self.assertEqual(len(KONFIG.quellen), 41)
        self.assertEqual(len({q.id for q in KONFIG.quellen}), 41)


if __name__ == "__main__":
    unittest.main()
