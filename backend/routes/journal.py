"""Journal and vault endpoints."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, journal, vault

router = APIRouter(tags=["journal"])


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


class NewEntry(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    result: Literal["win", "loss", ""] | None = None


class Generate(BaseModel):
    model: str = Field(min_length=1)


@router.get("/decks/{deck_id}/journal")
def api_entries(conn: Conn, deck_id: int) -> dict[str, Any]:
    _require(conn, deck_id)
    return {
        "entries": journal.entries(conn, deck_id),
        "record": journal.record(conn, deck_id),
        "min_entries": journal.MIN_ENTRIES,
    }


@router.post("/decks/{deck_id}/journal", status_code=201)
def api_add_entry(conn: Conn, deck_id: int, body: NewEntry) -> dict[str, Any]:
    _require(conn, deck_id)
    entry_id = journal.add_entry(conn, deck_id, body.body, body.result or None)
    # Written to the vault on submit, not on keystroke: rapid small writes to a
    # synced folder produce conflict copies.
    written = _sync(conn, deck_id)
    return {"id": entry_id, "record": journal.record(conn, deck_id), **written}


@router.delete("/journal/{entry_id}", status_code=200)
def api_delete_entry(conn: Conn, entry_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT deck_id FROM journal WHERE id=?", (entry_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such entry")
    deck_id = row["deck_id"]
    journal.delete_entry(conn, entry_id)
    return {"deleted": entry_id, **_sync(conn, deck_id)}


@router.get("/journal/models")
def api_models() -> dict[str, Any]:
    """Offered at the moment of generation, never buried in settings: the
    journal is the most personal data in the app, so the data-flow consequence
    is shown next to the button."""
    return {"models": journal.available_models()}


@router.post("/decks/{deck_id}/journal/overview")
def api_generate(conn: Conn, deck_id: int, body: Generate) -> dict[str, Any]:
    _require(conn, deck_id)
    try:
        overview = journal.generate_overview(conn, deck_id, body.model)
    except journal.GenerationError as e:
        raise HTTPException(400, str(e)) from e
    result = _sync(conn, deck_id, overview=overview)
    return {"overview": overview, **result}


@router.post("/decks/{deck_id}/vault-sync")
def api_sync(conn: Conn, deck_id: int) -> dict[str, Any]:
    _require(conn, deck_id)
    return _sync(conn, deck_id)


def _sync(conn, deck_id: int, overview: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return {"vault": journal.sync_deck(conn, deck_id, overview=overview)}
    except vault.VaultError as e:
        # Never fail the write that the user actually asked for just because the
        # vault is unavailable; the entry is already safe in SQLite.
        return {"vault_error": str(e)}
    except OSError as e:
        return {"vault_error": f"Could not write to the vault: {e}"}
