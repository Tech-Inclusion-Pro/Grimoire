"""Vault sync tests.

Every failure mode here is silent: the note still exists, it just quietly has
less in it than it did. So the rules are asserted directly rather than assumed.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from backend import db, decks, journal, scryfall, vault

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scryfall_cards.json"

DECK = {"name": "Elfball", "commander": "Marwyn, the Nurturer",
        "colors": ["G"], "format": "commander", "bracket_claimed": 3}
STATS = {"buildable_now": 22, "cards": 23}


@pytest.fixture()
def root(tmp_path):
    return tmp_path / "vault"


def entry(at, body, result=None):
    return vault.render_entry(at, body, result)


# --- filenames -------------------------------------------------------------

@pytest.mark.parametrize("name,expect", [
    ("Elfball", "Elfball.md"),
    ('Kongming, "Sleeping Dragon"', "Kongming, Sleeping Dragon.md"),
    ("Green/White tokens", "GreenWhite tokens.md"),
    ("  spaced   out  ", "spaced out.md"),
    ("", "Untitled deck.md"),
])
def test_deck_filenames_are_safe_and_readable(name, expect):
    assert vault.deck_filename(name) == expect


def test_deck_tag_matches_the_vault_style():
    assert vault.deck_tag("Edgar Vampires") == "edgar-vampires"
    assert vault.deck_tag('Kongming, "Sleeping Dragon"') == "kongming-sleeping-dragon"


# --- the eviction guard ----------------------------------------------------

def test_writing_is_refused_when_icloud_has_evicted_the_note(root):
    path = vault.write_deck_note(DECK, STATS, entries=[entry("2026-09-06T21:40:00", "x")], root=root)
    # Simulate eviction: real content gone, hidden placeholder left behind.
    vault.eviction_stub(path).write_text("")
    with pytest.raises(vault.VaultError, match="evicted"):
        vault.write_deck_note(DECK, STATS, root=root)


def test_writing_is_refused_over_an_empty_note(root):
    path = vault.write_deck_note(DECK, STATS, root=root)
    path.write_text("")
    with pytest.raises(vault.VaultError, match="empty"):
        vault.write_deck_note(DECK, STATS, root=root)


# --- atomic writes ---------------------------------------------------------

def test_a_failed_write_leaves_no_debris(root, monkeypatch):
    path = vault.deck_path(DECK["name"], root)
    path.parent.mkdir(parents=True)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(vault.os, "replace", boom)
    with pytest.raises(OSError):
        vault.atomic_write(path, "content")
    assert not path.exists()
    assert list(path.parent.glob(".grimoire-*")) == [], "temp file left behind"


def test_write_goes_through_a_rename(root, monkeypatch):
    """Rename is what makes the write atomic; a plain open-and-write would let
    a sync engine upload a half-written note."""
    calls = []
    real = vault.os.replace
    monkeypatch.setattr(vault.os, "replace", lambda a, b: (calls.append((a, b)), real(a, b))[1])
    vault.write_deck_note(DECK, STATS, root=root)
    assert len(calls) == 1


# --- the one-way rule ------------------------------------------------------

def test_everything_the_user_wrote_survives_a_resync(root):
    path = vault.write_deck_note(DECK, STATS, entries=[entry("2026-09-06T21:40:00", "First game.", "win")], root=root)

    text = path.read_text(encoding="utf-8")
    text = text.replace("_Yours to write. Grimoire never edits this section._",
                        "Go wide on elves and dump mana into one finisher.")
    text = text.replace("  - elfball", "  - elfball\n  - my-own-tag")
    text = text.replace("---\n\n## What", "Mood: focused\n---\n\n## What")
    text = text.replace(vault.END_MARKER, vault.END_MARKER + "\n\n### Typed in Obsidian\nKeep me.")
    text += "\n## My own section\nKeep me too.\n"
    path.write_text(text, encoding="utf-8")

    vault.write_deck_note(
        DECK, {"buildable_now": 23, "cards": 23},
        entries=[entry("2026-09-06T21:40:00", "First game.", "win"),
                 entry("2026-09-08T19:05:00", "Second game.", "loss")],
        overview=vault.render_overview({"How it feels to play": "Fast."}, "qwen2.5:3b", "2026-09-09 16:40"),
        root=root)

    after = path.read_text(encoding="utf-8")
    for survivor in ("Go wide on elves and dump mana into one finisher.",
                     "my-own-tag", "Mood: focused",
                     "### Typed in Obsidian", "Keep me.",
                     "## My own section", "Keep me too."):
        assert survivor in after, f"lost: {survivor}"

    assert "owned: 23" in after, "managed frontmatter must update"
    assert "Second game." in after
    assert after.count("First game.") == 1, "entries duplicated on resync"


def test_entries_are_replaced_not_appended(root):
    """The block is rebuilt from the database each sync, so deleting an entry
    in the app removes it from the note. Appending would duplicate everything."""
    vault.write_deck_note(DECK, STATS, entries=[entry("2026-09-06T21:40:00", "One."),
                                                entry("2026-09-07T21:40:00", "Two.")], root=root)
    path = vault.write_deck_note(DECK, STATS, entries=[entry("2026-09-06T21:40:00", "One.")], root=root)
    text = path.read_text(encoding="utf-8")
    assert text.count("One.") == 1
    assert "Two." not in text


def test_a_note_with_no_frontmatter_gains_one_without_losing_the_body(root):
    path = vault.deck_path(DECK["name"], root)
    path.parent.mkdir(parents=True)
    path.write_text("Some notes I wrote before Grimoire existed.\n", encoding="utf-8")
    vault.write_deck_note(DECK, STATS, root=root)
    after = path.read_text(encoding="utf-8")
    assert after.startswith("---")
    assert "Some notes I wrote before Grimoire existed." in after


def test_managed_frontmatter_is_replaced_not_duplicated(root):
    vault.write_deck_note(DECK, STATS, root=root)
    path = vault.write_deck_note(DECK, {"buildable_now": 5, "cards": 9}, root=root)
    text = path.read_text(encoding="utf-8")
    assert text.count("owned:") == 1
    assert "owned: 5" in text
    assert text.count("type: mtg-deck") == 1


def test_frontmatter_matches_the_vaults_conventions(root):
    path = vault.write_deck_note(DECK, STATS, root=root)
    text = path.read_text(encoding="utf-8")
    # Obsidian Bases filters on `type`, and the vault groups by `project`.
    assert "type: mtg-deck" in text
    assert 'project: "[[PROJECT - MTG Decks, Play, and Events]]"' in text
    assert 'Time: "[[' in text


# --- journal + generation --------------------------------------------------

@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_DB", str(tmp_path / "j.db"))
    c = db.open_db(tmp_path / "j.db")
    scryfall.load_cards(c, json.loads(FIXTURES.read_text(encoding="utf-8")))
    yield c
    c.close()


def test_entries_and_record(conn):
    deck = decks.create_deck(conn, "Elfball")
    journal.add_entry(conn, deck, "Won on turn seven.", "win")
    journal.add_entry(conn, deck, "Lost to combo.", "loss")
    journal.add_entry(conn, deck, "Just goldfished.")
    assert journal.record(conn, deck) == {"wins": 1, "losses": 1, "unrecorded": 1}
    assert [e["body"] for e in journal.entries(conn, deck)][0] == "Won on turn seven."


def test_a_bad_result_value_is_rejected(conn):
    deck = decks.create_deck(conn, "D")
    with pytest.raises(ValueError):
        journal.add_entry(conn, deck, "x", "draw")


def test_overview_refuses_until_there_is_enough_written(conn):
    deck = decks.create_deck(conn, "D")
    journal.add_entry(conn, deck, "One game.")
    with pytest.raises(journal.GenerationError, match="reads what you wrote"):
        journal.generate_overview(conn, deck, "qwen2.5:3b")


def test_the_prompt_contains_the_journal_and_never_the_decklist(conn):
    deck = decks.create_deck(conn, "D")
    oid = conn.execute("SELECT oracle_id FROM cards WHERE name='Craterhoof Behemoth'").fetchone()[0]
    decks.set_card(conn, deck, oid, 1)
    journal.add_entry(conn, deck, "Marwyn got huge.", "win")

    prompt = journal.build_prompt(journal.entries(conn, deck))
    assert "Marwyn got huge." in prompt
    assert "Craterhoof" not in prompt, "the decklist leaked into the prompt"
    assert "must not guess" in prompt
    for section in journal.SECTIONS:
        assert section in prompt


def test_sections_parse_out_of_a_loosely_formatted_reply():
    reply = ("## How it feels to play\nQuick.\n\n"
             "**When to bring it**\nAgainst slow tables.\n\n"
             "Tricks and lines:\nHold up mana.\n\n"
             "### What you keep running into\nCombo decks.\n")
    got = journal.parse_sections(reply)
    assert got["How it feels to play"] == "Quick."
    assert got["When to bring it"] == "Against slow tables."
    assert got["Tricks and lines"] == "Hold up mana."
    assert got["What you keep running into"] == "Combo decks."


def test_sync_writes_the_deck_note(conn, root):
    deck = decks.create_deck(conn, "Elfball", "commander")
    oid = conn.execute("SELECT oracle_id FROM cards WHERE name='Craterhoof Behemoth'").fetchone()[0]
    decks.set_card(conn, deck, oid, 1)
    journal.add_entry(conn, deck, "Won on the back of a big swing.", "win")

    result = journal.sync_deck(conn, deck, root=root)
    text = pathlib.Path(result["path"]).read_text(encoding="utf-8")
    assert "Won on the back of a big swing." in text
    assert "colors:\n  - G" in text, "colour identity comes from the actual cards"
    assert conn.execute("SELECT vault_path FROM decks WHERE id=?", (deck,)).fetchone()[0]


def test_deleting_an_entry_removes_it_from_the_note(conn, root):
    deck = decks.create_deck(conn, "Elfball")
    a = journal.add_entry(conn, deck, "First game.", "win")
    journal.add_entry(conn, deck, "Second game.", "loss")
    journal.sync_deck(conn, deck, root=root)

    journal.delete_entry(conn, a)
    result = journal.sync_deck(conn, deck, root=root)
    text = pathlib.Path(result["path"]).read_text(encoding="utf-8")
    assert "First game." not in text
    assert "Second game." in text


def test_managed_frontmatter_keeps_a_stable_order(root):
    """A key absent on the first write used to be appended at the end, so
    `commander` appeared below `updated` the first time a deck got one."""
    no_commander = {**DECK, "commander": None}
    vault.write_deck_note(no_commander, STATS, root=root)
    path = vault.write_deck_note(DECK, STATS, root=root)

    keys = [ln.split(":")[0] for ln in path.read_text(encoding="utf-8").split("\n")[1:]
            if ln and not ln.startswith(" ") and ":" in ln and not ln.startswith("---")]
    managed = [k for k in keys if k in vault.MANAGED_KEYS]
    assert managed == sorted(managed, key=vault.MANAGED_KEYS.index)
    assert managed.index("commander") < managed.index("updated")
