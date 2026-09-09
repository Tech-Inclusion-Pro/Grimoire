"""Synergy search, the shelf, and packages."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, embeddings, shelf, synergy

router = APIRouter(tags=["synergy"])


def get_db():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_db)]


class SynergyQuery(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    colors: str | None = None
    format: str | None = None
    exclude_deck: int | None = None
    hide_committed: bool = False
    limit: int = Field(30, ge=1, le=100)


class ShelfAdd(BaseModel):
    oracle_id: str
    note: str | None = None


class ShelfNote(BaseModel):
    note: str | None = None


class SendToDeck(BaseModel):
    deck_id: int
    oracle_ids: list[str] | None = None
    keep_on_shelf: bool = False


class NewPackage(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    oracle_ids: list[str] = Field(min_length=1)


@router.get("/embeddings")
def api_embed_status(conn: Conn) -> dict[str, Any]:
    return embeddings.status(conn)


@router.post("/embeddings/build")
def api_embed_build(conn: Conn) -> dict[str, Any]:
    try:
        return embeddings.build(conn)
    except embeddings.EmbeddingError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/synergy")
def api_synergy(conn: Conn, body: SynergyQuery) -> dict[str, Any]:
    try:
        return synergy.search(
            conn, body.goal, colors=body.colors, fmt=body.format,
            exclude_deck=body.exclude_deck, hide_committed=body.hide_committed,
            limit=body.limit)
    except embeddings.EmbeddingError as e:
        raise HTTPException(400, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/shelf")
def api_shelf(conn: Conn) -> dict[str, Any]:
    return {"items": shelf.items(conn), "packages": shelf.packages(conn)}


@router.post("/shelf", status_code=201)
def api_shelf_add(conn: Conn, body: ShelfAdd) -> dict[str, Any]:
    if not conn.execute("SELECT 1 FROM cards WHERE oracle_id=? LIMIT 1",
                        (body.oracle_id,)).fetchone():
        raise HTTPException(404, "no such card")
    added = shelf.add(conn, body.oracle_id, body.note)
    return {"added": added, "already_there": not added}


@router.put("/shelf/{oracle_id}")
def api_shelf_note(conn: Conn, oracle_id: str, body: ShelfNote) -> dict[str, Any]:
    if not shelf.set_note(conn, oracle_id, body.note):
        raise HTTPException(404, "not on the shelf")
    return {"oracle_id": oracle_id, "note": body.note}


@router.delete("/shelf/{oracle_id}", status_code=204)
def api_shelf_remove(conn: Conn, oracle_id: str) -> None:
    if not shelf.remove(conn, oracle_id):
        raise HTTPException(404, "not on the shelf")


@router.post("/shelf/send")
def api_shelf_send(conn: Conn, body: SendToDeck) -> dict[str, Any]:
    if not conn.execute("SELECT 1 FROM decks WHERE id=?", (body.deck_id,)).fetchone():
        raise HTTPException(404, "no such deck")
    return shelf.send_to_deck(conn, body.deck_id, body.oracle_ids,
                              keep_on_shelf=body.keep_on_shelf)


@router.post("/packages", status_code=201)
def api_create_package(conn: Conn, body: NewPackage) -> dict[str, Any]:
    package_id = shelf.create_package(conn, body.name, body.oracle_ids)
    return {"id": package_id, "name": body.name, "cards": len(body.oracle_ids)}


@router.get("/packages/{package_id}")
def api_package(conn: Conn, package_id: int) -> list[dict[str, Any]]:
    return shelf.package_cards(conn, package_id)


@router.post("/packages/{package_id}/to-shelf")
def api_package_to_shelf(conn: Conn, package_id: int) -> dict[str, Any]:
    if not conn.execute("SELECT 1 FROM packages WHERE id=?", (package_id,)).fetchone():
        raise HTTPException(404, "no such package")
    return {"added": shelf.package_to_shelf(conn, package_id)}


@router.delete("/packages/{package_id}", status_code=204)
def api_delete_package(conn: Conn, package_id: int) -> None:
    if not shelf.delete_package(conn, package_id):
        raise HTTPException(404, "no such package")
