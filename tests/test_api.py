"""API-level tests.

The stats endpoint returned 500 intermittently on the first real run: FastAPI
runs a sync dependency and the sync path operation it feeds on different
threadpool workers, and sqlite3 refuses a connection used across threads.
Hitting the app repeatedly is what catches it -- a single request usually
lands on one thread and passes.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from backend import db, scryfall

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scryfall_cards.json"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """The test database, kept open so a test can set up rows directly."""
    path = tmp_path / "api.db"
    monkeypatch.setenv("GRIMOIRE_DB", str(path))
    c = db.open_db(path)
    scryfall.load_cards(c, json.loads(FIXTURES.read_text(encoding="utf-8")))
    yield c
    c.close()


@pytest.fixture()
def client(conn):
    from backend.main import app
    with TestClient(app) as c:
        yield c


def test_stats_survives_repeated_requests(client):
    for _ in range(25):
        r = client.get("/api/stats")
        assert r.status_code == 200, r.text
    # Counted from the fixture file rather than hard-coded, so adding a
    # fixture does not fail an unrelated test.
    assert r.json()["printings"] == len(
        json.loads(FIXTURES.read_text(encoding="utf-8")))


def test_healthz(client):
    assert client.get("/healthz").json()["ok"] is True


def test_search_is_owned_only_by_default(client):
    assert client.get("/api/cards?q=Llanowar").json()["total"] == 0
    assert client.get("/api/cards?q=Llanowar&owned=false").json()["total"] >= 1


def test_add_copy_requires_a_printing_id_not_an_oracle_id(client):
    hit = client.get("/api/cards?q=Craterhoof&owned=false").json()["results"][0]
    bad = client.post("/api/copies", json={"card_id": hit["oracle_id"]})
    assert bad.status_code == 404, "an oracle_id is not a printing"

    printings = client.get(f"/api/cards/{hit['oracle_id']}/printings").json()
    ok = client.post("/api/copies",
                     json={"card_id": printings[0]["id"], "quantity": 2, "foil": True})
    assert ok.status_code == 201
    assert ok.json()["added"] == 2
    assert client.get("/api/cards?q=Craterhoof").json()["total"] == 1


def test_locations_reject_duplicates(client):
    assert client.post("/api/locations", json={"name": "Binder A"}).status_code == 201
    assert client.post("/api/locations", json={"name": "Binder A"}).status_code == 409


def test_search_with_quotes_does_not_500(client):
    r = client.get("/api/cards", params={"q": 'Kongming, "Sleeping Dragon"', "owned": False})
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 1


# --- saved searches --------------------------------------------------------

def test_saved_search_roundtrip(client):
    assert client.get("/api/searches").json() == []
    r = client.post("/api/searches", json={"name": "Cheap green", "query": "c:g mv<=3"})
    assert r.status_code == 201
    rows = client.get("/api/searches").json()
    assert [x["name"] for x in rows] == ["Cheap green"]

    assert client.delete(f"/api/searches/{rows[0]['id']}").status_code == 204
    assert client.get("/api/searches").json() == []
    assert client.delete("/api/searches/999").status_code == 404


def test_an_unrunnable_search_cannot_be_saved(client):
    """A saved search that errors is worse than none: it fails later, when you
    have forgotten what the syntax was meant to be."""
    r = client.post("/api/searches", json={"name": "Broken", "query": "colour:zzz"})
    assert r.status_code == 400
    assert "not a colour" in r.json()["detail"]
    assert client.get("/api/searches").json() == []


def test_bad_query_returns_the_parser_message(client):
    r = client.get("/api/cards", params={"q": "foo:bar", "owned": False})
    assert r.status_code == 400
    assert "Unknown filter" in r.json()["detail"]


def test_search_reports_how_it_read_the_query(client):
    body = client.get("/api/cards", params={"q": "t:creature -is:deck", "owned": False}).json()
    assert body["terms"] == ["type:creature", "not is:deck"]


# --- SPA serving -----------------------------------------------------------

def test_deep_links_boot_the_app(client):
    """A refresh on a client-side route must serve index.html, not 404."""
    r = client.get("/decks/1")
    assert r.status_code == 200
    assert "<div id=\"root\">" in r.text


def test_static_fallback_refuses_path_traversal(client):
    for attack in ("../pyproject.toml", "..%2f..%2fpyproject.toml",
                   "assets/../../pyproject.toml"):
        r = client.get(f"/{attack}")
        assert r.status_code == 200, attack
        assert "[project]" not in r.text, f"{attack} escaped the static root"
        assert "<div id=\"root\">" in r.text


def test_api_routes_still_win_over_the_spa_fallback(client):
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/nope").status_code == 404


# --- card detail -----------------------------------------------------------

def test_card_detail_carries_the_printed_text_and_numbers(client, conn):
    oid = conn.execute(
        "SELECT oracle_id FROM cards WHERE name='Dwynen''s Elite'").fetchone()["oracle_id"]
    d = client.get(f"/api/cards/{oid}/detail").json()
    assert d["name"] == "Dwynen's Elite"
    assert d["power"] == "2" and d["toughness"] == "2"
    assert "Elf" in d["type_line"]
    assert d["oracle_text"]
    assert d["legalities"]["commander"] == "legal"
    assert d["tokens"][0]["name"] == "Elf Warrior"


def test_card_detail_splits_a_multiface_card_into_faces(client, conn):
    oid = conn.execute(
        "SELECT oracle_id FROM cards WHERE layout='transform'").fetchone()["oracle_id"]
    d = client.get(f"/api/cards/{oid}/detail").json()
    assert len(d["faces"]) == 2
    assert d["faces"][0]["name"] != d["faces"][1]["name"]
    # The trap this whole codebase is built around: root text is empty here.
    assert all(f["type_line"] for f in d["faces"])


def test_card_detail_says_where_your_copies_are(client, conn):
    row = conn.execute("SELECT id, oracle_id FROM cards WHERE name='Llanowar Elves' "
                       "LIMIT 1").fetchone()
    loc = conn.execute("INSERT INTO locations (name) VALUES ('Binder A')").lastrowid
    conn.execute("INSERT INTO copies (card_id, finish, foil, condition, location_id) "
                 "VALUES (?,'foil',1,'NM',?)", (row["id"], loc))
    conn.commit()

    d = client.get(f"/api/cards/{row['oracle_id']}/detail").json()
    assert d["owned"] == 1
    assert d["copies"][0]["location"] == "Binder A"
    assert d["copies"][0]["finish"] == "foil"
    assert d["copies"][0]["quantity"] == 1


def test_identical_copies_are_grouped_not_listed_one_by_one(client, conn):
    """41 identical Arcane Signets buried everything else in the panel."""
    row = conn.execute("SELECT id, oracle_id FROM cards WHERE name='Llanowar Elves' "
                       "LIMIT 1").fetchone()
    for _ in range(12):
        conn.execute("INSERT INTO copies (card_id, finish, foil, condition) "
                     "VALUES (?, 'normal', 0, 'NM')", (row["id"],))
    conn.commit()

    d = client.get(f"/api/cards/{row['oracle_id']}/detail").json()
    assert d["owned"] == 12, "the count is still every physical copy"
    assert len(d["copies"]) == 1, "but they collapse into one line"
    assert d["copies"][0]["quantity"] == 12


def test_card_detail_prefers_a_printing_you_own(client, conn):
    """The art shown should be the copy in the binder, not an arbitrary one."""
    rows = conn.execute("SELECT id, oracle_id FROM cards WHERE name='Llanowar Elves' "
                        "ORDER BY set_code").fetchall()
    conn.execute("INSERT INTO copies (card_id, finish, foil) VALUES (?, 'normal', 0)",
                 (rows[0]["id"],))
    conn.commit()
    d = client.get(f"/api/cards/{rows[0]['oracle_id']}/detail").json()
    assert d["card_id"] == rows[0]["id"]


def test_card_detail_honours_an_explicit_printing(client, conn):
    rows = conn.execute("SELECT id, oracle_id FROM cards WHERE name='Llanowar Elves' "
                        "ORDER BY set_code").fetchall()
    d = client.get(f"/api/cards/{rows[2]['oracle_id']}/detail",
                   params={"card_id": rows[2]["id"]}).json()
    assert d["card_id"] == rows[2]["id"]


def test_unknown_card_detail_is_404(client):
    assert client.get("/api/cards/not-a-real-id/detail").status_code == 404
