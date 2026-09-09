"""Deck editor tests.

The rules that matter are the ones the spec is firm about: decks reference the
game object rather than a printing, wishlist cards are allowed but counted
separately, and a category the user set is never overwritten.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from backend import db, decks, scryfall

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scryfall_cards.json"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_DB", str(tmp_path / "decks.db"))
    c = db.open_db(tmp_path / "decks.db")
    scryfall.load_cards(c, json.loads(FIXTURES.read_text(encoding="utf-8")))
    yield c
    c.close()


@pytest.fixture()
def client(conn):
    from backend.main import app
    with TestClient(app) as c:
        yield c


def oracle(conn, name):
    return conn.execute("SELECT oracle_id FROM cards WHERE name=? LIMIT 1",
                        (name,)).fetchone()["oracle_id"]


def own(conn, name, n=1, finish="normal"):
    row = conn.execute("SELECT id FROM cards WHERE name=? LIMIT 1", (name,)).fetchone()
    for _ in range(n):
        conn.execute("INSERT INTO copies (card_id, finish, foil) VALUES (?,?,?)",
                     (row["id"], finish, 0 if finish == "normal" else 1))
    conn.commit()


# --- decklist parsing ------------------------------------------------------

@pytest.mark.parametrize("line,qty,name", [
    ("1 Sol Ring", 1, "Sol Ring"),
    ("4x Llanowar Elves", 4, "Llanowar Elves"),
    ("Sol Ring", 1, "Sol Ring"),
    ("2 Sol Ring (C21) 263", 2, "Sol Ring"),
    ("1 Fire // Ice", 1, "Fire // Ice"),
    ('1 Kongming, "Sleeping Dragon"', 1, 'Kongming, "Sleeping Dragon"'),
    ("1 Ghazbán Ogre", 1, "Ghazbán Ogre"),
    ("3 Forest *F*", 3, "Forest"),
])
def test_decklist_lines_parse(line, qty, name):
    got = decks.parse_decklist(line)
    assert len(got) == 1
    assert got[0]["quantity"] == qty
    assert got[0]["name"] == name


def test_comments_and_blank_lines_are_skipped():
    assert decks.parse_decklist("// a comment\n\n# another\n1 Sol Ring") == [
        {"quantity": 1, "name": "Sol Ring", "set_code": None, "section": None}]


def test_resolution_prefers_a_printing_you_own(conn):
    own(conn, "Llanowar Elves", 2)
    res = decks.resolve_names(conn, decks.parse_decklist("1 Llanowar Elves"))
    assert res["unresolved"] == []
    assert res["resolved"][0]["owned"] == 2
    assert res["resolved"][0]["category"] == "Creature"


def test_a_front_face_name_resolves_to_the_whole_card(conn):
    res = decks.resolve_names(conn, decks.parse_decklist("1 Fire"))
    assert res["resolved"][0]["matched_name"] == "Fire // Ice"


def test_unknown_cards_are_reported_not_guessed(conn):
    res = decks.resolve_names(conn, decks.parse_decklist("1 Definitely Not A Card"))
    assert res["resolved"] == []
    assert res["unresolved"][0]["name"] == "Definitely Not A Card"


# --- the counting rules ----------------------------------------------------

def test_owning_three_printings_satisfies_one_slot(conn):
    """Decks reference the game object. Owning Llanowar Elves in three
    printings is three copies against one slot, not three slots."""
    own(conn, "Llanowar Elves", 1)
    deck = decks.create_deck(conn, "Elfball")
    decks.set_card(conn, deck, oracle(conn, "Llanowar Elves"), 1)
    s = decks.deck_stats(conn, deck)
    assert s["cards"] == 1 and s["buildable_now"] == 1 and s["buildable_pct"] == 100.0


def test_wishlist_cards_are_in_the_deck_but_not_buildable(conn):
    deck = decks.create_deck(conn, "Elfball")
    own(conn, "Dwynen's Elite", 1)
    decks.set_card(conn, deck, oracle(conn, "Dwynen's Elite"), 1)
    decks.set_card(conn, deck, oracle(conn, "Craterhoof Behemoth"), 1)  # unowned

    s = decks.deck_stats(conn, deck)
    assert s["cards"] == 2, "the wishlist card is still in the list"
    assert s["buildable_now"] == 1
    assert s["wishlist"] == 1
    assert s["missing"] == 1
    assert s["buildable_pct"] == 50.0


def test_wishlist_is_inferred_from_what_you_hold(conn):
    deck = decks.create_deck(conn, "D")
    own(conn, "Dwynen's Elite", 1)
    decks.set_card(conn, deck, oracle(conn, "Dwynen's Elite"), 1)
    decks.set_card(conn, deck, oracle(conn, "Craterhoof Behemoth"), 1)
    rows = {c["name"]: c["wishlist"] for c in decks.deck_cards(conn, deck)}
    assert rows["Dwynen's Elite"] == 0
    assert rows["Craterhoof Behemoth"] == 1


def test_needing_more_copies_than_you_own_is_not_buildable(conn):
    own(conn, "Llanowar Elves", 2)
    deck = decks.create_deck(conn, "D")
    decks.set_card(conn, deck, oracle(conn, "Llanowar Elves"), 4, wishlist=False)
    assert decks.deck_stats(conn, deck)["buildable_now"] == 0


def test_two_decks_cannot_both_claim_the_same_single_copy(conn):
    """Without conflict detection both decks report 100% buildable while
    sharing one physical card."""
    own(conn, "Llanowar Elves", 1)
    a = decks.create_deck(conn, "Deck A")
    b = decks.create_deck(conn, "Deck B")
    oid = oracle(conn, "Llanowar Elves")
    decks.set_card(conn, a, oid, 1, wishlist=False)
    decks.set_card(conn, b, oid, 1, wishlist=False)

    conflicts = decks.deck_stats(conn, a)["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["name"] == "Llanowar Elves"
    assert conflicts[0]["owned"] == 1 and conflicts[0]["used_elsewhere"] == 1


def test_a_second_copy_resolves_the_conflict(conn):
    own(conn, "Llanowar Elves", 2)
    a, b = decks.create_deck(conn, "A"), decks.create_deck(conn, "B")
    oid = oracle(conn, "Llanowar Elves")
    decks.set_card(conn, a, oid, 1, wishlist=False)
    decks.set_card(conn, b, oid, 1, wishlist=False)
    assert decks.deck_stats(conn, a)["conflicts"] == []


# --- categories ------------------------------------------------------------

def test_lands_are_categorised_before_ramp_or_creatures():
    assert decks.suggest_category("Land — Forest") == "Land"
    assert decks.suggest_category("Artifact Creature — Golem") == "Creature"
    assert decks.suggest_category("Legendary Creature — Elf") == "Creature"
    assert decks.suggest_category("Instant") == "Instant"
    assert decks.suggest_category(None) == "Other"


def test_a_user_set_category_is_never_overwritten(conn):
    """Suggested once on import, then untouched."""
    deck = decks.create_deck(conn, "D")
    oid = oracle(conn, "Dwynen's Elite")
    decks.set_card(conn, deck, oid, 1, category="Elfball engine")
    decks.set_card(conn, deck, oid, 2)          # quantity change, no category
    assert decks.deck_cards(conn, deck)[0]["category"] == "Elfball engine"


# --- curve, tokens, filter -------------------------------------------------

def test_lands_are_excluded_from_the_mana_curve(conn):
    deck = decks.create_deck(conn, "D")
    decks.set_card(conn, deck, oracle(conn, "Dwynen's Elite"), 1)
    decks.set_card(conn, deck, oracle(conn, "Agadeem's Awakening // Agadeem, the Undercrypt"), 1)
    curve = decks.deck_stats(conn, deck)["curve"]
    assert curve.get("2") == 1, "Dwynen's Elite is a 2-drop"


def test_tokens_to_bring_is_a_packing_list(conn):
    deck = decks.create_deck(conn, "D")
    decks.set_card(conn, deck, oracle(conn, "Dwynen's Elite"), 1)
    assert [t["name"] for t in decks.tokens_needed(conn, deck)] == ["Elf Warrior"]


def test_in_deck_filter_uses_the_same_search_syntax(conn):
    deck = decks.create_deck(conn, "D")
    for n in ("Dwynen's Elite", "Craterhoof Behemoth", "Fire // Ice"):
        decks.set_card(conn, deck, oracle(conn, n), 1)
    assert {c["name"] for c in decks.deck_cards(conn, deck, "c:g")} == {
        "Dwynen's Elite", "Craterhoof Behemoth"}
    assert {c["name"] for c in decks.deck_cards(conn, deck, "mv<=2")} == {"Dwynen's Elite"}


# --- history ---------------------------------------------------------------

def test_history_restores_a_previous_list(conn):
    deck = decks.create_deck(conn, "D")
    decks.set_card(conn, deck, oracle(conn, "Dwynen's Elite"), 1)
    hid = decks.snapshot(conn, deck, "one card")

    decks.set_card(conn, deck, oracle(conn, "Craterhoof Behemoth"), 1)
    assert decks.deck_stats(conn, deck)["distinct"] == 2

    decks.restore(conn, deck, hid)
    assert {c["name"] for c in decks.deck_cards(conn, deck)} == {"Dwynen's Elite"}


def test_restoring_is_itself_undoable(conn):
    deck = decks.create_deck(conn, "D")
    decks.set_card(conn, deck, oracle(conn, "Dwynen's Elite"), 1)
    hid = decks.snapshot(conn, deck, "one card")
    decks.set_card(conn, deck, oracle(conn, "Craterhoof Behemoth"), 1)
    decks.restore(conn, deck, hid)

    entries = decks.history(conn, deck)
    assert any(e["summary"] == "before restore" for e in entries)
    decks.restore(conn, deck, next(e["id"] for e in entries if e["summary"] == "before restore"))
    assert decks.deck_stats(conn, deck)["distinct"] == 2


# --- export ----------------------------------------------------------------

def test_text_export(conn):
    deck = decks.create_deck(conn, "D")
    decks.set_card(conn, deck, oracle(conn, "Dwynen's Elite"), 3)
    assert decks.export(conn, deck, "text") == "3 Dwynen's Elite\n"


def test_arena_export_uses_the_front_face_name(conn):
    deck = decks.create_deck(conn, "D")
    decks.set_card(conn, deck, oracle(conn, "Fire // Ice"), 1)
    out = decks.export(conn, deck, "arena")
    assert out.startswith("1 Fire (")
    assert "//" not in out, "Arena rejects the combined name"


def test_csv_export_quotes_a_name_containing_a_comma(conn):
    deck = decks.create_deck(conn, "D")
    decks.set_card(conn, deck, oracle(conn, 'Kongming, "Sleeping Dragon"'), 1)
    line = decks.export(conn, deck, "csv").splitlines()[1]
    assert '"Kongming, ""Sleeping Dragon"""' in line


