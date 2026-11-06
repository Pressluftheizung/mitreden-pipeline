"""Tests für Kartenbau, Prüfung und Bild-Adressen. Alle Texte sind ausgedacht."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pipeline.bauen import ausgabe_id, grundwissen_bereinigen, kapitel_bereinigen, lager_mix, slug
from pipeline.ki import KI, KeinModellFrei, Zaehler, lese_json
from pipeline.pruefen import anwenden, code_pruefung, zahlen
from pipeline.teaser import bild_url

CONFIG = Path(__file__).parent.parent / "config"


def karte() -> dict:
    return {"titel": "Bahn streicht 120 Züge", "satz": "Die Bahn streicht am Montag 120 Züge.",
            "punkte": ["Betroffen sind 120 Züge.", "Grund ist ein Sturm.", "Ab 2027 soll alles besser werden."],
            "mitreden": "Wer am Montag fährt, sollte die App prüfen.",
            "faktenbox": [{"fakt": "Die Bahn streicht 120 Züge.", "quellen": ["q1", "q2"]}]}


class Zahlen(unittest.TestCase):
    def test_schreibweisen(self):
        self.assertEqual(zahlen("1.000 Menschen, 2,5 Prozent, 3:1"), {"1000", "2.5", "3", "1"})

    def test_fremde_zahl_wird_gefunden(self):
        probleme = code_pruefung(karte(), ["Die Bahn streicht wegen eines Sturms am Montag 120 Züge."])
        self.assertEqual([p["feld"] for p in probleme if not p.get("form")], ["punkte"])  # „2027“ steht nirgends
        self.assertIn("2027", probleme[0]["problem"])


class Anwenden(unittest.TestCase):
    def test_nicht_belegter_punkt_fliegt_raus(self):
        k = karte()
        urteil = {"aussagen": [{"nr": 5, "urteil": "nicht_belegt", "grund": "steht nicht in den Quellen"}],
                  "neutralitaet": "mitte", "seitentausch_ok": True, "wertende_woerter": []}
        p = anwenden(k, [], urteil, "groq", "test")
        self.assertEqual(p["ergebnis"], "korrigiert")
        self.assertEqual(len(k["punkte"]), 2)

    def test_nicht_belegter_titel_gibt_warnung(self):
        k = karte()
        urteil = {"aussagen": [{"nr": 1, "urteil": "nicht_belegt"}], "neutralitaet": "mitte", "seitentausch_ok": True}
        self.assertEqual(anwenden(k, [], urteil, "groq", "test")["ergebnis"], "warnung")
        self.assertEqual(k["titel"], "Bahn streicht 120 Züge")

    def test_mitreden_ohne_beleg_wird_gestrichen(self):
        k = karte()
        urteil = {"aussagen": [{"nr": 6, "urteil": "nicht_belegt", "grund": "Spekulation"}], "neutralitaet": "mitte",
                  "seitentausch_ok": True}
        p = anwenden(k, [], urteil, "groq", "test")
        self.assertEqual((p["ergebnis"], k["mitreden"]), ("korrigiert", None))

    def test_nicht_belegter_kapitelpunkt_fliegt_raus(self):
        k = karte()
        k["kapitel"] = [{"titel": "Die Strecken", "punkte": ["Köln betroffen.", "Hamburg betroffen."]},
                        {"titel": "Leer danach", "punkte": ["Nur ein Punkt."]}]
        urteil = {"aussagen": [{"nr": 6, "urteil": "nicht_belegt"}, {"nr": 8, "urteil": "nicht_belegt"}],
                  "neutralitaet": "mitte", "seitentausch_ok": True}
        p = anwenden(k, [], urteil, "groq", "test")
        self.assertEqual(p["ergebnis"], "korrigiert")
        self.assertEqual(k["kapitel"], [{"titel": "Die Strecken", "punkte": ["Hamburg betroffen."]}])
        self.assertEqual(k["mitreden"], "Wer am Montag fährt, sollte die App prüfen.")  # Nummer 9, unberührt

    def test_falsches_grundwissen_fliegt_raus(self):
        k = karte()
        k["grundwissen"] = [{"begriff": "Senat", "text": "Hat 50 Mitglieder."}, {"begriff": "Haus", "text": "435 Sitze."}]
        k["nebenbei"] = "Eine Randnotiz."
        urteil = {"aussagen": [], "neutralitaet": "mitte", "seitentausch_ok": True,
                  "einordnung_falsch": ["g1", "n1"], "einordnung_grund": "falsch"}
        p = anwenden(k, [], urteil, "groq", "test")
        self.assertEqual((p["ergebnis"], k["grundwissen"], k["nebenbei"]),
                         ("korrigiert", [{"begriff": "Haus", "text": "435 Sitze."}], None))

    def test_falsche_vorgeschichte_und_fakten_fliegen_raus(self):
        k = karte()
        k["vorgeschichte"], k["fakten"] = ["Richtig.", "Falsch."], ["Fakt A.", "Fakt B."]
        k["zusammenhang"] = [{"text": "Hängt zusammen.", "karte": "k-x"}]
        urteil = {"aussagen": [], "neutralitaet": "mitte", "seitentausch_ok": True,
                  "einordnung_falsch": ["v2", "f1", "z1"], "einordnung_grund": "falsch"}
        anwenden(k, [], urteil, "groq", "test")
        self.assertEqual((k["vorgeschichte"], k["fakten"], k["zusammenhang"]), (["Richtig."], ["Fakt B."], []))

    def test_falsche_stimme_und_antwort_fliegen_raus(self):
        from pipeline.pruefen import aussagen
        k = karte()
        k["stimmen"] = [{"art": "ausland", "wer": "Polen", "text": "Warnt."}, {"art": "fachleute", "wer": "X", "text": "Erfunden."}]
        k["fragen"] = [{"frage": "Warum?", "antwort": "Darum."}, {"frage": "Wie?", "antwort": "Falsch."}]
        nr = next(a["nr"] for a in aussagen(k) if a["feld"] == "stimmen" and a["index"] == 1)
        urteil = {"aussagen": [{"nr": nr, "urteil": "nicht_belegt", "grund": "steht nirgends"}], "neutralitaet": "mitte",
                  "seitentausch_ok": True, "einordnung_falsch": ["a2"], "einordnung_grund": "falsch"}
        anwenden(k, [], urteil, "groq", "test")
        self.assertEqual([s["wer"] for s in k["stimmen"]], ["Polen"])
        self.assertEqual([f["frage"] for f in k["fragen"]], ["Warum?"])

    def test_fragen_und_stimmen_bereinigen(self):
        from pipeline.bauen import fragen_bereinigen, stimmen_bereinigen
        self.assertEqual(fragen_bereinigen([{"frage": " Warum? ", "antwort": "Darum."}, {"frage": "Leer", "antwort": ""}, "x"]),
                         [{"frage": "Warum?", "antwort": "Darum."}])
        self.assertEqual(stimmen_bereinigen([{"art": "quatsch", "wer": "Polen", "text": "Warnt."}, {"wer": "", "text": "y"}]),
                         [{"art": "politik", "wer": "Polen", "text": "Warnt."}])
        vage = [{"wer": w, "text": "Zu hoch."} for w in ("Wirtschaftsexperten", "Viele Beobachter", "Kritiker", "Ifo-Ökonom Clemens Fuest")]
        self.assertEqual([s["wer"] for s in stimmen_bereinigen(vage)], ["Ifo-Ökonom Clemens Fuest"])

    def test_volltext_reihenfolge_ausgewogen(self):
        from pipeline.bauen import volltext_reihenfolge
        q = lambda i, lager, neutral=False, ok=True: {"id": i, "lager": lager, "neutral": neutral, "volltext_ok": ok, "url": "https://x/" + i}
        quellen = [q("l1", "links"), q("l2", "mitte_links"), q("r1", "rechts"), q("m", "mitte", True), q("bezahlt", "mitte", True, False)]
        self.assertEqual([x["id"] for x in volltext_reihenfolge(quellen)], ["m", "l1", "r1", "l2"])

    def test_verweise_aufloesen(self):
        from pipeline.bauen import verweise_aufloesen, zusammenhang_bereinigen
        roh = zusammenhang_bereinigen([{"text": "Mit Börse", "thema": "t2"}, {"text": "Mit sich selbst", "thema": "t1"},
                                       "Nur Text", {"text": "Gibt es nicht", "thema": "t9"}])
        self.assertEqual(len(roh), 2)  # höchstens 2
        voll = {0: {"id": "k-a", "titel": "A"}, 1: {"id": "k-b", "titel": "Börse"}}
        self.assertEqual(verweise_aufloesen(roh, 0, voll),
                         [{"text": "Mit Börse", "karte": "k-b", "titel": "Börse"}, {"text": "Mit sich selbst"}])

    def test_kapitel_bereinigen(self):
        roh = [{"titel": " A ", "punkte": ["x", "", 3, "y", "z", "u", "v", "w"]}, {"titel": "B", "punkte": []},
               "Unsinn", {"titel": "C", "punkte": ["c"]}, {"titel": "D", "punkte": ["d"]}, {"titel": "E", "punkte": ["e"]},
               {"titel": "F", "punkte": ["f"]}]
        self.assertEqual(kapitel_bereinigen(roh), [{"titel": "A", "punkte": ["x", "y", "z", "u", "v"]},
                                                   {"titel": "C", "punkte": ["c"]}, {"titel": "D", "punkte": ["d"]},
                                                   {"titel": "E", "punkte": ["e"]}])
        self.assertEqual(kapitel_bereinigen(None), [])

    def test_grundwissen_bereinigen(self):
        roh = [{"begriff": "Senat", "text": "100 Senatoren."}, {"begriff": "", "text": "x"}, "Unsinn",
               {"begriff": "Leitzins", "text": "Zins der Notenbank."}, {"begriff": "Drei", "text": "ok"},
               {"begriff": "Vier", "text": "zu viel"}]
        self.assertEqual([g["begriff"] for g in grundwissen_bereinigen(roh)], ["Senat", "Leitzins", "Drei"])
        self.assertEqual(grundwissen_bereinigen("kaputt"), [])

    def test_mitreden_nur_bei_falsch_oder_parteiisch_weg(self):
        for problem, erwartet in ((None, "Wer am Montag fährt, sollte die App prüfen."), ("parteiisch", None)):
            k = karte()
            urteil = {"aussagen": [], "neutralitaet": "mitte", "seitentausch_ok": True, "mitreden_problem": problem}
            anwenden(k, [], urteil, "groq", "test")
            self.assertEqual(k["mitreden"], erwartet)

    def test_liste_statt_objekt_ist_ungueltig(self):
        from pipeline.bauen import pruefe_karten
        with self.assertRaises(ValueError):
            pruefe_karten([{"story": "s1"}])

    def test_englische_feldnamen(self):
        from pipeline.bauen import pruefe_karten
        antwort = {"karten": [{"story": "s1", "title": "Titel", "sentence": "Satz.", "points": ["a", "b", "c"]}]}
        pruefe_karten(antwort)
        self.assertEqual(antwort["karten"][0]["titel"], "Titel")

    def test_ohne_pruef_ki(self):
        self.assertEqual(anwenden(karte(), [], None, None, None)["ergebnis"], "ungeprueft")


class Bau(unittest.TestCase):
    def test_ausgabe_nach_uhrzeit(self):
        self.assertEqual(ausgabe_id(datetime(2026, 11, 2, 6, 23, tzinfo=timezone.utc))[0], "2026-11-02-08")
        self.assertEqual(ausgabe_id(datetime(2026, 11, 2, 13, 23, tzinfo=timezone.utc))[0], "2026-11-02-15")
        self.assertEqual(ausgabe_id(datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc))[0], "2026-11-03-08")

    def test_slug(self):
        self.assertEqual(slug("Größter Bahnstreik: Züge stehen still"), "groesster-bahnstreik-zue")

    def test_lager_hinweis_erst_bei_schieflage(self):
        q = [{"name": n, "lager": l} for n, l in [("A", "links"), ("B", "mitte_links"), ("C", "links"), ("D", "mitte_links")]]
        self.assertEqual(lager_mix(q)[1], "Vor allem von linken Medien aufgegriffen")
        self.assertIsNone(lager_mix(q[:3] + [{"name": "E", "lager": "mitte"}])[1])


class KISchicht(unittest.TestCase):
    def test_json_aus_codeblock(self):
        self.assertEqual(lese_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_kontingent_reserve(self):
        z = Zaehler(None)
        grenzen = {"rpm": 5, "rpd": 20, "tpm": 1000}
        for _ in range(17):
            z.buche("m", 10)
        self.assertTrue(z.frei("m", grenzen, 0.1, 10))
        z.buche("m", 10)
        self.assertFalse(z.frei("m", grenzen, 0.1, 10))  # 18 von 20: Reserve von 10 % bleibt frei

    def test_ohne_schluessel_kein_modell(self):
        with tempfile.TemporaryDirectory() as tmp:
            ki = KI(CONFIG / "ki.config.json", Zaehler(Path(tmp) / "v.json"))
            import os
            alt = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "PRUEFER_API_KEY")}
            try:
                with self.assertRaises(KeinModellFrei):
                    ki.frage("kurz", json.dumps({"geschichten": []}))
            finally:
                os.environ.update({k: v for k, v in alt.items() if v})

    def test_zu_grosse_anfrage_wartet_nicht_endlos(self):
        # 15-Uhr-Bau 5.11.: Anfrage größer als das Minutenlimit → warte() hing 60 Minuten
        with tempfile.TemporaryDirectory() as tmp:
            ki = KI(CONFIG / "ki.config.json", Zaehler(Path(tmp) / "v.json"))
            ki._frage = lambda *a: self.fail("darf nicht fragen")
            with self.assertRaises(KeinModellFrei) as f:
                ki.frage("pruefen", "x" * 40000)
            self.assertIn("zu groß", str(f.exception))

    def test_pruef_anfrage_passt_ins_minutenlimit(self):
        from pipeline.pruefen import PRUEF_MAX_ZEICHEN, pruef_anfrage
        qs = [{"id": f"q{i}", "redaktion": "R", "lager": "mitte", "art": "bericht", "titel": "T" * 80,
               "text": "a" * 600, "volltext": "b" * 3000 if i < 2 else None} for i in range(14)]
        karte = {"titel": "T", "satz": "S", "punkte": ["p"] * 3, "kapitel": [{"titel": "K", "punkte": ["x" * 90] * 5}] * 4,
                 "grundwissen": [{"begriff": "B", "text": "t" * 250}], "nebenbei": "n" * 200}
        text = pruef_anfrage(karte, qs)
        self.assertLessEqual(len(text), PRUEF_MAX_ZEICHEN)
        self.assertGreaterEqual(len(json.loads(text)["quellen"]), 5)


class Wartezeit(unittest.TestCase):
    def test_groq_und_gemini(self):
        from pipeline.ki import HttpFehler, wartezeit
        self.assertAlmostEqual(wartezeit(HttpFehler(429, "Rate limit reached ... Please try again in 7.5s")), 8.5)
        self.assertEqual(wartezeit(HttpFehler(429, "Please try again in 1m2s")), 63.0)
        self.assertIsNone(wartezeit(HttpFehler(429, "Request too large for model")))
        self.assertEqual(wartezeit(HttpFehler(503, "overloaded")), 10.0)
        self.assertIsNone(wartezeit(HttpFehler(400, "bad")))


class Datenkarten(unittest.TestCase):
    def test_vorlagen(self):
        from pipeline.daten import deutsch, vorlage_titel, vorlage_warum
        self.assertEqual(deutsch(25873.2, 0), "25.873")
        self.assertEqual(vorlage_warum({"name": "Öl", "veraenderung_prozent": -7.79}), "Öl gibt deutlich nach: −7,8 %.")
        self.assertEqual(vorlage_warum({"name": "DAX", "veraenderung_prozent": 0.1}), "Kaum verändert.")
        spiele = [{"heim": "A", "gast": "B", "tore": (4, 1)}, {"heim": "C", "gast": "D", "tore": (2, 2)}]
        self.assertEqual(vorlage_titel(spiele), "A schießt B ab")
        self.assertEqual(vorlage_titel([{"heim": "C", "gast": "D", "tore": (3, 3)}]), "Torfestival: C gegen D")

    def test_warum_mit_fremder_zahl_wird_verworfen(self):
        from pipeline.daten import zahlen_karte

        class KI:
            def frage(self, aufgabe, nutzer, pruefe):
                return {"warum": {"DAX": "DAX steigt dank Rekordgewinn von 42 Milliarden.", "Gold": "Anleger suchen Sicherheit."}}, "test"

        werte = [{"name": "DAX", "wert": "25.874 Punkte", "veraenderung_prozent": 0.5},
                 {"name": "Gold", "wert": "3.910 $ je Unze", "veraenderung_prozent": 1.0}]
        k = zahlen_karte(werte, ["Gold gefragt wie nie"], KI(), "x")
        self.assertEqual(k["werte"][0]["warum"], "DAX legt zu: +0,5 %.")  # 42 steht nirgends: Vorlage
        self.assertEqual(k["werte"][1]["warum"], "Anleger suchen Sicherheit.")


class BildAdresse(unittest.TestCase):
    def test_reihenfolge(self):
        self.assertEqual(bild_url({"media_content": [{"url": "https://a.example/klein.jpg", "width": "200"},
                                                     {"url": "https://a.example/gross.jpg", "width": "1200"}]}),
                         "https://a.example/gross.jpg")
        self.assertEqual(bild_url({"links": [{"rel": "enclosure", "type": "image/jpeg", "href": "https://b.example/x.jpg"}]}),
                         "https://b.example/x.jpg")
        self.assertEqual(bild_url({"summary": '<p><img src="https://c.example/y.png"> Text</p>'}), "https://c.example/y.png")
        self.assertIsNone(bild_url({"summary": "nur Text"}))


if __name__ == "__main__":
    unittest.main()


class PopUp(unittest.TestCase):
    def test_erklaere_bereinigen_und_wiki(self):
        from unittest import mock
        from pipeline import bauen
        roh = [{"wort": "Dmitrijew", "art": "person", "text": "Putins Gesandter.", "wiki": "Kirill Dmitrijew"},
               {"wort": "dmitrijew", "art": "person", "text": "doppelt"}, {"wort": "X", "text": "zu kurz"},
               {"wort": "Proklamation", "art": "quatsch", "text": "Erlass.", "wiki": "Gibt es nicht"}]
        k = {"erklaere": bauen.erklaere_bereinigen(roh)}
        self.assertEqual([(e["wort"], e["art"]) for e in k["erklaere"]], [("Dmitrijew", "person"), ("Proklamation", "begriff")])
        treffer = {"Kirill Dmitrijew": {"url": "https://de.m.wikipedia.org/wiki/Kirill_Dmitrijew", "beschreibung": "Manager"},
                   "Gibt es nicht": None}
        with mock.patch.object(bauen, "wiki_abgleich", return_value=treffer):
            bauen.wiki_ergaenzen([k])
        self.assertEqual(k["erklaere"][0]["wiki"]["beschreibung"], "Manager")
        self.assertNotIn("wiki", k["erklaere"][1])
        self.assertFalse(any("_wiki_titel" in e for e in k["erklaere"]))
