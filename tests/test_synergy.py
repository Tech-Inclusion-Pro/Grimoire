"""Synergy search and shelf tests.

The embedding model is not called: these assert the ranking, the reasons and
the shelf rules, all of which are deterministic given a set of vectors.
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import db, decks, embeddings, scryfall, shelf, synergy

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scryfall_cards.json"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_DB", str(tmp_path / "s.db"))
    c = db.open_db(tmp_path / "s.db")
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


def own(conn, name, n=1):
    row = conn.execute("SELECT id FROM cards WHERE name=? LIMIT 1", (name,)).fetchone()
    for _ in range(n):
        conn.execute("INSERT INTO copies (card_id, finish, foil) VALUES (?,'normal',0)",
                     (row["id"],))
    conn.commit()


# --- vectors ---------------------------------------------------------------

def test_vectors_are_normalised_on_the_way_in():
    """Normalising at write time turns every query into a dot product instead
    of a cosine with magnitudes to divide out 8,000 times."""
    blob = embeddings.to_blob([3.0, 4.0] + [0.0] * 766)
    arr = embeddings.from_blob(blob)
    assert np.isclose(np.linalg.norm(arr), 1.0)
    assert np.isclose(arr[0], 0.6) and np.isclose(arr[1], 0.8)


def test_a_zero_vector_does_not_divide_by_zero():
    arr = embeddings.from_blob(embeddings.to_blob([0.0] * 768))
    assert not np.isnan(arr).any()


def test_index_ranks_by_similarity():
    ids = ["a", "b", "c"]
    matrix = np.stack([embeddings.from_blob(embeddings.to_blob(v)) for v in (
        [1.0, 0.0] + [0.0] * 766,
        [0.9, 0.1] + [0.0] * 766,
        [0.0, 1.0] + [0.0] * 766)])
    index = embeddings.Index(ids, matrix)
    hits = index.search([1.0, 0.0] + [0.0] * 766, top=3)
    assert [h[0] for h in hits] == ["a", "b", "c"]
    assert hits[0][1] > hits[1][1] > hits[2][1]


def test_card_text_changing_forces_a_rebuild():
    a = embeddings.text_hash(embeddings.card_text("X", "Creature", "Flying"))
    b = embeddings.text_hash(embeddings.card_text("X", "Creature", "Flying, vigilance"))
    assert a != b


def test_searching_without_an_index_says_so(conn):
    with pytest.raises(embeddings.EmbeddingError, match="No embeddings"):
        synergy.search(conn, "make lots of elves")


# --- role detection --------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Add {G}.", "ramp"),
    ("Search your library for a basic land card.", "ramp"),
    ("Draw a card.", "card draw"),
    ("Destroy target creature.", "removal"),
    ("Destroy all creatures.", "board wipe"),
    ("Counter target spell.", "counterspell"),
    ("Create a 1/1 green Elf Warrior creature token.", "token maker"),
    ("As an additional cost to cast this spell, sacrifice a creature.", "sacrifice"),
])
def test_card_roles_are_read_off_rules_text(text, expected):
    assert expected in synergy.roles_of(text)


def test_sacrifice_does_not_require_an_activated_ability():
    """Demanding a colon told Eaten Alive it was not a sacrifice card."""
    assert "sacrifice" in synergy.roles_of(
        "As an additional cost to cast this spell, sacrifice a creature.")


@pytest.mark.parametrize("goal,expected", [
    ("I want lots of ramp", "ramp"),
    ("dump a huge amount of mana into one finisher", "ramp"),
    ("I need cheap removal", "removal"),
    ("go wide with tokens", "token maker"),
    ("sacrifice creatures for value", "sacrifice"),
    ("recur things out of the graveyard", "recursion"),
])
def test_goal_roles_are_read_from_plain_language(goal, expected):
    """A goal says 'dump mana'; a card says 'add {G}'. Reading the goal with
    the card patterns matched nothing, so half of every reason never fired."""
    assert expected in synergy.goal_roles_of(goal)


def test_every_goal_role_name_exists_as_a_card_role():
    """A mismatched name silently disables role pairing for that concept."""
    assert {n for n, _ in synergy.GOAL_PATTERNS} <= {n for n, _ in synergy.ROLES}


# --- reasons and weaknesses ------------------------------------------------

def test_a_matched_role_is_stated_plainly():
    reason = synergy._reason(0.7, ["ramp"], ["ramp"], [], {"ramp"})
    assert "Does ramp, which is what you asked for" in reason


def test_a_weak_match_names_its_weakness():
    """A 54% match labelled with why it surfaced beats hiding it."""
    weakness = synergy._weakness(0.54, [], ["lifegain"], [], {"ramp"})
    assert "Weak match" in weakness
    assert "lifegain" in weakness
    assert "not because it fits the theme" in weakness


def test_a_strong_match_with_shared_wording_needs_no_caveat():
    assert synergy._weakness(0.77, [], ["removal"], ["sacrifice"], {"sacrifice"}) is None


def test_a_matched_role_is_never_contradicted():
    assert synergy._weakness(0.4, ["ramp"], ["ramp"], [], {"ramp"}) is None


# --- the shelf -------------------------------------------------------------

def test_shelf_add_remove_and_note(conn):
    oid = oracle(conn, "Craterhoof Behemoth")
    assert shelf.add(conn, oid, "big finisher") is True
    assert shelf.add(conn, oid) is False, "adding twice is not an error"
    assert shelf.items(conn)[0]["note"] == "big finisher"
    assert shelf.remove(conn, oid) is True
    assert shelf.items(conn) == []


def test_re_adding_does_not_wipe_an_existing_note(conn):
    oid = oracle(conn, "Craterhoof Behemoth")
    shelf.add(conn, oid, "why I kept this")
    shelf.add(conn, oid, None)
    assert shelf.items(conn)[0]["note"] == "why I kept this"


def test_sending_to_a_deck_snapshots_first(conn):
    """A batch that turns out wrong should be one Restore away."""
    deck = decks.create_deck(conn, "Elfball")
    oid = oracle(conn, "Craterhoof Behemoth")
    shelf.add(conn, oid)
    shelf.send_to_deck(conn, deck, [oid])

    assert [c["name"] for c in decks.deck_cards(conn, deck)] == ["Craterhoof Behemoth"]
    assert shelf.items(conn) == [], "cards leave the shelf once placed"
    history = decks.history(conn, deck)
    assert any("from the shelf" in (h["summary"] or "") for h in history)


def test_cards_can_be_kept_on_the_shelf(conn):
    deck = decks.create_deck(conn, "D")
    oid = oracle(conn, "Craterhoof Behemoth")
    shelf.add(conn, oid)
    shelf.send_to_deck(conn, deck, [oid], keep_on_shelf=True)
    assert len(shelf.items(conn)) == 1


def test_sending_assigns_a_suggested_category(conn):
    deck = decks.create_deck(conn, "D")
    shelf.add(conn, oracle(conn, "Craterhoof Behemoth"))
    shelf.send_to_deck(conn, deck)
    assert decks.deck_cards(conn, deck)[0]["category"] == "Creature"


# --- packages --------------------------------------------------------------

def test_a_package_loads_onto_the_shelf_not_into_a_deck(conn):
    """The shelf is the only route into a deck, for packages too."""
    ids = [oracle(conn, "Craterhoof Behemoth"), oracle(conn, "Dwynen's Elite")]
    pkg = shelf.create_package(conn, "Green ramp suite", ids)
    assert shelf.packages(conn)[0]["cards"] == 2

    assert shelf.package_to_shelf(conn, pkg) == 2
    assert len(shelf.items(conn)) == 2
    assert shelf.delete_package(conn, pkg) is True


# --- the API ---------------------------------------------------------------

def test_shelf_api_roundtrip(client, conn):
    oid = oracle(conn, "Craterhoof Behemoth")
    assert client.post("/api/shelf", json={"oracle_id": oid, "note": "maybe"}).status_code == 201
    body = client.get("/api/shelf").json()
    assert body["items"][0]["note"] == "maybe"

    assert client.put(f"/api/shelf/{oid}", json={"note": "definitely"}).status_code == 200
    assert client.get("/api/shelf").json()["items"][0]["note"] == "definitely"
    assert client.delete(f"/api/shelf/{oid}").status_code == 204
    assert client.delete(f"/api/shelf/{oid}").status_code == 404


def test_shelving_an_unknown_card_is_rejected(client):
    assert client.post("/api/shelf", json={"oracle_id": "nope"}).status_code == 404


def test_synergy_without_an_index_explains_rather_than_500s(client):
    r = client.post("/api/synergy", json={"goal": "make lots of elves"})
    assert r.status_code == 400
    assert "No embeddings" in r.json()["detail"]


def test_there_is_no_endpoint_from_synergy_straight_into_a_deck(client):
    """Spec 6.3: a search result must never quietly modify a built deck. The
    only route is via the shelf."""
    from backend.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    for route in app.routes:
        for sub in getattr(getattr(route, "app", None), "routes", []):
            paths.add(getattr(sub, "path", ""))
    assert not any("synergy" in p and "deck" in p for p in paths)