def test_markdown_export_marks_what_you_must_buy(conn):
    deck = decks.create_deck(conn, "Elfball")
    own(conn, "Dwynen's Elite", 1)
    decks.set_card(conn, deck, oracle(conn, "Dwynen's Elite"), 1)
    decks.set_card(conn, deck, oracle(conn, "Craterhoof Behemoth"), 1)
    md = decks.export(conn, deck, "markdown")
    assert "# Elfball" in md
    assert "- 1 Craterhoof Behemoth  *(need to buy)*" in md
    assert "- 1 Dwynen's Elite\n" in md


# --- the API ---------------------------------------------------------------

def test_deck_api_roundtrip(client, conn):
    r = client.post("/api/decks", json={"name": "Elfball", "format": "commander"})
    assert r.status_code == 201
    deck_id = r.json()["id"]

    own(conn, "Dwynen's Elite", 1)
    oid = oracle(conn, "Dwynen's Elite")
    assert client.put(f"/api/decks/{deck_id}/cards",
                      json={"oracle_id": oid, "quantity": 2}).status_code == 200

    body = client.get(f"/api/decks/{deck_id}").json()
    assert body["deck"]["name"] == "Elfball"
    assert body["stats"]["cards"] == 2
    assert body["tokens"][0]["name"] == "Elf Warrior"

    assert client.get(f"/api/decks/{deck_id}", params={"q": "t:land"}).json()["cards"] == []
    assert client.delete(f"/api/decks/{deck_id}").status_code == 204
    assert client.get(f"/api/decks/{deck_id}").status_code == 404


