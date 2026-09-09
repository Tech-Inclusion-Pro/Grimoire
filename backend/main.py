"""Grimoire API.

Build order step 1: the importer and data model, with nothing but a table view
on top. Everything here is read-only over cards plus the minimum writes needed
to record that you own something.
"""
from __future__ import annotations

import datetime
import pathlib
import sqlite3
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, images, queries, query_lang
from .routes import auth as auth_route
from .routes import decks as decks_route
from .routes import journal as journal_route
from .routes import synergy as synergy_route

STATIC = pathlib.Path(__file__).with_name("static")

app = FastAPI(
    title="Grimoire",
    description="A personal Magic: The Gathering library, deck builder, and play journal.",
    version="0.1.0",
)


app.include_router(auth_route.router, prefix="/api")
app.include_router(decks_route.router, prefix="/api")
app.include_router(journal_route.router, prefix="/api")
app.include_router(synergy_route.router, prefix="/api")

# Reachable without a login: the login flow itself, and the static shell plus
# its CSS/JS so the login form can actually paint. Everything else under /api
# is gated. The page gates its own content client-side via /api/auth/status.
_PUBLIC_API_PREFIXES = ("/api/auth/", "/api/docs", "/api/openapi.json")


def _is_public_path(path: str) -> bool:
    if path.startswith(_PUBLIC_API_PREFIXES):
        return True
    # /healthz stays open so the install script and launchd can probe it.
    if path == "/healthz":
        return True
    return not path.startswith("/api/")


@app.middleware("http")
async def require_auth(request: Request, call_next):
    cfg, enabled, err = auth.load_auth_config()
    if err is not None:
        # Fail closed. A misconfigured auth file must not silently become
        # "no auth" on a tailnet shared with other people.
        return JSONResponse({"detail": f"auth misconfigured: {err}"}, status_code=503)
    if not enabled or _is_public_path(request.url.path):
        return await call_next(request)
    if auth.current_user(request.cookies.get(auth.COOKIE_NAME)) is None:
        return JSONResponse({"detail": "not authenticated"}, status_code=401)
    return await call_next(request)


def get_db():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_db)]


@app.get("/api/stats")
def api_stats(conn: Conn) -> dict[str, Any]:
    return queries.stats(conn)


