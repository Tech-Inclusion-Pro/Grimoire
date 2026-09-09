"""SQLite connection handling.

The database lives on local disk and never inside the vault or any other
synced folder -- sync engines copy files mid-write and the result is a corrupt
database (spec section 3).
"""
from __future__ import annotations

import os
import pathlib
import sqlite3

SCHEMA = pathlib.Path(__file__).with_name("schema.sql")

# Overridable so tests can point at a tmpdir and the launchd job can point at
# a data directory outside the repo.
DEFAULT_DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "grimoire.db"


def db_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("GRIMOIRE_DB", DEFAULT_DB))


def connect(path: pathlib.Path | str | None = None) -> sqlite3.Connection:
    p = pathlib.Path(path) if path else db_path()
    if p != pathlib.Path(":memory:"):
        p.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False because FastAPI runs a sync dependency and the
    # sync path operation it feeds on *different* threadpool workers. With the
    # default, a request whose two halves land on different threads raises
    # ProgrammingError -- intermittently, which is worse than always.
    #
    # Safe here only because connect() hands out a fresh connection per request
    # and nothing shares one across concurrent requests. Do not turn this into
    # a module-level singleton.
    conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` does not
# touch an existing table, so a database created before these existed would
# still be missing them and every insert would fail. Keep this list append-only.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("cards", "power", "TEXT"),
    ("cards", "toughness", "TEXT"),
    ("cards", "loyalty", "TEXT"),
    ("saved_searches", "created_at", "TEXT"),
    ("cards", "price_usd_etched", "REAL"),
    ("copies", "finish", "TEXT NOT NULL DEFAULT 'normal'"),
    ("copies", "purchase_price", "REAL"),
    ("cards", "image_small", "TEXT"),
    ("cards", "image_art_crop", "TEXT"),
)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any columns missing from an older database. Returns what it did."""
    applied: list[str] = []
    for table, column, decl in _ADDED_COLUMNS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue  # table does not exist yet; schema.sql will create it
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            applied.append(f"{table}.{column}")
    if applied:
        conn.commit()
    return applied


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    migrate(conn)


def open_db(path: pathlib.Path | str | None = None) -> sqlite3.Connection:
    conn = connect(path)
    init(conn)
    return conn
