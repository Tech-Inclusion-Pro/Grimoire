"""Deck endpoints."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .. import db, decks, query_lang

router = APIRouter(tags=["decks"], prefix="/decks")


def get_db():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_db)]


def _require(conn, deck_id: int):
    row = conn.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such deck")
    return row


class NewDeck(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    format: str = "commander"
    commander_oracle_id: str | None = None
    bracket_claimed: int | None = Field(None, ge=1, le=5)


class DeckEdit(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=160)
    format: str | None = None
    commander_oracle_id: str | None = None
    bracket_claimed: int | None = Field(None, ge=1, le=5)
    clear_commander: bool = False


class CardSlot(BaseModel):
    oracle_id: str
    quantity: int = Field(1, ge=0, le=99)
    category: str | None = None
    tag: str | None = None
    wishlist: bool | None = None
    print_pref: str | None = None


class BulkAdd(BaseModel):
    text: str
    apply: bool = False


@router.get("")
def api_list(conn: Conn) -> list[dict[str, Any]]:
    return decks.list_decks(conn)


@router.post("", status_code=201)
def api_create(conn: Conn, body: NewDeck) -> dict[str, Any]:
    deck_id = decks.create_deck(conn, body.name, body.format,
                                body.commander_oracle_id, body.bracket_claimed)
    return {"id": deck_id, "name": body.name}


@router.get("/{deck_id}")
def api_get(conn: Conn, deck_id: int, q: str = "") -> dict[str, Any]:
    deck = _require(conn, deck_id)
    try:
        cards = decks.deck_cards(conn, deck_id, q)
    except query_lang.QueryError as e:
        raise HTTPException(400, str(e)) from e
    return {"deck": dict(deck), "cards": cards,
            "stats": decks.deck_stats(conn, deck_id),
            "tokens": decks.tokens_needed(conn, deck_id)}


@router.patch("/{deck_id}")
def api_edit(conn: Conn, deck_id: int, body: DeckEdit) -> dict[str, Any]:
    _require(conn, deck_id)
    if body.commander_oracle_id and not conn.execute(
            "SELECT 1 FROM cards WHERE oracle_id=? LIMIT 1",
            (body.commander_oracle_id,)).fetchone():
        raise HTTPException(404, "no such card")

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.format is not None:
        fields["format"] = body.format
    if body.bracket_claimed is not None:
        fields["bracket_claimed"] = body.bracket_claimed
    # An explicit flag, because None already means "leave it alone".
    if body.clear_commander:
        fields["commander_oracle_id"] = None
    elif body.commander_oracle_id:
        fields["commander_oracle_id"] = body.commander_oracle_id

    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE decks SET {sets}, updated_at=? WHERE id=?",
                     [*fields.values(), decks.now(), deck_id])
        conn.commit()
    return dict(_require(conn, deck_id))


@router.delete("/{deck_id}", status_code=204)
def api_delete(conn: Conn, deck_id: int) -> None:
    if not decks.delete_deck(conn, deck_id):
        raise HTTPException(404, "no such deck")


@router.put("/{deck_id}/cards")
def api_set_card(conn: Conn, deck_id: int, body: CardSlot) -> dict[str, Any]:
    _require(conn, deck_id)
    if not conn.execute("SELECT 1 FROM cards WHERE oracle_id=? LIMIT 1",
                        (body.oracle_id,)).fetchone():
        raise HTTPException(404, "no such card")
    decks.set_card(conn, deck_id, body.oracle_id, body.quantity, body.category,
                   body.tag, body.wishlist, body.print_pref)
    return decks.deck_stats(conn, deck_id)


@router.post("/{deck_id}/bulk")
def api_bulk(conn: Conn, deck_id: int, body: BulkAdd) -> dict[str, Any]:
    """Paste a decklist. Dry run by default -- nothing is written until
    `apply` is true, so the unresolved lines can be reviewed first."""
    _require(conn, deck_id)
    parsed = decks.parse_decklist(body.text)
    result = decks.resolve_names(conn, parsed)
    if body.apply and result["resolved"]:
        n = len(result["resolved"])
        decks.snapshot(conn, deck_id,
                       f"before adding {n} card{'' if n == 1 else 's'}")
        result["added"] = decks.add_many(conn, deck_id, result["resolved"])
        result["stats"] = decks.deck_stats(conn, deck_id)
    return result


@router.get("/{deck_id}/playtest")
def api_playtest(conn: Conn, deck_id: int) -> dict[str, Any]:
    """Everything a goldfish session needs, fetched once. Shuffling, drawing
    and zone moves all happen in the browser — there is nothing to persist and
    no rules to enforce."""
    _require(conn, deck_id)
    try:
        return decks.playtest_deck(conn, deck_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/{deck_id}/history")
def api_history(conn: Conn, deck_id: int) -> list[dict[str, Any]]:
    _require(conn, deck_id)
    return decks.history(conn, deck_id)


@router.post("/{deck_id}/history/{history_id}/restore")
def api_restore(conn: Conn, deck_id: int, history_id: int) -> dict[str, Any]:
    _require(conn, deck_id)
    try:
        n = decks.restore(conn, deck_id, history_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return {"restored": n, "stats": decks.deck_stats(conn, deck_id)}


@router.get("/{deck_id}/export", response_class=PlainTextResponse)
def api_export(conn: Conn, deck_id: int,
               fmt: str = Query("text", pattern="^(text|arena|csv|markdown)$")) -> str:
    _require(conn, deck_id)
    return decks.export(conn, deck_id, fmt)