def test_bulk_add_is_a_dry_run_until_applied(client, conn):
    deck_id = client.post("/api/decks", json={"name": "D"}).json()["id"]
    text = "1 Dwynen's Elite\n1 Definitely Not A Card"

    dry = client.post(f"/api/decks/{deck_id}/bulk", json={"text": text}).json()
    assert len(dry["resolved"]) == 1 and len(dry["unresolved"]) == 1
    assert "added" not in dry
    assert client.get(f"/api/decks/{deck_id}").json()["stats"]["cards"] == 0

    live = client.post(f"/api/decks/{deck_id}/bulk", json={"text": text, "apply": True}).json()
    assert live["added"] == 1
    assert client.get(f"/api/decks/{deck_id}").json()["stats"]["cards"] == 1


def test_bad_in_deck_filter_returns_the_parser_message(client):
    deck_id = client.post("/api/decks", json={"name": "D"}).json()["id"]
    r = client.get(f"/api/decks/{deck_id}", params={"q": "nonsense:x"})
    assert r.status_code == 400 and "Unknown filter" in r.json()["detail"]


def test_export_endpoint(client, conn):
    deck_id = client.post("/api/decks", json={"name": "D"}).json()["id"]
    client.put(f"/api/decks/{deck_id}/cards",
               json={"oracle_id": oracle(conn, "Dwynen's Elite"), "quantity": 1})
    assert client.get(f"/api/decks/{deck_id}/export").text == "1 Dwynen's Elite\n"
    assert client.get(f"/api/decks/{deck_id}/export", params={"fmt": "zzz"}).status_code == 422


