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

import numpy as np
import yaml

from .cluster import Schwellen, baue_meldungen, bette_ein, finde_geschichten
from .collect import lade_teaser
from .quellen import lade_konfiguration
from .score import Bewerter, Signale, jetzt_utc

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
    bewertungen = [bewerter.bewerte(g, meldungen, vektoren, signale, jetzt) for g in ergebnis.geschichten]
    bewerter.formate_vergeben(bewertungen)

    abdeckung = abdeckung_pruefen(signale, bewertungen, vektoren)
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


def abdeckung_pruefen(signale: Signale, bewertungen: list, vektoren: np.ndarray) -> dict:
    """Abdeckungsquote (PLAN.md 7): Welche Top-Meldungen von Google News haben wir als Karte oder Zeile?"""
    if not signale.news or signale.news_vektoren is None:
        return {"quote": None, "liste": []}
    # Vergleich mit jeder Meldung einer Geschichte (nicht mit dem Mittelwert): Google-Titel sind kurz.
    geschichte_von = np.empty(len(vektoren), dtype=int)
    for n, b in enumerate(bewertungen):
        geschichte_von[b.mitglieder] = n
    liste = []
    for n, v in zip(signale.news, signale.news_vektoren):
        einzeln = vektoren @ v
        werte = np.full(len(bewertungen), -1.0)
        np.maximum.at(werte, geschichte_von, einzeln)
        beste = int(werte.argmax())
        gefunden = werte[beste] >= 0.65 and bewertungen[beste].format != "weg"
        liste.append({"signal": n["signal"], "rang": n["rang"], "titel": n["titel"],
                      "platz": beste + 1 if gefunden else None, "aehnlichkeit": round(float(werte[beste]), 2),
                      "unser_titel": bewertungen[beste].titel})
    de = [x for x in liste if x["signal"].endswith("_de")]
    quote = sum(1 for x in de if x["platz"]) / len(de) if de else None
    return {"quote": round(quote, 3) if quote is not None else None, "liste": liste}


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
    abdeckung = "".join(
        f"<li>{'✓ Platz ' + str(x['platz']) if x['platz'] else '<b style=\"color:#c0392b\">✗ fehlt</b>'} · {e(x['titel'])}"
        f" <span class='klein'>(beste: {e(x['unser_titel'])}, Ähnlichkeit {x['aehnlichkeit']})</span></li>"
        for x in r["abdeckung"]["liste"] if x["signal"].endswith("_de"))
    quote = r["abdeckung"]["quote"]
    f = r["formate"]
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
<b>Abdeckung Google News DE: {f"{quote:.0%}" if quote is not None else "–"}</b>.
Lager-Einordnung ist ein Vorschlag (Bias-Review steht aus).</p>
<table>{''.join(zeilen)}</table>
<h2>Abdeckung: Google News Top Deutschland</h2>
<p class="klein">Ziel nach PLAN.md: über 95 %. Gefunden heißt: Die Meldung passt zu einer unserer Geschichten, die nicht wegfällt.</p>
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
    print(f"Abdeckung Google News DE: {quote:.0%}" if quote is not None else "Abdeckung: keine Signale")
    print(f"{r['meldungen']} Meldungen → {r['geschichten']} Geschichten: "
          f"{f['top']} Top, {f['karte']} Karte, {f['kurz']} Kurz, {f['weg']} weg → {args.ausgabe / 'werkbank.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
