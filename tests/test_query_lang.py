"""Search syntax tests.

Two classes of failure matter here. A query that returns the wrong cards is
obvious. A query that silently returns *everything* because a filter was
misspelled is not, and is the reason unknown syntax raises instead of being
ignored.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from backend import db, query_lang, queries, scryfall
from backend.query_lang import QueryError

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scryfall_cards.json"


@pytest.fixture(scope="module")
def conn():
    c = db.open_db(":memory:")
    scryfall.load_cards(c, json.loads(FIXTURES.read_text(encoding="utf-8")))
    yield c
    c.close()


def find(conn, q, owned=False):
    return {r["name"] for r in
            queries.search_cards(conn, q, owned_only=owned, limit=100)["results"]}


# --- parsing ---------------------------------------------------------------

def test_bare_word_is_a_name_search():
    node = query_lang.parse("Llanowar")
    assert isinstance(node, query_lang.Term)
    assert (node.field, node.value) == ("name", "Llanowar")


def test_quoted_values_keep_their_spaces():
    node = query_lang.parse('o:"target creature"')
    assert (node.field, node.value) == ("oracle", "target creature")


def test_implicit_and_between_terms():
    node = query_lang.parse("t:creature c:g")
    assert isinstance(node, query_lang.And)
    assert len(node.children) == 2


def test_or_binds_looser_than_and():
    # 'a b or c' is '(a and b) or c'
    node = query_lang.parse("t:creature c:g or c:u")
    assert isinstance(node, query_lang.Or)
    assert isinstance(node.children[0], query_lang.And)


def test_parentheses_override_precedence():
    node = query_lang.parse("t:creature (c:g or c:u)")
    assert isinstance(node, query_lang.And)
    assert isinstance(node.children[1], query_lang.Or)


def test_leading_minus_negates():
    node = query_lang.parse("-is:deck")
    assert isinstance(node, query_lang.Not)


def test_a_hyphen_inside_a_word_is_not_negation():
    """'Lim-Dul' must not parse as 'Lim' AND NOT 'Dul'."""
    node = query_lang.parse("Lim-Dul")
    assert isinstance(node, query_lang.Term)
    assert node.value == "Lim-Dul"


def test_blank_query_is_no_filter():
    assert query_lang.parse("   ") is None
    assert query_lang.compile_query("") is None


# --- errors, never silent no-ops -------------------------------------------

@pytest.mark.parametrize("bad,expect", [
    ("foo:bar", "Unknown filter"),
    ("c:zzz", "not a colour"),
    ("mv:many", "not a number"),
    ("r:superrare", "not a rarity"),
    ("is:sparkly", "is not something Grimoire knows"),
    ('o:"unclosed', "Unclosed quote"),
    ("(t:elf", "Unclosed '('"),
    ("t:elf)", "Unbalanced ')'"),
    ("t:", "needs a value"),
])
def test_bad_syntax_explains_itself(bad, expect):
    with pytest.raises(QueryError) as e:
        query_lang.compile_query(bad)
    assert expect in str(e.value)


# --- semantics against real card data --------------------------------------

def test_type_and_text_search(conn):
    assert "Craterhoof Behemoth" in find(conn, "t:creature c:g")
    assert "Craterhoof Behemoth" in find(conn, "o:haste")
    assert "Craterhoof Behemoth" not in find(conn, "t:instant")


def test_exact_name_matches_a_multiface_card_by_its_front(conn):
    """!"Fire" must find "Fire // Ice" -- the exact-name form has to know that
    a multi-face card's stored name is the combined one."""
    assert "Fire // Ice" in find(conn, '!"Fire"')
    assert find(conn, '!"Llanowar Elves"') == {"Llanowar Elves"}


def test_double_faced_cards_are_found_by_back_face_text(conn):
    assert "Delver of Secrets // Insectile Aberration" in find(conn, "o:flying")


def test_colour_contains_versus_exactly(conn):
    assert "Fire // Ice" in find(conn, "c:r")        # contains red
    assert "Fire // Ice" in find(conn, "c:ru")       # contains both
    assert "Fire // Ice" in find(conn, "c=ru")       # is exactly RU
    assert "Fire // Ice" not in find(conn, "c=r")    # not mono-red


def test_colour_letter_order_does_not_matter(conn):
    assert find(conn, "c=ru") == find(conn, "c=ur")


def test_guild_names_are_colour_shorthand(conn):
    assert find(conn, "c:izzet") == find(conn, "c:ur")


