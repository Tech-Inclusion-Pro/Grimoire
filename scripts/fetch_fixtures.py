"""Pull the fixture set from Scryfall once and freeze it on disk.

The fixtures exist to break naive implementations, so they must be *real*
Scryfall shapes rather than hand-written JSON. Run this only when the fixture
set changes; the committed output is what the tests read.
"""
import json
import os
import pathlib
import time
import urllib.request

UA = f"Grimoire/0.1 ({os.environ.get('GRIMOIRE_CONTACT', 'https://github.com/Tech-Inclusion-Pro/Grimoire')})"
OUT = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "scryfall_cards.json"

# Named lookups: one identifier per fixture case.
# NOTE: /cards/collection resolves the FRONT FACE name, not the "A // B"
# combined name. Sending the combined name returns not_found -- one of the
# quiet ways a naive importer loses every multi-face card.
NAMED = [
    "Delver of Secrets",        # transform, text lives in card_faces
    "Agadeem's Awakening",      # modal_dfc, mana cost per face
    "Fire",                     # split, two costs on one card
    "Craterhoof Behemoth",                          # in a deck, not owned -> wishlist
    "Dwynen's Elite",                               # creates a token
    'Kongming, "Sleeping Dragon"',                  # comma AND embedded quotes
    "Grizzly Bears",            # genuinely vanilla: no oracle text at all
]

# Identifiers needing a specific printing. Ghazbán Ogre resolves by name to its
# Masters Edition printing, which is MTGO-only (digital=1) and so is filtered
# out of search as something you can never physically own. Pin the paper
# printing, or the "non-ASCII name" fixture silently tests nothing.
PINNED = [
    {"name": "Ghazbán Ogre", "set": "chr"},
]

def post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def main():
    cards = []

    # /cards/collection takes up to 75 identifiers per request. Use it.
    res = post("https://api.scryfall.com/cards/collection",
               {"identifiers": [{"name": n} for n in NAMED] + PINNED})
    for miss in res.get("not_found", []):
        print("  NOT FOUND:", miss)
    cards.extend(res["data"])
    time.sleep(0.6)  # search/named/collection are limited to 2 req/sec

    # Three printings of one card, so the oracle_id <-> printing join has teeth.
    res = get("https://api.scryfall.com/cards/search?q=%21%22Llanowar+Elves%22&unique=prints")
    picks = [c for c in res["data"] if c.get("set") in ("dom", "m19", "m12", "3ed")][:3]
    if len(picks) < 3:
        picks = res["data"][:3]
    cards.extend(picks)

    # An art card, which is ownable but is not a card you deckbuild with.
    # "Delver of Secrets" returns the art card first without a layout filter.
    time.sleep(0.6)
    res = get("https://api.scryfall.com/cards/search"
              "?q=%21%22Delver+of+Secrets%22+layout%3Aart_series&unique=prints")
    cards.extend(res["data"][:1])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cards, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nwrote {len(cards)} cards -> {OUT}")
    for c in cards:
        faces = len(c.get("card_faces", []) or [])
        print(f"  {c['layout']:<10} {c['set']:<5} faces={faces}  {c['name']}")

if __name__ == "__main__":
    main()
