"""Prüfansicht (PLAN.md 16.3): Karte links, Quellen rechts. Ein Klick auf einen Fakt markiert seine Quellen.

Enthält Titel und Anrisstexte der Quellen, gehört deshalb nur ins private Repo.
"""

import html
import json

E = html.escape


def _karte(k: dict) -> str:
    qs = k.get("_quellen_ki") or []
    bild = k.get("bild") or {}
    bild_html = ""
    if bild.get("url"):
        ersatz = (bild.get("ersatz") or {}).get("url", "")
        ausweich = f' onerror="this.src=\'{E(ersatz)}\'"' if ersatz else ""
        lizenz = f" · {E(bild['lizenz'])}" if bild.get("lizenz") else ""
        bild_html = (f'<img src="{E(bild["url"])}" alt="{E(bild.get("alt", ""))}" loading="lazy" referrerpolicy="no-referrer"{ausweich}>'
                     f'<div class="klein">{E(bild.get("art", ""))}{lizenz}</div>')
    p = k.get("pruefung") or {}
    fakten = "".join(
        f'<li class="fakt" data-q="{E(" ".join(f.get("quellen", [])))}">{E(f.get("fakt", ""))} '
        f'<span class="klein">[{E(", ".join(f.get("quellen", [])))}]</span></li>' for f in k.get("faktenbox") or [])
    ebenen = ""
    for e in k.get("ebenen") or []:
        inhalt = "".join(f"<li>{E(x)}</li>" for x in e.get("punkte", []) + e.get("unstrittig", []))
        inhalt += "".join(f"<li><b>{E(x['name'])}</b>: {E(x['rolle'])}</li>" for x in e.get("personen", []))
        inhalt += "".join(f"<li><b>{E(x['absender'])}</b>: {E(x['text'])}</li>" for x in e.get("positionen", []))
        streit = f"<p>{E(e['streitpunkt'])}</p>" if e.get("streitpunkt") else ""
        ebenen += f"<details><summary>{E(e.get('titel', e.get('typ', '')))}</summary>{streit}<ul>{inhalt}</ul></details>"
    quellen = "".join(
        f'<li id="{E(k["id"])}-{E(q["id"])}"><b>{E(q["id"])}</b> <span class="lager {E(q["lager"])}">{E(q["redaktion"])}</span>'
        f'{" <i>Meinung</i>" if q["art"] == "meinung" else ""}{" <i>+ Volltext</i>" if q.get("volltext") else ""}'
        f' <a href="{E(q.get("url") or "#")}" target="_blank" rel="noopener">'
        f'{E(q["titel"])}</a><div class="klein">{E(q["text"])}</div></li>' for q in qs)
    hinweise = "".join(f"<li>{E(h)}</li>" for h in p.get("hinweise", []))
    erklaerung = ""
    if k.get("erklaerung"):
        erklaerung = ("<details><summary>Erklär mir das</summary><ul>"
                      + "".join(f"<li>{E(x)}</li>" for x in k["erklaerung"]) + "</ul></details>")
    fehlt =", ".join((k.get("_ki") or {}).get("fehlt") or [])
    return f"""
<section class="paar {E(p.get('ergebnis', ''))}" data-id="{E(k['id'])}">
  <div class="karte">
    <div class="kopf"><span class="format">{E(k['format'])}</span> {E(k['thema'])} · {E(k['region'])} · Punkte {k.get('punktzahl')}
      · Version {k.get('version')} · <b>Prüfung: {E(p.get('ergebnis', '–'))}</b> ({E(p.get('modell') or '')})</div>
    {bild_html}
    <h2>{E(k.get('titel', ''))}</h2>
    <p><b>{E(k.get('satz', ''))}</b></p>
    <ul>{''.join(f'<li>{E(x)}</li>' for x in k.get('punkte') or [])}</ul>
    <p class="mitreden">💬 {E(k.get('mitreden') or '– (gestrichen)')} <span class="klein">{E(k.get('mitreden_art') or '')}</span></p>
    {f'<p class="klein">Geändert: {E(k["aenderung"])}</p>' if k.get('aenderung') else ''}
    <div class="klein">Labels: {E(', '.join(k.get('labels', [])) or '–')} · Lager-Mix: {E(json.dumps(k.get('lager_mix', {}), ensure_ascii=False))}
      {(' · ' + E(k['lager_hinweis'])) if k.get('lager_hinweis') else ''} · Quellen fehlen zu: {E(fehlt or '–')}</div>
    <h3>Faktenbox <span class="klein">(anklicken markiert die Quellen)</span></h3><ul>{fakten}</ul>
    {erklaerung}
    {ebenen}
    {f'<h3>Prüf-Hinweise</h3><ul class="hinweise">{hinweise}</ul>' if hinweise else ''}
  </div>
  <ol class="quellen">{quellen or '<li class="klein">Karte aus einem früheren Lauf übernommen.</li>'}</ol>
</section>"""


