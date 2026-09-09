"""CSV import tests.

Built against the shapes that actually broke on the real ManaBox export:
etched foil as a third finish, MTGO-only printing ids for cards held in paper,
and card names containing commas and quotes.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from backend import csv_import, db, scryfall

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scryfall_cards.json"

MANABOX_HEADER = ("Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,"
                  "ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,"
                  "Condition,Language,Purchase price currency,Added")


@pytest.fixture()
def conn():
    c = db.open_db(":memory:")
    scryfall.load_cards(c, json.loads(FIXTURES.read_text(encoding="utf-8")))
    yield c
    c.close()


def card(conn, name):
    return conn.execute("SELECT * FROM cards WHERE name=? LIMIT 1", (name,)).fetchone()


def manabox_csv(*lines: str) -> str:
    return MANABOX_HEADER + "\n" + "\n".join(lines) + "\n"


def plan_for(conn, text):
    headers, rows = csv_import.read_csv(text)
    return csv_import.build_plan(conn, rows, csv_import.detect_mapping(headers),
                                 csv_import.detect_format(headers))


# --- format and column detection -------------------------------------------

def test_manabox_is_detected():
    headers, _ = csv_import.read_csv(manabox_csv("A,B,C,1,normal,rare,1,1,x,0,false,false,near_mint,en,USD,2026-01-01"))
    assert csv_import.detect_format(headers) == "ManaBox"


def test_scryfall_id_column_wins_over_a_generic_id_column():
    headers = ["Name", "ID", "Scryfall ID", "Quantity"]
    mapping = csv_import.detect_mapping(headers)
    assert mapping["scryfall_id"] == "Scryfall ID"


def test_unknown_format_still_maps_what_it_can():
    headers = ["Card Name", "Qty", "Edition", "Card Number"]
    mapping = csv_import.detect_mapping(headers)
    assert mapping["name"] == "Card Name"
    assert mapping["quantity"] == "Qty"
    assert mapping["collector_num"] == "Card Number"
    assert mapping["scryfall_id"] is None


def test_a_file_with_no_rows_is_rejected():
    with pytest.raises(csv_import.ImportError_):
        csv_import.read_csv(MANABOX_HEADER + "\n")


# --- parsing the awkward values --------------------------------------------

def test_name_with_comma_and_quotes_survives_csv_parsing(conn):
    c = card(conn, 'Kongming, "Sleeping Dragon"')
    text = manabox_csv(
        f'"Kongming, ""Sleeping Dragon""",{c["set_code"]},Set,{c["collector_num"]},'
        f'normal,rare,1,1,{c["id"]},0.5,false,false,near_mint,en,USD,2026-01-01')
    plan = plan_for(conn, text)
    assert plan.unresolved == []
    assert plan.copies[0].name == 'Kongming, "Sleeping Dragon"'
    assert plan.copies[0].how == "id"


def test_etched_is_a_third_finish_not_a_kind_of_foil(conn):
    c = card(conn, "Craterhoof Behemoth")
    text = manabox_csv(
        f'Craterhoof Behemoth,{c["set_code"]},S,{c["collector_num"]},etched,mythic,1,1,'
        f'{c["id"]},9.99,false,false,near_mint,en,USD,2026-01-01')
    plan = plan_for(conn, text)
    assert plan.copies[0].finish == "etched"


@pytest.mark.parametrize("raw,expect", [
    ("foil", "foil"), ("normal", "normal"), ("etched", "etched"),
    ("", "normal"), ("false", "normal"), ("true", "foil"), ("NONFOIL", "normal"),
])
def test_finish_values_normalise(raw, expect):
    assert csv_import._norm_finish(raw) == expect


def test_condition_and_date_normalise():
    assert csv_import._norm_condition("near_mint") == "NM"
    assert csv_import._norm_condition("Lightly Played") == "LP"
    assert csv_import._norm_date("2025-11-05T04:37:23.386Z") == "2025-11-05"
    assert csv_import._norm_date("") is None


def test_blank_purchase_price_is_none_not_zero():
    assert csv_import._to_float("") is None
    assert csv_import._to_float("$12.30") == 12.30
    assert csv_import._to_float("1,234.5") == 1234.5


# --- resolution ------------------------------------------------------------

def test_scryfall_id_is_used_when_present(conn):
    c = card(conn, "Dwynen's Elite")
    text = manabox_csv(f"Wrong Name,zzz,S,999,normal,rare,1,1,{c['id']},0,false,false,near_mint,en,USD,2026-01-01")
    plan = plan_for(conn, text)
    assert plan.copies[0].card_id == c["id"]
    assert plan.copies[0].how == "id"


