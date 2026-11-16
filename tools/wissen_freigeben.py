"""Wissenskarten-Entwürfe nach der Durchsicht freigeben (Entscheidung des Nutzers 16.11.2026).

Neue KI-Karten (.github/workflows/wissen.yml) tragen "_entwurf" und erscheinen nicht in der App.
Nach der Durchsicht: Entwurf entfernen und version + 1 (sonst gewinnt beim Vereinen die alte Fassung).

  python tools/wissen_freigeben.py ../mitreden-rohdaten/wissen/wissen.json            # Entwürfe auflisten
  python tools/wissen_freigeben.py ../mitreden-rohdaten/wissen/wissen.json ID ID ...  # diese freigeben
  python tools/wissen_freigeben.py ../mitreden-rohdaten/wissen/wissen.json --alle     # alle freigeben
Danach wie gewohnt speichern und mit pipeline.veroeffentlichen in die App legen.
"""
import json
import sys
from pathlib import Path


def main() -> int:
    pfad = Path(sys.argv[1])
    db = json.loads(pfad.read_text(encoding="utf-8"))
    entwuerfe = [kid for kid, k in db["karten"].items() if k.get("_entwurf")]
    wahl = sys.argv[2:]
    if not wahl:
        for kid in entwuerfe:
            print(kid, "·", db["karten"][kid].get("titel", ""))
        print(f"{len(entwuerfe)} Entwürfe")
        return 0
    ids = entwuerfe if wahl == ["--alle"] else wahl
    for kid in ids:
        k = db["karten"].get(kid)
        if not k or not k.get("_entwurf"):
            print("kein Entwurf:", kid)
            continue
        del k["_entwurf"]
        k["version"] = k.get("version", 0) + 1
        print("freigegeben:", kid)
    pfad.write_text(json.dumps(db, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