def als_html(ergebnis: dict, karten_voll: dict, metriken: dict) -> str:
    neu = [k for k in karten_voll.values() if k.get("_neu_geschrieben")]
    alt = [k for k in karten_voll.values() if not k.get("_neu_geschrieben")]
    m = metriken
    kopf = (f"{m['geschichten']} Geschichten → {m['karten_voll']} Karten ({len(neu)} neu geschrieben), "
            f"{m['kurz_zeilen']} Kurzmeldungen · Prüfung: {', '.join(f'{k} {v}' for k, v in m['pruefung'].items())} · "
            f"Bilder: {', '.join(f'{k} {v}' for k, v in m['bilder'].items())} · {m['dauer_s']} s")
    kurz = "".join(f"<li>{E(z['text'])} <a class='klein' href='{E(z.get('url') or '#')}' target='_blank' rel='noopener'>Quelle</a></li>"
                   for k in ergebnis["karten"] if k["format"] == "kurz" for z in k["meldungen"])
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prüfansicht {E(ergebnis['ausgabe'])}</title>
<style>
  body {{ font: 15px/1.45 system-ui, sans-serif; margin: 20px; color: #1c1c1f; background: #f6f6f8; }}
  .paar {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; background: #fff; border-radius: 12px;
          padding: 16px; margin: 16px 0; border-left: 6px solid #ccc; }}
  .paar.ok {{ border-left-color: #30a46c; }} .paar.korrigiert {{ border-left-color: #f5a524; }} .paar.warnung {{ border-left-color: #e5484d; }}
  @media (max-width: 800px) {{ .paar {{ grid-template-columns: 1fr; }} }}
  img {{ max-width: 100%; max-height: 260px; border-radius: 8px; object-fit: cover; }}
  h2 {{ margin: 8px 0 4px; font-size: 20px; }} h3 {{ font-size: 14px; margin: 12px 0 4px; }}
  .klein {{ font-size: 12.5px; color: #666; }} .kopf {{ font-size: 12.5px; color: #555; }}
  .format {{ background: #1c1c1f; color: #fff; padding: 1px 8px; border-radius: 99px; font-weight: 700; }}
  .mitreden {{ background: #eef2ff; padding: 6px 10px; border-radius: 8px; }}
  .fakt {{ cursor: pointer; }} .fakt.an {{ background: #fff3c4; }}
  .quellen li {{ margin: 6px 0; padding: 4px 6px; border-radius: 6px; }} .quellen li.an {{ background: #fff3c4; }}
  .lager {{ font-size: 11.5px; font-weight: 700; padding: 1px 6px; border-radius: 4px; background: #e4e4e9; }}
  .links {{ background: #d9d3fd; }} .mitte_links {{ background: #e9e6fe; }} .mitte_rechts {{ background: #fbecc4; }}
  .rechts {{ background: #f6d9a6; }} .international {{ background: #cdf3ed; }}
  .hinweise li {{ color: #a33; }} a {{ color: inherit; }}
</style></head><body>
<h1>Prüfansicht · {E(ergebnis['ausgabe_label'])} ({E(ergebnis['ausgabe'])})</h1>
<p class="klein">{E(kopf)}<br>⚠ {E(ergebnis['hinweis'])} Neutralität, Lager-Mix und Quellenauswahl sind Testwerte.</p>
<h2>Neu geschriebene Karten</h2>
{''.join(_karte(k) for k in neu)}
<h2>Übernommene Karten</h2>
{''.join(_karte(k) for k in alt)}
<h2>Kurz & knapp</h2><ul>{kurz}</ul>
<script>
document.querySelectorAll('.fakt').forEach(function (f) {{
  f.addEventListener('click', function () {{
    var paar = f.closest('.paar'), id = paar.dataset.id;
    paar.querySelectorAll('.an').forEach(function (x) {{ x.classList.remove('an'); }});
    f.classList.add('an');
    (f.dataset.q || '').split(' ').forEach(function (q) {{
      var el = document.getElementById(id + '-' + q); if (el) el.classList.add('an');
    }});
  }});
}});
</script>
</body></html>"""
