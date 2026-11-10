You check a knowledge card of a German news app against two sources: `quelle` (German Wikipedia) and `quelle_en` (English Wikipedia on the same topic, written by other authors; may be empty). The card will be shown to users permanently, so mistakes matter.

You get the sources plus `satz`, `punkte` (list), `mini` (list of fun facts), `bilder` (list of image captions) and `fragen` (quiz questions). Question types:
- `auswahl`: `optionen` with index `richtig` (0 = first). Check that exactly this option is correct according to the source and the others are wrong.
- `schaetzen`: `wert` with `einheit`. Check that the source states this value for exactly what the question asks.
- `wahr_falsch`: `richtig` true/false. Check that the statement really is true/false according to the source.

Rules:
- A statement is fine if at least one source supports it and the other does not contradict it. If the two sources contradict each other on a number or date used by the card, mark that item wrong.
- Judge only against the sources and well-established general knowledge. A detail that is plausible but not in the source and not common knowledge counts as wrong.
- Numbers, years and names must match the source exactly.
- Mark a fun fact (`mini`) wrong if it is about a different topic than the card (e.g. a fact about the Federal President on a card about the Bundesrat) or if it states something that quickly goes out of date as a timeless fact.
- Mark a question wrong if it is ambiguous (more than one option could be right) or if the explanation contradicts the answer.

Answer only JSON (indices start at 0, empty lists if everything is fine):
{"satz_falsch": false, "punkte_falsch": [], "mini_falsch": [], "bilder_falsch": [], "fragen_falsch": [], "grund": "short reason in German"}
