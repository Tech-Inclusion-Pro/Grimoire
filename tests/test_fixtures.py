"""The fixture set from spec section 12, built before the importer.

Six shapes that break naive implementations, each asserted here so a later
change cannot quietly reintroduce the bug.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from backend import db, scryfall

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scryfall_cards.json"


@pytest.fixture(scope="module")
def cards() -> list[dict]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_name(cards) -> dict[str, dict]:
    return {c["name"]: c for c in cards}


@pytest.fixture()
def conn(tmp_path, cards):
    c = db.open_db(tmp_path / "test.db")
    scryfall.load_cards(c, cards)
    yield c
    c.close()


# --- 1. transform: text lives in card_faces --------------------------------

def test_transform_card_keeps_its_text(by_name):
    card = by_name["Delver of Secrets // Insectile Aberration"]
    assert card["layout"] == "transform"
    # The trap: the root object genuinely has none of this.
    assert card.get("oracle_text") is None
    assert card.get("mana_cost") is None

    row = scryfall.extract(card)
    assert "look at the top card" in row["oracle_text"]
    assert "Flying" in row["oracle_text"]      # back face is searchable too
    assert row["mana_cost"] == "{U}"           # back face cost is empty, not joined


def test_modal_dfc_joins_both_costs(by_name):
    card = by_name["Agadeem's Awakening // Agadeem, the Undercrypt"]
    assert card["layout"] == "modal_dfc"
    row = scryfall.extract(card)
    assert row["mana_cost"] == "{X}{B}{B}{B}"
    assert "Return from your graveyard" in row["oracle_text"]
    assert "pay 3 life" in row["oracle_text"]


def test_every_face_gets_its_own_image(conn, by_name):
    card_id = by_name["Delver of Secrets // Insectile Aberration"]["id"]
    faces = conn.execute(
        "SELECT face_index, name, image_normal FROM card_faces "
        "WHERE card_id=? ORDER BY face_index", (card_id,)).fetchall()
    assert len(faces) == 2
    assert faces[0]["image_normal"] != faces[1]["image_normal"]
    assert all(f["image_normal"] for f in faces)


# --- 2. split: two mana costs, one card ------------------------------------

def test_split_card_carries_both_halves(by_name):
    card = by_name["Fire // Ice"]
    assert card["layout"] == "split"
    # Split is the awkward middle: root HAS mana_cost and image_uris, but no
    # oracle_text. Code that branches on "has card_faces" must not assume the
    # root is empty.
    assert card.get("mana_cost") == "{1}{R} // {1}{U}"
    assert card.get("oracle_text") is None

    row = scryfall.extract(card)
    assert row["mana_cost"] == "{1}{R} // {1}{U}"
    assert "Fire deals 2 damage" in row["oracle_text"]
    assert "Tap target permanent" in row["oracle_text"]
    assert row["color_identity"] == "RU"
    assert row["image_normal"], "split art lives at the root, not on the faces"


# --- 3. three printings, one foil: the oracle_id / printing join -----------

def test_three_printings_share_one_oracle_id(conn):
    rows = conn.execute(
        "SELECT id, set_code, oracle_id FROM cards WHERE name='Llanowar Elves'"
    ).fetchall()
    assert len(rows) == 3
    assert len({r["oracle_id"] for r in rows}) == 1, "one game object"
    assert len({r["id"] for r in rows}) == 3, "three printings"


def test_owning_three_printings_is_one_deck_slot(conn):
    printings = conn.execute(
        "SELECT id FROM cards WHERE name='Llanowar Elves' ORDER BY set_code"
    ).fetchall()
    conn.execute("INSERT INTO locations (name) VALUES ('Elfball box')")
    loc = conn.execute("SELECT id FROM locations").fetchone()["id"]
    for i, p in enumerate(printings):
        conn.execute(
            "INSERT INTO copies (card_id, foil, condition, location_id) VALUES (?,?,?,?)",
            (p["id"], 1 if i == 0 else 0, "NM", loc))
    conn.commit()

    oracle = conn.execute(
        "SELECT oracle_id FROM cards WHERE name='Llanowar Elves' LIMIT 1"
    ).fetchone()["oracle_id"]
    conn.execute("INSERT INTO decks (name, format) VALUES ('Elfball','commander')")
    deck = conn.execute("SELECT id FROM decks").fetchone()["id"]
    conn.execute("INSERT INTO deck_cards (deck_id, oracle_id, quantity) VALUES (?,?,1)",
                 (deck, oracle))
    conn.commit()

    owned = conn.execute("""
        SELECT COUNT(*) AS n FROM copies
        JOIN cards ON cards.id = copies.card_id
        WHERE cards.oracle_id = ?""", (oracle,)).fetchone()["n"]
    assert owned == 3, "three physical copies"

    foils = conn.execute("SELECT COUNT(*) AS n FROM copies WHERE foil=1").fetchone()["n"]
    assert foils == 1

    slots = conn.execute("SELECT SUM(quantity) AS n FROM deck_cards").fetchone()["n"]
    assert slots == 1, "one deck slot regardless of how many printings are owned"


# --- 4. a card in a deck you do not own ------------------------------------

def test_unowned_deck_card_is_wishlist(conn, by_name):
    oracle = by_name["Craterhoof Behemoth"]["oracle_id"]
    conn.execute("INSERT INTO decks (name, format) VALUES ('Elfball','commander')")
    deck = conn.execute("SELECT id FROM decks").fetchone()["id"]
    conn.execute(
        "INSERT INTO deck_cards (deck_id, oracle_id, quantity, wishlist) VALUES (?,?,1,1)",
        (deck, oracle))
    conn.commit()

    # Buildable-now percentage counts owned slots only, but the card still
    # appears in the list -- a hard owned-only rule makes planning impossible.
    row = conn.execute("""
        SELECT
          COUNT(*) AS slots,
          SUM(CASE WHEN wishlist=0 THEN 1 ELSE 0 END) AS owned_slots
        FROM deck_cards WHERE deck_id=?""", (deck,)).fetchone()
    assert row["slots"] == 1
    assert row["owned_slots"] == 0

    held = conn.execute("""
        SELECT COUNT(*) AS n FROM copies
        JOIN cards ON cards.id = copies.card_id
        WHERE cards.oracle_id=?""", (oracle,)).fetchone()["n"]
    assert held == 0


# --- 5. a card that creates a token ----------------------------------------

def test_token_maker_records_what_to_bring(conn, by_name):
    card_id = by_name["Dwynen's Elite"]["id"]
    tokens = conn.execute(
        "SELECT name, type_line FROM card_parts WHERE card_id=? AND component='token'",
        (card_id,)).fetchall()
    assert [t["name"] for t in tokens] == ["Elf Warrior"]


def test_a_card_is_not_its_own_combo_piece(conn, by_name):
    card_id = by_name["Dwynen's Elite"]["id"]
    self_ref = conn.execute(
        "SELECT COUNT(*) AS n FROM card_parts WHERE card_id=? AND part_id=?",
        (card_id, card_id)).fetchone()["n"]
    assert self_ref == 0


# --- 6. names that break CSV parsing and name resolution -------------------

def test_name_with_comma_and_quotes_round_trips(conn):
    row = conn.execute(
        'SELECT name FROM cards WHERE name LIKE ?', ("Kongming%",)).fetchone()
    assert row["name"] == 'Kongming, "Sleeping Dragon"'


def test_non_ascii_name_round_trips(conn):
    row = conn.execute(
        "SELECT name FROM cards WHERE name LIKE ?", ("Ghazb%",)).fetchone()
    assert row["name"] == "Ghazbán Ogre"


def test_multiface_names_do_not_resolve_via_collection_endpoint(by_name):
    """Documents the trap found while building the fixture set: Scryfall's
    /cards/collection matches the FRONT FACE name, not 'A // B'. The importer
    and the CSV resolver must split on ' // ' before sending identifiers."""
    full = "Fire // Ice"
    assert full in by_name
    front = full.split(" // ")[0]
    assert front == "Fire"


# --- search: the thing that quietly breaks ---------------------------------

def test_double_faced_cards_are_findable_by_back_face_text(conn):
    hits = conn.execute(
        "SELECT c.name FROM cards_fts f JOIN cards c ON c.rowid=f.rowid "
        "WHERE cards_fts MATCH 'Flying'").fetchall()
    names = {h["name"] for h in hits}
    assert "Delver of Secrets // Insectile Aberration" in names


def test_every_fixture_card_is_in_the_search_index(conn, cards):
    n = conn.execute("SELECT COUNT(*) AS n FROM cards_fts").fetchone()["n"]
    assert n == len(cards)


# --- search behaviour that broke on the first real run ---------------------

def test_art_cards_do_not_bury_the_real_card(conn):
    """'Delver of Secrets' returned the art card first before the layout
    filter existed. 3,322 art cards, tokens, and emblems sit in the bulk file."""
    from backend import queries
    res = queries.search_cards(conn, "Delver of Secrets", owned_only=False)
    names = [r["name"] for r in res["results"]]
    assert "Delver of Secrets // Insectile Aberration" in names
    assert "Delver of Secrets // Delver of Secrets" not in names, "art card leaked in"


def test_extras_are_still_reachable_on_request(conn):
    from backend import queries
    res = queries.search_cards(conn, "Delver of Secrets", owned_only=False,
                               include_extras=True)
    names = [r["name"] for r in res["results"]]
    assert "Delver of Secrets // Delver of Secrets" in names


def test_printings_count_is_not_narrowed_by_ownership(conn):
    """Owned-only search inner-joins copies. COUNT(DISTINCT c.id) then counted
    only the printings owned, so a card with 62 printings reported 3."""
    from backend import queries
    row = conn.execute("SELECT id FROM cards WHERE name='Llanowar Elves' LIMIT 1").fetchone()
    conn.execute("INSERT INTO copies (card_id) VALUES (?)", (row["id"],))
    conn.commit()

    res = queries.search_cards(conn, "Llanowar Elves", owned_only=True)
    hit = next(r for r in res["results"] if r["name"] == "Llanowar Elves")
    assert hit["owned"] == 1
    assert hit["printings"] == 3, "all known printings, not just the owned one"


def test_search_handles_a_name_containing_quotes(conn):
    """Unescaped, this name is FTS5 phrase syntax and raises OperationalError."""
    from backend import queries
    res = queries.search_cards(conn, 'Kongming, "Sleeping Dragon"', owned_only=False)
    assert res["total"] >= 1