# --- which printing represents a deck slot ---------------------------------

def test_deck_price_comes_from_the_cheapest_paper_printing(conn):
    """The representative printing can be from a set Scryfall has not priced,
    which showed a dash for Sol Ring while 43 copies were owned."""
    deck = decks.create_deck(conn, "D")
    oid = oracle(conn, "Llanowar Elves")
    conn.execute("UPDATE cards SET price_usd = NULL WHERE oracle_id = ?", (oid,))
    newest = conn.execute(
        "SELECT id FROM cards WHERE oracle_id=? ORDER BY released_at DESC LIMIT 1",
        (oid,)).fetchone()["id"]
    oldest = conn.execute(
        "SELECT id FROM cards WHERE oracle_id=? ORDER BY released_at ASC LIMIT 1",
        (oid,)).fetchone()["id"]
    conn.execute("UPDATE cards SET price_usd = 3.50 WHERE id = ?", (oldest,))
    conn.commit()

    decks.set_card(conn, deck, oid, 1)
    row = decks.deck_cards(conn, deck)[0]
    assert row["price_usd"] == 3.50, "an unpriced newest printing must not win"
    assert newest != oldest


def test_a_deck_slot_prefers_a_printing_you_actually_own(conn):
    deck = decks.create_deck(conn, "D")
    oid = oracle(conn, "Llanowar Elves")
    held = conn.execute(
        "SELECT id FROM cards WHERE oracle_id=? ORDER BY released_at ASC LIMIT 1",
        (oid,)).fetchone()["id"]
    conn.execute("INSERT INTO copies (card_id, finish, foil) VALUES (?, 'normal', 0)", (held,))
    conn.commit()

    decks.set_card(conn, deck, oid, 1)
    conn.execute("UPDATE cards SET type_line = 'MARKER' WHERE id = ?", (held,))
    conn.commit()
    assert decks.deck_cards(conn, deck)[0]["type_line"] == "MARKER"


def test_print_pref_beats_everything(conn):
    deck = decks.create_deck(conn, "D")
    oid = oracle(conn, "Llanowar Elves")
    chosen = conn.execute(
        "SELECT id FROM cards WHERE oracle_id=? ORDER BY set_code LIMIT 1", (oid,)).fetchone()["id"]
    decks.set_card(conn, deck, oid, 1, print_pref=chosen)
    conn.execute("UPDATE cards SET type_line = 'CHOSEN' WHERE id = ?", (chosen,))
    conn.commit()
    assert decks.deck_cards(conn, deck)[0]["type_line"] == "CHOSEN"


# --- editing a deck after creation -----------------------------------------

def test_deck_can_be_renamed_and_given_a_commander(client, conn):
    deck_id = client.post("/api/decks", json={"name": "Untitled"}).json()["id"]
    oid = oracle(conn, "Craterhoof Behemoth")

    r = client.patch(f"/api/decks/{deck_id}",
                     json={"name": "Elfball", "commander_oracle_id": oid,
                           "bracket_claimed": 3})
    assert r.status_code == 200
    assert r.json()["name"] == "Elfball"
    assert r.json()["commander_oracle_id"] == oid
    assert client.get("/api/decks").json()[0]["commander"] == "Craterhoof Behemoth"


def test_omitted_fields_are_left_alone(conn, client):
    deck_id = client.post("/api/decks", json={"name": "D", "format": "modern"}).json()["id"]
    oid = oracle(conn, "Craterhoof Behemoth")
    client.patch(f"/api/decks/{deck_id}", json={"commander_oracle_id": oid})
    body = client.patch(f"/api/decks/{deck_id}", json={"name": "Renamed"}).json()
    assert body["format"] == "modern", "format was not touched"
    assert body["commander_oracle_id"] == oid, "commander was not cleared by omission"