def test_identity_defaults_to_subset(conn):
    """id:g means 'legal in a mono-green deck', so a Simic card must not match."""
    green = find(conn, "id:g")
    assert "Craterhoof Behemoth" in green
    assert "Fire // Ice" not in green


def test_mana_value_comparisons(conn):
    assert "Dwynen's Elite" in find(conn, "mv=2")
    assert "Dwynen's Elite" in find(conn, "mv<=2")
    assert "Dwynen's Elite" not in find(conn, "mv<2")
    assert find(conn, "cmc=2") == find(conn, "mv=2"), "cmc is an alias for mv"


def test_power_comparison_ignores_star_creatures(conn):
    """CAST('*' AS REAL) is 0, so an unguarded pow<=0 would sweep in every
    '*'-power creature."""
    sql, params = query_lang.compile_query("pow<=0")
    assert "GLOB" in sql, "the non-numeric guard is missing"
    assert "Dwynen's Elite" in find(conn, "pow=2")
    assert "Dwynen's Elite" not in find(conn, "pow>=3")


def test_negation_partitions_the_results(conn):
    everything = find(conn, "t:creature")
    elves = find(conn, "t:creature t:elf")
    others = find(conn, "t:creature -t:elf")
    assert elves | others == everything
    assert not (elves & others)


def test_or_widens(conn):
    assert find(conn, "t:instant or t:sorcery") >= find(conn, "t:instant")


def test_is_vanilla(conn):
    assert "Grizzly Bears" in find(conn, "is:vanilla")
    assert "Craterhoof Behemoth" not in find(conn, "is:vanilla")
    assert "Ghazbán Ogre" not in find(conn, "is:vanilla"), "it has an upkeep trigger"


def test_is_dfc(conn):
    dfc = find(conn, "is:dfc")
    assert "Delver of Secrets // Insectile Aberration" in dfc
    assert "Agadeem's Awakening // Agadeem, the Undercrypt" in dfc
    assert "Fire // Ice" not in dfc, "split is not double-faced"


def test_search_finds_names_with_commas_quotes_and_accents(conn):
    assert find(conn, "Kongming") == {'Kongming, "Sleeping Dragon"'}
    assert find(conn, "Ghazb") == {"Ghazbán Ogre"}


def test_digital_only_printings_never_appear(conn):
    """Masters Edition and Arena sets are MTGO/Arena-only. They are real
    Scryfall cards but can never be a physical copy, so search must not offer
    them -- the fixture set deliberately pins the paper Ghazbán Ogre."""
    row = conn.execute(
        "SELECT digital, set_code FROM cards WHERE name='Ghazbán Ogre'").fetchone()
    assert row["digital"] == 0 and row["set_code"] == "chr"


# --- ownership filters, and the oracle-scoping rule ------------------------

@pytest.fixture()
def owned(conn):
    """Own two printings of Llanowar Elves: one foil in Binder A, one in Bulk."""
    conn.execute("DELETE FROM copies")
    conn.execute("DELETE FROM locations")
    conn.execute("INSERT INTO locations (id,name) VALUES (1,'Binder A'),(2,'Bulk box')")
    prints = conn.execute(
        "SELECT id FROM cards WHERE name='Llanowar Elves' ORDER BY set_code").fetchall()
    conn.execute("INSERT INTO copies (card_id,finish,foil,location_id) "
                 "VALUES (?,'foil',1,1)", (prints[0]["id"],))
    conn.execute("INSERT INTO copies (card_id,finish,foil,location_id) "
                 "VALUES (?,'normal',0,2)", (prints[1]["id"],))
    conn.commit()
    yield conn
    conn.execute("DELETE FROM copies")
    conn.execute("DELETE FROM locations")
    conn.commit()


def test_is_foil_and_is_owned(owned):
    assert find(owned, "is:owned") == {"Llanowar Elves"}
    assert find(owned, "is:foil") == {"Llanowar Elves"}
    assert find(owned, "is:nonfoil") == {"Llanowar Elves"}
    assert find(owned, "is:lent") == set()


def test_location_filter_answers_where_is_this_card(owned):
    assert find(owned, "loc:binder") == {"Llanowar Elves"}
    assert find(owned, 'loc:"bulk box"') == {"Llanowar Elves"}
    assert find(owned, "loc:nowhere") == set()


