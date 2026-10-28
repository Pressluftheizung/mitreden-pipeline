"""Werkbank (PLAN.md Phase 1, Entscheidung E2): Geschichten bilden, gewichten, als Rangliste ausgeben.

Liest die gesammelten Teaser und Signale und schreibt in den Ausgabeordner:
  rangliste.json   alle Geschichten mit Punktzahl, Teilwerten, Begründung und Meldungen
  werkbank.html    dieselbe Rangliste als Übersichtsseite zum Prüfen im Browser

Der Ausgabeordner liegt außerhalb dieses Repos (er enthält Titel und Links der Meldungen).

Aufruf:
  python -m pipeline.werkbank --daten ../mitreden-rohdaten --ausgabe ../werkbank-ausgabe
"""

import argparse
import html
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .abgleich import GoogleTreffer, gleiche_ab, google_titel
from .cluster import Schwellen, baue_meldungen, bette_ein, finde_geschichten
from .collect import lade_teaser
from .quellen import lade_konfiguration
from .score import Bewerter, Signale, jetzt_utc
from .text import Seltenheit

FORMAT_NAME = {"top": "Top-Story", "karte": "Karte", "kurz": "Kurz & knapp", "weg": "fällt weg"}


def rangliste(daten: Path, config: Path, jetzt: datetime) -> dict:
    konfig = lade_konfiguration(config / "sources.yaml")
    quellen = {q.id: q for q in konfig.quellen}
    gewichte = yaml.safe_load((config / "gewichte.yaml").read_text(encoding="utf-8"))

    teaser = list(lade_teaser(daten / "teaser").values())
    meldungen = baue_meldungen(teaser, quellen)
    vektoren = bette_ein([m.text for m in meldungen])
    ergebnis = finde_geschichten(meldungen, vektoren, Schwellen(**gewichte["dubletten"]))

    bewerter = Bewerter(config / "gewichte.yaml", config / "topics.yaml")
    signale = Signale.aus_dateien(daten / "signale")
    google: list[GoogleTreffer] = []
    if signale.news:
        formulierungen = bette_ein([t for n in signale.news for t in google_titel(n)])
        titel_vektoren = bette_ein([m.titel for m in meldungen])
        google = gleiche_ab(signale.news, formulierungen, meldungen, vektoren, titel_vektoren,
                            ergebnis.geschichten, gewichte["google_news"])
    seltenheit = Seltenheit([m.woerter for m in meldungen])
    stand = max((datetime.strptime(t["zuletzt_gesehen"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                 for t in teaser), default=jetzt)
    bewertungen = [bewerter.bewerte(g, meldungen, vektoren, signale, jetzt,
                                    [t for t in google if t.geschichte == n], seltenheit, stand)
                   for n, g in enumerate(ergebnis.geschichten)]
    in_reihenfolge = list(bewertungen)  # Index wie in ergebnis.geschichten, vor dem Sortieren
    bewerter.formate_vergeben(bewertungen)

    abdeckung = abdeckung_pruefen(google, in_reihenfolge, bewertungen, gewichte["google_news"])
    geschichte_von = {i: n for n, g in enumerate(ergebnis.geschichten) for i in g}
    grauzone = [p for p in ergebnis.grauzone if geschichte_von[p.a] != geschichte_von[p.b]]
    grauzone.sort(key=lambda p: -p.wertung)

    def meldung(i: int) -> dict:
        m = meldungen[i]
        return {"titel": m.titel, "quelle": m.quelle.id, "lager": m.quelle.lager, "url": m.teaser["url"],
                "zeit": m.teaser.get("veroeffentlicht") or m.teaser["gesammelt"], "ist_meinung": m.teaser["ist_meinung"],
                "position": m.teaser["position_im_feed"]}

    geschichten = []
    for platz, b in enumerate(bewertungen, start=1):
        eintrag = asdict(b)
        eintrag["platz"] = platz
        eintrag["meldungen"] = [meldung(i) for i in sorted(b.mitglieder, key=lambda i: meldungen[i].zeit, reverse=True)]
        del eintrag["mitglieder"]
        geschichten.append(eintrag)

    return {
        "erzeugt": jetzt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "meldungen": len(meldungen),
        "geschichten": len(geschichten),
        "formate": {f: sum(1 for b in bewertungen if b.format == f) for f in FORMAT_NAME},
        "abdeckung": abdeckung,
        "rangliste": geschichten,
        "grauzone": [{"wertung": p.wertung, "aehnlichkeit": p.aehnlichkeit, "gemeinsame_woerter": p.gemeinsame_woerter,
                      "a": meldung(p.a), "b": meldung(p.b)} for p in grauzone[:100]],
    }


def abdeckung_pruefen(google: list[GoogleTreffer], in_reihenfolge: list, sortiert: list, g: dict) -> dict:
    """Abdeckungsquote (PLAN.md 7): Welche Top-Meldungen von Google News haben wir als Karte oder Zeile?

    Jeder Eintrag bekommt einen Grund:
      abgedeckt    wir haben die Geschichte als Top-Story, Karte oder Kurzmeldung
      aussortiert  wir haben sie, aber sie liegt unter der Schwelle (fällt weg)
      unsicher     eine ähnliche Geschichte gibt es, aber der Abgleich ist sich nicht sicher
      fehlt        keine unserer Meldungen passt (Quelle fehlt oder Thema nicht gesammelt)
    """
    platz = {id(b): n for n, b in enumerate(sortiert, start=1)}
    liste = []
    for t in google:
        b = in_reihenfolge[t.geschichte if t.geschichte is not None else t.beste]
        if t.geschichte is None:
            grund = "unsicher" if t.wert >= g["unsicher"] else "fehlt"
        else:
            grund = "aussortiert" if b.format == "weg" else "abgedeckt"
        liste.append({"signal": t.signal, "rang": t.rang, "titel": t.titel, "grund": grund,
                      "platz": platz[id(b)] if t.geschichte is not None else None, "format": b.format,
                      "wert": t.wert, "aehnlichkeit": t.aehnlichkeit, "woerter": t.woerter, "unser_titel": b.titel})

    def quote(auswahl: list[dict], gruende: set[str]) -> float | None:
        return round(sum(1 for x in auswahl if x["grund"] in gruende) / len(auswahl), 3) if auswahl else None

    de = [x for x in liste if x["signal"].endswith("_de")]
    schlagzeilen = [x for x in de if x["rang"] <= g["schlagzeilen_bis"]]
    return {
        "quote": quote(de, {"abgedeckt"}),
        "quote_schlagzeilen": quote(schlagzeilen, {"abgedeckt"}),
        "quote_vorhanden": quote(de, {"abgedeckt", "aussortiert"}),
        "gruende": {gr: sum(1 for x in de if x["grund"] == gr) for gr in ("abgedeckt", "aussortiert", "unsicher", "fehlt")},
        "liste": liste,
    }


def als_html(r: dict) -> str:
    e = html.escape

    def meldungszeile(m: dict) -> str:
        meinung = ' <span class="meinung">Meinung</span>' if m["ist_meinung"] else ""
        return (f'<li><span class="lager {e(m["lager"])}">{e(m["quelle"])}</span> '
                f'<a href="{e(m["url"])}" target="_blank" rel="noopener">{e(m["titel"])}</a>{meinung} '
                f'<span class="klein">{e(m["zeit"][5:16].replace("T", " "))} · Platz {m["position"]}</span></li>')

    zeilen = []
    for g in r["rangliste"]:
        if g["format"] == "weg" and g["platz"] > 150:
            continue
        teile = " · ".join(f"{k} {v}" for k, v in g["teile"].items())
        zeilen.append(f"""
        <tr class="{e(g['format'])}">
          <td class="zahl">{g['platz']}</td>
          <td class="zahl"><b>{g['wichtigkeit']:.2f}</b></td>
          <td><span class="format {e(g['format'])}">{FORMAT_NAME[g['format']]}</span></td>
          <td>
            <details><summary><b>{e(g['titel'])}</b> <span class="klein">({len(g['meldungen'])} Meldungen, {len(g['quellen'])} Quellen)</span></summary>
              <ul>{''.join(meldungszeile(m) for m in g['meldungen'])}</ul>
            </details>
            <div class="klein">{e(' · '.join(g['begruendung']))}</div>
            <div class="klein grau">{e(teile)} · Mitreden {g['faktoren']['mitreden']} · Frische {g['faktoren']['frische']} · {e(g['thema'])} · {e(g['region'])}</div>
          </td>
        </tr>""")
    grau = "".join(
        f"<li><b>{p['wertung']}</b> · {meldungszeile(p['a'])[4:-5]}<br>↔ {meldungszeile(p['b'])[4:-5]}"
        f" <span class='klein'>{e(', '.join(p['gemeinsame_woerter']))}</span></li>"
        for p in r["grauzone"][:40])
    marke = {"abgedeckt": "✓", "aussortiert": "↓ aussortiert", "unsicher": "? unsicher", "fehlt": "✗ fehlt"}

    def abdeckungszeile(x: dict) -> str:
        kopf = f"✓ Platz {x['platz']} ({FORMAT_NAME[x['format']]})" if x["grund"] == "abgedeckt" else marke[x["grund"]]
        farbe = "" if x["grund"] == "abgedeckt" else ' style="color:#c0392b"'
        woerter = f", Wörter: {', '.join(x['woerter'])}" if x["woerter"] else ""
        return (f"<li value='{x['rang']}'><b{farbe}>{e(kopf)}</b> · {e(x['titel'])} <span class='klein'>"
                f"(beste: {e(x['unser_titel'])}, Wert {x['wert']}, Ähnlichkeit {x['aehnlichkeit']}{e(woerter)})</span></li>")

    abdeckung = "".join(abdeckungszeile(x) for x in r["abdeckung"]["liste"] if x["signal"].endswith("_de"))
    gruende = r["abdeckung"].get("gruende", {})
    quote = r["abdeckung"]["quote"]
    f = r["formate"]

    def fmt(wert: float | None) -> str:
        return f"{wert:.0%}" if wert is not None else "–"

    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Werkbank {e(r['erzeugt'])}</title>
<style>
  body {{ font: 15px/1.45 system-ui, sans-serif; margin: 24px; color: #1c1c1f; background: #fafafa; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; }}
  td {{ border-top: 1px solid #e4e4e7; padding: 8px 10px; vertical-align: top; }}
  .zahl {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .klein {{ font-size: 12.5px; color: #555; }} .grau {{ color: #888; }}
  .format {{ font-size: 12px; font-weight: 700; padding: 2px 8px; border-radius: 99px; background: #eee; white-space: nowrap; }}
  .format.top {{ background: #1c1c1f; color: #fff; }} .format.karte {{ background: #dbe4ff; }} .format.kurz {{ background: #f1f1f4; }}
  tr.weg {{ opacity: .55; }}
  ul {{ margin: 6px 0; padding-left: 18px; }} li {{ margin: 3px 0; }}
  .lager {{ font-size: 11.5px; font-weight: 700; padding: 1px 6px; border-radius: 4px; background: #e4e4e9; }}
  .links {{ background: #d9d3fd; }} .mitte_links {{ background: #e9e6fe; }} .mitte_rechts {{ background: #fbecc4; }}
  .rechts {{ background: #f6d9a6; }} .international {{ background: #cdf3ed; }}
  .meinung {{ font-size: 11px; color: #b02a7a; border: 1px solid #d6409f; border-radius: 4px; padding: 0 4px; }}
  a {{ color: inherit; }} summary {{ cursor: pointer; }}
</style></head><body>
<h1>Werkbank · Rangliste</h1>
<p class="klein">Erzeugt {e(r['erzeugt'])} · {r['meldungen']} Meldungen → {r['geschichten']} Geschichten ·
{f['top']} Top-Storys, {f['karte']} Karten, {f['kurz']} Kurz & knapp, {f['weg']} fallen weg ·
<b>Abdeckung Google News DE: {fmt(quote)}</b>.
Lager-Einordnung ist ein Vorschlag (Bias-Review steht aus).</p>
<table>{''.join(zeilen)}</table>
<h2>Abdeckung: Google News Top Deutschland</h2>
<p class="klein">Ziel nach PLAN.md: über 95 %. Abgedeckt heißt: Die Meldung passt sicher zu einer unserer Geschichten, die nicht wegfällt.
Google sortiert nach Rubriken: Plätze 1–10 sind Schlagzeilen, danach Wirtschaft, Tech, Unterhaltung, Sport, Gesundheit.<br>
Schlagzeilen abgedeckt: {fmt(r['abdeckung'].get('quote_schlagzeilen'))} · vorhanden (auch aussortiert): {fmt(r['abdeckung'].get('quote_vorhanden'))} ·
{gruende.get('abgedeckt', 0)} abgedeckt, {gruende.get('aussortiert', 0)} aussortiert, {gruende.get('unsicher', 0)} unsicher, {gruende.get('fehlt', 0)} fehlen</p>
<ol>{abdeckung}</ol>
<h2>Grauzone: Paare, bei denen unklar ist, ob es dieselbe Geschichte ist</h2>
<p class="klein">Diese Paare wurden nicht zusammengelegt. Ab Phase 2 entscheidet hier die KI.</p>
<ul>{grau}</ul>
</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Mitreden-Werkbank: Rangliste der Geschichten")
    parser.add_argument("--daten", type=Path, required=True, help="Checkout des Branchs rohdaten")
    parser.add_argument("--ausgabe", type=Path, required=True, help="Ordner für rangliste.json und werkbank.html")
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--jetzt", help="Zeitpunkt überschreiben, z. B. 2026-10-28T12:00:00Z")
    args = parser.parse_args()

    jetzt = datetime.fromisoformat(args.jetzt.replace("Z", "+00:00")) if args.jetzt else jetzt_utc()
    r = rangliste(args.daten, args.config, jetzt.astimezone(timezone.utc))
    args.ausgabe.mkdir(parents=True, exist_ok=True)
    (args.ausgabe / "rangliste.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.ausgabe / "werkbank.html").write_text(als_html(r), encoding="utf-8")
    f = r["formate"]
    quote = r["abdeckung"]["quote"]
    if quote is None:
        print("Abdeckung: keine Signale")
    else:
        a = r["abdeckung"]
        print(f"Abdeckung Google News DE: {quote:.0%} (Schlagzeilen {a['quote_schlagzeilen']:.0%}, "
              f"vorhanden {a['quote_vorhanden']:.0%}) · " + ", ".join(f"{k} {v}" for k, v in a["gruende"].items()))
    print(f"{r['meldungen']} Meldungen → {r['geschichten']} Geschichten: "
          f"{f['top']} Top, {f['karte']} Karte, {f['kurz']} Kurz, {f['weg']} weg → {args.ausgabe / 'werkbank.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