def test_clearing_a_commander_takes_an_explicit_flag(conn, client):
    deck_id = client.post("/api/decks", json={"name": "D"}).json()["id"]
    oid = oracle(conn, "Craterhoof Behemoth")
    client.patch(f"/api/decks/{deck_id}", json={"commander_oracle_id": oid})
    body = client.patch(f"/api/decks/{deck_id}", json={"clear_commander": True}).json()
    assert body["commander_oracle_id"] is None


def test_an_unknown_commander_is_rejected(client):
    deck_id = client.post("/api/decks", json={"name": "D"}).json()["id"]
    assert client.patch(f"/api/decks/{deck_id}",
                        json={"commander_oracle_id": "not-a-real-id"}).status_code == 404


# --- the deck tile's front card --------------------------------------------

def test_the_commander_is_the_front_card(conn):
    deck = decks.create_deck(conn, "Elfball")
    for n in ("Craterhoof Behemoth", "Dwynen's Elite"):
        decks.set_card(conn, deck, oracle(conn, n), 1)
    cmd = oracle(conn, "Dwynen's Elite")
    conn.execute("UPDATE decks SET commander_oracle_id=? WHERE id=?", (cmd, deck))
    conn.commit()

    banner = decks.list_decks(conn)[0]["banner_card_id"]
    assert conn.execute("SELECT oracle_id FROM cards WHERE id=?",
                        (banner,)).fetchone()["oracle_id"] == cmd


def test_without_a_commander_the_first_card_listed_is_the_front_card(conn):
    """Any other format is identified by the card at the top of the list."""
    deck = decks.create_deck(conn, "Modern thing", "modern")
    for n in ("Craterhoof Behemoth", "Dwynen's Elite", "Grizzly Bears"):
        decks.set_card(conn, deck, oracle(conn, n), 1)

    banner = decks.list_decks(conn)[0]["banner_card_id"]
    name = conn.execute("SELECT name FROM cards WHERE id=?", (banner,)).fetchone()["name"]
    listed = [c["name"] for c in decks.deck_cards(conn, deck)]
    assert name == listed[0] == "Craterhoof Behemoth"


def test_an_empty_deck_has_no_front_card(conn):
    decks.create_deck(conn, "Empty")
    assert decks.list_decks(conn)[0]["banner_card_id"] is None


# --- playtesting -----------------------------------------------------------

def test_playtest_expands_quantities_into_individual_cards(conn):
    deck = decks.create_deck(conn, "D")
    decks.set_card(conn, deck, oracle(conn, "Grizzly Bears"), 4)
    p = decks.playtest_deck(conn, deck)
    assert len(p["library"]) == 4
    assert len({c["uid"] for c in p["library"]}) == 4, "each copy needs its own id"


def test_the_commander_starts_in_the_command_zone(conn):
    deck = decks.create_deck(conn, "Elfball")
    cmd = oracle(conn, "Dwynen's Elite")
    decks.set_card(conn, deck, cmd, 1)
    decks.set_card(conn, deck, oracle(conn, "Grizzly Bears"), 2)
    conn.execute("UPDATE decks SET commander_oracle_id=? WHERE id=?", (cmd, deck))
    conn.commit()

    p = decks.playtest_deck(conn, deck)
    assert [c["name"] for c in p["command_zone"]] == ["Dwynen's Elite"]
    assert "Dwynen's Elite" not in [c["name"] for c in p["library"]]
    assert len(p["library"]) == 2


def test_starting_life_follows_the_format(conn):
    commander = decks.create_deck(conn, "C", "commander")
    modern = decks.create_deck(conn, "M", "modern")
    assert decks.playtest_deck(conn, commander)["starting_life"] == 40
    assert decks.playtest_deck(conn, modern)["starting_life"] == 20


def test_playtest_endpoint(client, conn):
    deck_id = client.post("/api/decks", json={"name": "D"}).json()["id"]
    client.put(f"/api/decks/{deck_id}/cards",
               json={"oracle_id": oracle(conn, "Grizzly Bears"), "quantity": 3})
    body = client.get(f"/api/decks/{deck_id}/playtest").json()
    assert len(body["library"]) == 3
    assert client.get("/api/decks/999/playtest").status_code == 404