def test_a_card_in_two_places_matches_both_locations(owned):
    """Predicates are oracle-scoped, so one card owned in two printings filed
    in two boxes matches both loc: filters. Counting locations by summing
    loc: results will therefore over-count, and that is correct."""
    assert find(owned, "loc:binder") == find(owned, 'loc:"bulk box"')


def test_set_filter_does_not_narrow_the_owned_count(owned):
    """Compiled inline, s:<set> would also decide which printings the
    aggregate counted, so a card owned twice would report owning once."""
    row = queries.search_cards(owned, "is:owned", owned_only=True)["results"][0]
    assert row["owned"] == 2

    set_code = owned.execute(
        "SELECT set_code FROM cards WHERE name='Llanowar Elves' ORDER BY set_code"
    ).fetchone()["set_code"]
    row = queries.search_cards(owned, f"s:{set_code}", owned_only=True)["results"][0]
    assert row["owned"] == 2, "still owns two copies, not one"


def test_is_deck_and_wishlist(owned):
    oracle = owned.execute(
        "SELECT oracle_id FROM cards WHERE name='Craterhoof Behemoth'").fetchone()["oracle_id"]
    owned.execute("INSERT INTO decks (id,name,format) VALUES (1,'Elfball','commander')")
    owned.execute("INSERT INTO deck_cards (deck_id,oracle_id,quantity,wishlist) VALUES (1,?,1,1)",
                  (oracle,))
    owned.commit()
    try:
        assert find(owned, "is:deck") == {"Craterhoof Behemoth"}
        assert find(owned, "is:wishlist") == {"Craterhoof Behemoth"}
        assert "Craterhoof Behemoth" not in find(owned, "-is:deck t:creature")
    finally:
        owned.execute("DELETE FROM deck_cards")
        owned.execute("DELETE FROM decks")
        owned.commit()


def test_ownership_filters_are_not_correlated_subqueries():
    """The correlated EXISTS form made `is:foil` take 8.8 seconds against the
    real database by re-running per oracle_id."""
    sql, _ = query_lang.compile_query("is:foil")
    assert "EXISTS" not in sql
    assert "oracle_id IN (SELECT" in sql


# --- price versus value ----------------------------------------------------

def test_usd_and_value_ask_different_questions(owned):
    """usd: is 'some printing costs this much'; value: is 'a copy I hold is
    worth this much'. Conflating them made a usd>=40 search return cards whose
    owned printing cost 25 cents."""
    conn = owned
    prints = conn.execute(
        "SELECT id FROM cards WHERE name='Llanowar Elves' ORDER BY set_code").fetchall()
    # Make one unowned printing expensive, and the owned ones cheap.
    conn.execute("UPDATE cards SET price_usd = 0.25 WHERE name='Llanowar Elves'")
    conn.execute("UPDATE cards SET price_usd = 99.0 WHERE id = ?", (prints[2]["id"],))
    conn.commit()

    assert "Llanowar Elves" in find(conn, "usd>=40"), "a printing is expensive"
    assert "Llanowar Elves" not in find(conn, "value>=40"), "but not one you hold"

    # prints[0] is the copy held as FOIL, so its value comes from the foil
    # price. Setting price_usd on it would leave the foil price NULL and the
    # copy would drop out of value: entirely.
    conn.execute("UPDATE cards SET price_usd_foil = 99.0 WHERE id = ?", (prints[0]["id"],))
    conn.commit()
    assert "Llanowar Elves" in find(conn, "value>=40"), "now you hold the pricey one"


def test_a_copy_with_no_price_for_its_finish_is_worth_nothing_not_wrong(owned):
    """A foil with no foil price must not silently fall back to the non-foil
    price -- that would overstate the collection. It is unknown, and unknown
    reads as nothing."""
    conn = owned
    conn.execute("UPDATE cards SET price_usd = 50.0, price_usd_foil = NULL "
                 "WHERE name='Llanowar Elves'")
    conn.commit()
    row = queries.search_cards(conn, "is:owned", owned_only=True)["results"][0]
    assert row["value"] == 50.0, "only the non-foil copy has a known value"


def test_results_report_what_the_held_copies_are_worth(owned):
    conn = owned
    conn.execute("UPDATE cards SET price_usd = 2.0, price_usd_foil = 10.0 "
                 "WHERE name='Llanowar Elves'")
    conn.commit()
    row = queries.search_cards(conn, "is:owned", owned_only=True)["results"][0]
    # Two copies: one foil at 10, one normal at 2.
    assert row["value"] == 12.0
    assert row["price_usd"] == 2.0, "cheapest printing, for acquiring"
