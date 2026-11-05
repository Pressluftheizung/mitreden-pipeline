"""Großthemen bündeln und Gesprächswert schätzen (Feedback des Nutzers 04.11.2026).

Nach den Dubletten (pipeline/dubletten.py) und nur im Kartenbau. Eine KI-Anfrage je Bau (Gemini Flash Lite):
1. Großthemen: Geschichten, die Teile desselben großen Ereignisses sind (Wahlabend mit vielen Einzelsiegen),
   werden zu einer Geschichte zusammengelegt. Daraus entsteht eine Sammelkarte statt zehn Einzelkarten.
2. Gesprächswert 1–3: Würde man das in einer normalen Gruppe erzählen? Die Wichtigkeit wird damit
   multipliziert (gewichte.yaml, themen_ki.faktor), danach werden die Formate neu vergeben.

Die Wichtigkeit aus Algorithmus B (Quellenbreite, Aufmerksamkeit, …) bleibt die Grundlage. Der
Gesprächswert verschiebt nur, er ersetzt sie nicht. Fehlt die KI, bleibt alles wie vorher.
"""

import json


def als_nummer(x) -> int | None:
    """3, 3.0, "3" oder "s3" → 3 (Modelle schreiben Nummern nicht immer als Zahl)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)) and float(x).is_integer():
        return int(x)
    if isinstance(x, str):
        ziffern = x.strip().lstrip("sSnr#").strip()
        return int(ziffern) if ziffern.isdigit() else None
    return None


def lies_antwort(antwort, anzahl: int, cfg: dict) -> tuple[list[tuple[str, list[int]]], list[int]]:
    """Großthemen (Name, Nummern ab 0) und Gesprächswerte (je Geschichte 1–3, sonst 2). Unbrauchbares fällt weg."""
    werte = [2] * anzahl
    roh = antwort.get("gespraech") if isinstance(antwort, dict) else None
    if isinstance(roh, dict):  # {"3": [Nummern], "1": [Nummern]}, alle übrigen 2
        for w, nrs in roh.items():
            w = als_nummer(w)
            if w is None or not 1 <= w <= 3 or not isinstance(nrs, list):
                continue
            for x in nrs:
                x = als_nummer(x)
                if x is not None and 1 <= x <= anzahl:
                    werte[x - 1] = w
    elif isinstance(roh, list):
        for i, w in enumerate(roh[:anzahl]):
            w = als_nummer(w)
            if w is not None and 1 <= w <= 3:
                werte[i] = w
    themen, vergeben = [], set()
    for t in (antwort.get("grossthemen") or []) if isinstance(antwort, dict) else []:
        if not isinstance(t, dict) or not isinstance(t.get("nr"), list):
            continue
        nrs = []
        for x in t["nr"]:
            x = als_nummer(x)
            if x is not None and 1 <= x <= anzahl and x - 1 not in vergeben and x - 1 not in nrs:
                nrs.append(x - 1)
        # Zu groß heißt nicht falsch (8-Uhr-Bau 5.11.: 34 Midterms-Geschichten): dann nur die ersten max_teile
        nrs = nrs[:cfg["max_teile"]]
        if len(nrs) >= cfg["min_teile"]:
            vergeben.update(nrs)
            themen.append((str(t.get("name") or "")[:60].strip() or "Großthema", nrs))
    return themen, werte


def pruefe(antwort) -> None:
    if not isinstance(antwort, dict) or not isinstance(antwort.get("gespraech"), (list, dict)):
        raise ValueError("gespraech fehlt")


def buendeln(st, ki, cfg: dict, auswerten, bewerter):
    """Gibt den neuen Stand zurück. st.themen enthält den Bericht (für die Metriken)."""
    eigene = st.eigene_geschichten
    bew = st.in_reihenfolge[:len(eigene)]
    kandidaten = sorted((n for n in range(len(eigene)) if bew[n].format != "weg"), key=lambda n: -bew[n].wichtigkeit)
    kandidaten = kandidaten[:cfg["max_geschichten"]]
    if not kandidaten:
        st.themen = {"gefragt": 0}
        return st
    daten = {"geschichten": [{"nr": i + 1, "titel": bew[n].titel[:140], "thema": bew[n].thema}
                             for i, n in enumerate(kandidaten)]}
    antwort, modell = ki.frage("themen", json.dumps(daten, ensure_ascii=False), pruefe)
    themen, werte = lies_antwort(antwort, len(kandidaten), cfg)

    # Großthema nur, wenn mindestens eine Geschichte darin eine eigene Karte wäre
    themen = [(name, nrs) for name, nrs in themen if any(bew[kandidaten[i]].format in ("top", "karte") for i in nrs)]
    wert_von = {kandidaten[i]: w for i, w in enumerate(werte)}

    # Neue Einteilung: Großthemen zuerst (als eine Geschichte), dann alle übrigen in alter Reihenfolge
    neu, info, neu_wert = [], {}, {}
    im_thema = set()
    for name, nrs in themen:
        alte = [kandidaten[i] for i in nrs]
        im_thema.update(alte)
        info[len(neu)] = {"name": name, "teile": [bew[n].titel for n in sorted(alte, key=lambda n: -bew[n].wichtigkeit)]}
        neu_wert[len(neu)] = max(wert_von[n] for n in alte)
        neu.append(sorted(i for n in alte for i in eigene[n]))
    for n, g in enumerate(eigene):
        if n not in im_thema:
            if n in wert_von:
                neu_wert[len(neu)] = wert_von[n]
            neu.append(list(g))
    if themen:
        st = auswerten(neu)

    faktor = {int(k): v for k, v in cfg["faktor"].items()}
    for idx, b in enumerate(st.in_reihenfolge[:len(neu)]):
        if idx in info:
            b.sammel = info[idx]
            b.begruendung.append(f"Großthema „{info[idx]['name']}“ aus {len(info[idx]['teile'])} Geschichten")
        w = neu_wert.get(idx)
        if w is not None:
            b.gespraech["wert"] = w
            if faktor.get(w, 1.0) != 1.0:
                b.wichtigkeit = round(b.wichtigkeit * faktor[w], 3)
                b.begruendung.append(f"Gesprächswert {w} ×{faktor[w]}")
    bewerter.formate_vergeben(st.sortiert)

    st.themen = {
        "modell": modell, "gefragt": len(kandidaten),
        # Rohvorschlag der KI (Name, Zahl der Nummern), um zu sehen, was die Prüfung aussortiert hat
        "vorschlag": [{"name": str(t.get("name"))[:60], "nr": len(t.get("nr") or [])}
                      for t in antwort.get("grossthemen") or [] if isinstance(t, dict)][:20],
        "grossthemen": [{"name": name, "teile": len(nrs)} for name, nrs in themen],
        "gespraech": {w: werte.count(w) for w in (1, 2, 3)},
    }
    return st