@app.get("/api/cards")
def api_cards(
    conn: Conn,
    q: str = "",
    owned: bool = Query(True, description="Owned-only unless explicitly opted out"),
    extras: bool = Query(False, description="Include art cards, tokens, and emblems"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    try:
        return queries.search_cards(conn, q, owned_only=owned, limit=limit,
                                    offset=offset, include_extras=extras)
    except query_lang.QueryError as e:
        # The message is written to be shown to the user verbatim.
        raise HTTPException(400, str(e)) from e
    except sqlite3.OperationalError as e:
        raise HTTPException(400, f"bad search: {e}") from e


@app.get("/api/cards/{oracle_id}/detail")
def api_card_detail(conn: Conn, oracle_id: str,
                    card_id: str | None = None) -> dict[str, Any]:
    detail = queries.card_detail(conn, oracle_id, card_id)
    if detail is None:
        raise HTTPException(404, "no such card")
    return detail


@app.get("/api/cards/{oracle_id}/printings")
def api_printings(conn: Conn, oracle_id: str) -> list[dict[str, Any]]:
    rows = queries.printings_of(conn, oracle_id)
    if not rows:
        raise HTTPException(404, "no such card")
    return rows


@app.get("/api/cards/{oracle_id}/faces")
def api_faces(conn: Conn, oracle_id: str) -> list[dict[str, Any]]:
    row = conn.execute("SELECT id FROM cards WHERE oracle_id=? LIMIT 1",
                       (oracle_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such card")
    return queries.faces_of(conn, row["id"])


@app.get("/api/collection")
def api_collection(conn: Conn, limit: int = Query(200, ge=1, le=1000),
                   offset: int = Query(0, ge=0)) -> list[dict[str, Any]]:
    return queries.collection(conn, limit=limit, offset=offset)


@app.get("/api/locations")
def api_locations(conn: Conn) -> list[dict[str, Any]]:
    return queries.locations(conn)


class NewLocation(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@app.post("/api/locations", status_code=201)
def api_new_location(conn: Conn, body: NewLocation) -> dict[str, Any]:
    try:
        cur = conn.execute("INSERT INTO locations (name) VALUES (?)", (body.name,))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "a location with that name already exists") from None
    return {"id": cur.lastrowid, "name": body.name}


class NewCopy(BaseModel):
    card_id: str                       # a printing id, never an oracle_id
    quantity: int = Field(1, ge=1, le=99)
    # 'normal' | 'foil' | 'etched'. Etched is a real third finish with its own
    # price, so a boolean would lose money.
    finish: Literal["normal", "foil", "etched"] = "normal"
    condition: str | None = None
    language: str = "en"
    location_id: int | None = None
    notes: str | None = None


@app.post("/api/copies", status_code=201)
def api_add_copies(conn: Conn, body: NewCopy) -> dict[str, Any]:
    """Add physical copies. Quantity is tracked per printing, not per card --
    foils, sets, and conditions differ."""
    if not conn.execute("SELECT 1 FROM cards WHERE id=?", (body.card_id,)).fetchone():
        raise HTTPException(404, "no such printing")
    # `foil` is written alongside `finish` only to keep the legacy column
    # consistent. Every read derives from `finish`.
    rows = [(body.card_id, body.finish, 0 if body.finish == "normal" else 1,
             body.condition, body.language, body.location_id, body.notes)
            for _ in range(body.quantity)]
    conn.executemany(
        "INSERT INTO copies (card_id, finish, foil, condition, language, "
        "location_id, notes) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return {"added": body.quantity, "card_id": body.card_id}


@app.delete("/api/copies/{copy_id}", status_code=204)
def api_delete_copy(conn: Conn, copy_id: int) -> None:
    cur = conn.execute("DELETE FROM copies WHERE id=?", (copy_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "no such copy")


class SavedSearch(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=500)


@app.get("/api/searches")
def api_searches(conn: Conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, query, created_at FROM saved_searches ORDER BY name")]


@app.post("/api/searches", status_code=201)
def api_save_search(conn: Conn, body: SavedSearch) -> dict[str, Any]:
    # Validate before storing. A saved search that cannot run is worse than no
    # saved search, because it fails later when you have forgotten the syntax.
    try:
        query_lang.compile_query(body.query)
    except query_lang.QueryError as e:
        raise HTTPException(400, str(e)) from e
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT OR REPLACE INTO saved_searches (name, query, created_at) VALUES (?,?,?)",
        (body.name, body.query, now))
    conn.commit()
    return {"id": cur.lastrowid, "name": body.name, "query": body.query, "created_at": now}


@app.delete("/api/searches/{search_id}", status_code=204)
def api_delete_search(conn: Conn, search_id: int) -> None:
    cur = conn.execute("DELETE FROM saved_searches WHERE id=?", (search_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "no such saved search")


@app.get("/api/search-help")
def api_search_help() -> dict[str, Any]:
    """The syntax reference the UI renders, generated from the parser itself so
    it cannot drift out of date."""
    return {
        "fields": sorted(set(query_lang.FIELD_ALIASES.values()) - {"not"}),
        "aliases": query_lang.FIELD_ALIASES,
        "is_values": list(query_lang.IS_VALUES),
        "rarities": query_lang.RARITY_ORDER,
        "colors": sorted(query_lang.COLOR_NAMES),
        "operators": [":", "=", "!=", "<", "<=", ">", ">="],
    }


@app.get("/api/images/{card_id}/{variant}")
def api_image(conn: Conn, card_id: str, variant: str) -> FileResponse:
    """Cached card art. Fetched from Scryfall once, then served locally."""
    if variant not in images.VARIANTS:
        raise HTTPException(404, f"unknown variant; try {', '.join(images.VARIANTS)}")
    try:
        path = images.fetch(conn, card_id, variant)
    except images.ImageError as e:
        raise HTTPException(404, str(e)) from e
    # Card art for a given printing never changes, so let the browser keep it.
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/images")
def api_image_stats() -> dict[str, Any]:
    return images.stats()


@app.get("/healthz")
def healthz(conn: Conn) -> dict[str, Any]:
    n = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    return {"ok": True, "cards": n}


# The Vite build lands in backend/static. Serve it as a single-page app: real
# files when they exist, index.html otherwise, so a deep link still boots the
# app rather than 404ing.
_STATIC_ROOT = STATIC.resolve()

if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        # A *defined* /api route is matched above and never reaches here, but an
        # undefined one does. Without this it would come back as index.html with
        # a 200, and the frontend would try to parse HTML as JSON.
        if full_path.startswith("api/"):
            raise HTTPException(404, "no such endpoint")
        if full_path:
            try:
                candidate = (_STATIC_ROOT / full_path).resolve()
                candidate.relative_to(_STATIC_ROOT)   # refuse path traversal
            except (ValueError, OSError):
                candidate = None
            if candidate is not None and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(_STATIC_ROOT / "index.html")

else:
    @app.get("/", include_in_schema=False)
    def dev_root() -> dict[str, str]:
        return {
            "message": "Frontend not built. Run `cd frontend && npm run build`, "
                       "or use the Vite dev server on :5174 during development.",
            "api_docs": "/docs",
        }