def test_mtgo_only_printing_falls_back_to_paper(conn):
    """ManaBox records MTGO-only printing ids for cards held in paper. Those
    ids are not in the card table, so the row must fall through to set+number
    or name rather than being dropped."""
    c = card(conn, "Craterhoof Behemoth")
    bogus = "00000000-0000-0000-0000-000000000000"
    text = manabox_csv(
        f'Craterhoof Behemoth,{c["set_code"]},S,{c["collector_num"]},normal,mythic,1,1,'
        f'{bogus},1,false,false,near_mint,en,USD,2026-01-01')
    plan = plan_for(conn, text)
    assert plan.unresolved == []
    p = plan.copies[0]
    assert p.card_id == c["id"]
    assert p.how == "set+number"
    assert "MTGO-only" in p.note
    assert p in plan.substitutions, "a substitution must be reported, not silent"


def test_name_only_fallback_prefers_paper(conn):
    text = manabox_csv("Craterhoof Behemoth,,,,normal,mythic,1,1,,1,false,false,near_mint,en,USD,2026-01-01")
    plan = plan_for(conn, text)
    assert plan.copies[0].how == "name"
    row = conn.execute("SELECT digital FROM cards WHERE id=?", (plan.copies[0].card_id,)).fetchone()
    assert row["digital"] == 0


def test_multiface_card_resolves_by_its_front_face_name(conn):
    """Exports write 'Fire', the card table stores 'Fire // Ice'."""
    text = manabox_csv("Fire,,,,normal,uncommon,1,1,,1,false,false,near_mint,en,USD,2026-01-01")
    plan = plan_for(conn, text)
    assert plan.unresolved == []
    assert card(conn, "Fire // Ice")["id"] == plan.copies[0].card_id


def test_a_genuinely_unknown_card_is_reported_not_guessed(conn):
    text = manabox_csv("Not A Real Card,zzz,S,1,normal,rare,1,1,,1,false,false,near_mint,en,USD,2026-01-01")
    plan = plan_for(conn, text)
    assert plan.copies == []
    assert len(plan.unresolved) == 1


# --- planning writes nothing -----------------------------------------------

def test_building_a_plan_writes_nothing(conn):
    c = card(conn, "Dwynen's Elite")
    text = manabox_csv(f"Dwynen's Elite,{c['set_code']},S,{c['collector_num']},normal,uncommon,4,1,{c['id']},1,false,false,near_mint,en,USD,2026-01-01")
    plan_for(conn, text)
    assert conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0] == 0


def test_quantity_becomes_one_row_per_physical_card(conn):
    c = card(conn, "Dwynen's Elite")
    text = manabox_csv(f"Dwynen's Elite,{c['set_code']},S,{c['collector_num']},foil,uncommon,4,1,{c['id']},1.5,false,false,near_mint,en,USD,2025-11-05T04:37:23.386Z")
    plan = plan_for(conn, text)
    assert plan.total_cards == 4
    csv_import.apply_plan(conn, plan)

    rows = conn.execute("SELECT * FROM copies").fetchall()
    assert len(rows) == 4
    assert {r["finish"] for r in rows} == {"foil"}
    assert {r["foil"] for r in rows} == {1}, "foil flag derived from finish"
    assert {r["condition"] for r in rows} == {"NM"}
    assert {r["acquired_at"] for r in rows} == {"2025-11-05"}
    assert {r["purchase_price"] for r in rows} == {1.5}


def test_apply_files_everything_into_one_location(conn):
    c = card(conn, "Dwynen's Elite")
    loc = conn.execute("INSERT INTO locations (name) VALUES ('Bulk box')").lastrowid
    conn.commit()
    text = manabox_csv(f"Dwynen's Elite,{c['set_code']},S,{c['collector_num']},normal,uncommon,2,1,{c['id']},1,false,false,near_mint,en,USD,2026-01-01")
    csv_import.apply_plan(conn, plan_for(conn, text), location_id=loc)
    assert conn.execute("SELECT COUNT(*) FROM copies WHERE location_id=?", (loc,)).fetchone()[0] == 2


def test_summary_reports_what_will_happen(conn):
    c = card(conn, "Dwynen's Elite")
    text = manabox_csv(
        f"Dwynen's Elite,{c['set_code']},S,{c['collector_num']},etched,uncommon,3,1,{c['id']},1,false,false,near_mint,en,USD,2026-01-01",
        "Not A Real Card,zzz,S,1,normal,rare,1,1,,1,false,false,near_mint,en,USD,2026-01-01")
    s = plan_for(conn, text).summary()
    assert s["source_format"] == "ManaBox"
    assert s["rows"] == 2
    assert s["cards"] == 3
    assert s["unresolved"] == 1
    assert s["finishes"] == {"etched": 1}
