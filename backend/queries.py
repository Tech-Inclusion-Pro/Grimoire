"""Read queries over the card and collection tables.

Search is owned-only by default (spec section 11): type-in search that returns
cards you cannot sleeve leads to decks you cannot build.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import query_lang

# Art cards, tokens, and emblems are in the bulk file and are ownable, but they
# are not cards you search for when building a deck -- 3,322 of them, enough to
# bury the real result. "Delver of Secrets" otherwise returns the art card
# first. Tokens stay reachable through card_parts for the packing list.
NON_PLAYABLE_LAYOUTS = ("art_series", "token", "double_faced_token", "emblem")


# FTS5 treats these as syntax. A card name like 'Kongming, "Sleeping Dragon"'
# would otherwise be parsed as a phrase query and error out.
def fts_escape(term: str) -> str:
    cleaned = term.replace('"', '""')
    return f'"{cleaned}"'


# Etched foils carry only `usd_etched`; reading `usd` for them yields NULL and
# silently drops those cards out of the collection total.
VALUE_EXPR = """(CASE copies.finish
                   WHEN 'etched' THEN cards.price_usd_etched
                   WHEN 'foil'   THEN cards.price_usd_foil
                   ELSE cards.price_usd END)"""


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    def one(sql: str) -> Any:
        row = conn.execute(sql).fetchone()
        return row[0] if row else None

    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    return {
        "printings": one("SELECT COUNT(*) FROM cards"),
        "distinct_cards": one("SELECT COUNT(DISTINCT oracle_id) FROM cards"),
        "copies_owned": one("SELECT COUNT(*) FROM copies"),
        "distinct_owned": one(
            "SELECT COUNT(DISTINCT cards.oracle_id) FROM copies "
            "JOIN cards ON cards.id = copies.card_id"),
        "collection_value": one(f"SELECT ROUND(COALESCE(SUM({VALUE_EXPR}), 0), 2) "
                               "FROM copies JOIN cards ON cards.id = copies.card_id"),
        "purchase_total": one(
            "SELECT ROUND(COALESCE(SUM(purchase_price), 0), 2) FROM copies"),
        "lent_out": one("SELECT COUNT(*) FROM copies WHERE lent_to IS NOT NULL"),
        "decks": one("SELECT COUNT(*) FROM decks"),
        "locations": one("SELECT COUNT(*) FROM locations"),
        "bulk_updated_at": meta.get("bulk_updated_at"),
        "bulk_imported_at": meta.get("bulk_imported_at"),
    }


def search_cards(conn: sqlite3.Connection, q: str = "", *, owned_only: bool = True,
                 limit: int = 100, offset: int = 0,
                 include_extras: bool = False) -> dict[str, Any]:
    """Name/type/oracle-text search, grouped to one row per game object.

    Grouped by oracle_id, not by printing: searching for Llanowar Elves should
    return one result you own three copies of, not three results.
    """
    marks = ",".join("?" * len(NON_PLAYABLE_LAYOUTS))
    where: list[str] = ["c.digital = 0"]
    params: list[Any] = []

    if not include_extras:
        where.append(f"c.layout NOT IN ({marks})")
        params.extend(NON_PLAYABLE_LAYOUTS)

    # The query compiles to a set of matching oracle_ids rather than an inline
    # predicate. Inline, a printing-level filter like s:dom would also decide
    # which printings the aggregate counted, so a card owned in four printings
    # would report owning one. See query_lang for the full reasoning.
    compiled = query_lang.compile_query(q, alias="cq")
    if compiled is not None:
        pred, pred_params = compiled
        where.append(
            f"c.oracle_id IN (SELECT cq.oracle_id FROM cards cq WHERE {pred})")
        params.extend(pred_params)

    owned_join = "JOIN" if owned_only else "LEFT JOIN"

    sql = f"""
        SELECT
          c.oracle_id,
          MIN(c.name)                       AS name,
          MIN(c.type_line)                  AS type_line,
          MIN(c.mana_cost)                  AS mana_cost,
          MIN(c.mana_value)                 AS mana_value,
          MIN(c.color_identity)             AS color_identity,
          -- Total printings that EXIST, not the ones joined by this query. With
          -- an inner join to copies, COUNT(DISTINCT c.id) counts only printings
          -- you own, so a card with 62 printings reports 3.
          (SELECT COUNT(*) FROM cards c2
            WHERE c2.oracle_id = c.oracle_id AND c2.digital = 0) AS printings,
          COUNT(cp.id)                      AS owned,
          SUM(CASE WHEN cp.finish IS NOT NULL AND cp.finish != 'normal'
                   THEN 1 ELSE 0 END)         AS foils,
          -- A printing to show art from: one owned if there is one (that is
          -- the copy in the binder), otherwise any printing that has art.
          COALESCE(
            (SELECT c6.id FROM cards c6
               JOIN copies cp6 ON cp6.card_id = c6.id
              WHERE c6.oracle_id = c.oracle_id AND c6.image_normal IS NOT NULL
              ORDER BY c6.released_at DESC LIMIT 1),
            (SELECT c7.id FROM cards c7
              WHERE c7.oracle_id = c.oracle_id AND c7.digital = 0
                AND c7.image_normal IS NOT NULL
              ORDER BY c7.released_at DESC LIMIT 1)
          )                                 AS card_id,
          MIN(c.image_normal)               AS image_normal,
          -- Cheapest printing: what it would cost to acquire.
          MIN(c.price_usd)                  AS price_usd,
          -- What the copies actually held are worth. Showing MIN(price_usd)
          -- alone is misleading: a usd>=40 search matches a card because some
          -- printing is expensive, while the row displayed the cheap printing
          -- the user actually owns.
          ROUND(SUM(CASE cp.finish
                      WHEN 'etched' THEN c.price_usd_etched
                      WHEN 'foil'   THEN c.price_usd_foil
                      ELSE c.price_usd END), 2) AS value,
          GROUP_CONCAT(DISTINCT l.name)     AS locations
        FROM cards c
        {owned_join} copies cp ON cp.card_id = c.id
        LEFT JOIN locations l ON l.id = cp.location_id
        WHERE {' AND '.join(where)}
        GROUP BY c.oracle_id
        ORDER BY name
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(sql, [*params, limit, offset]).fetchall()

    count_sql = f"""
        SELECT COUNT(*) FROM (
          SELECT c.oracle_id FROM cards c
          {owned_join} copies cp ON cp.card_id = c.id
          WHERE {' AND '.join(where)}
          GROUP BY c.oracle_id)
    """
    total = conn.execute(count_sql, params).fetchone()[0]
    return {"total": total, "limit": limit, "offset": offset,
            # How the query was actually read, so a surprising result set is
            # debuggable without guessing at precedence.
            "terms": list(query_lang.describe(q)) if q.strip() else [],
            "results": [dict(r) for r in rows]}


def printings_of(conn: sqlite3.Connection, oracle_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT c.id, c.name, c.set_code, c.set_name, c.collector_num, c.rarity,
               c.price_usd, c.price_usd_foil, c.image_normal, c.released_at,
               COUNT(cp.id) AS owned
        FROM cards c
        LEFT JOIN copies cp ON cp.card_id = c.id
        WHERE c.oracle_id = ?
        GROUP BY c.id
        ORDER BY c.released_at DESC, c.set_code
    """, (oracle_id,)).fetchall()
    return [dict(r) for r in rows]


def card_detail(conn: sqlite3.Connection, oracle_id: str,
                card_id: str | None = None) -> dict[str, Any] | None:
    """Everything needed to read a card properly: the printed text, the
    numbers, what you hold and where it is.

    `card_id` picks a specific printing; without one, a printing you own wins
    (that is the copy in the binder), then the newest paper one."""
    row = conn.execute("""
        SELECT * FROM cards WHERE id = COALESCE(?, (
            SELECT COALESCE(
              (SELECT ca.id FROM cards ca JOIN copies cp ON cp.card_id = ca.id
                WHERE ca.oracle_id = ? ORDER BY ca.released_at DESC LIMIT 1),
              (SELECT cb.id FROM cards cb
                WHERE cb.oracle_id = ? AND cb.digital = 0
                ORDER BY cb.released_at DESC LIMIT 1))))
    """, (card_id, oracle_id, oracle_id)).fetchone()
    if row is None:
        return None

    raw = json.loads(row["raw"])
    faces = []
    for i, face in enumerate(raw.get("card_faces") or []):
        faces.append({
            "face_index": i,
            "name": face.get("name"),
            "mana_cost": face.get("mana_cost"),
            "type_line": face.get("type_line"),
            "oracle_text": face.get("oracle_text"),
            "power": face.get("power"),
            "toughness": face.get("toughness"),
            "loyalty": face.get("loyalty"),
            "flavor_text": face.get("flavor_text"),
        })

    # Grouped, not one row per copy: 41 identical Arcane Signets buried
    # everything else in the panel.
    copies = conn.execute("""
        SELECT COUNT(*) AS quantity,
               cp.finish, cp.condition, cp.language, cp.lent_to,
               COALESCE(l.name, 'Unfiled') AS location,
               c.set_code, c.set_name, c.collector_num
        FROM copies cp
        JOIN cards c ON c.id = cp.card_id
        LEFT JOIN locations l ON l.id = cp.location_id
        WHERE c.oracle_id = ?
        GROUP BY c.id, cp.finish, cp.condition, cp.language, cp.lent_to, l.name
        ORDER BY quantity DESC, c.released_at DESC
    """, (oracle_id,)).fetchall()

    held = conn.execute("""
        SELECT COUNT(*) AS n FROM copies cp JOIN cards c ON c.id = cp.card_id
        WHERE c.oracle_id = ?""", (oracle_id,)).fetchone()["n"]

    decks = conn.execute("""
        SELECT d.id, d.name, dc.quantity, dc.wishlist
        FROM deck_cards dc JOIN decks d ON d.id = dc.deck_id
        WHERE dc.oracle_id = ? ORDER BY d.name""", (oracle_id,)).fetchall()

    tokens = conn.execute("""
        SELECT DISTINCT p.name, p.type_line FROM card_parts p
        WHERE p.card_id = ? AND p.component = 'token' ORDER BY p.name
    """, (row["id"],)).fetchall()

    return {
        "oracle_id": row["oracle_id"],
        "card_id": row["id"],
        "name": row["name"],
        "mana_cost": row["mana_cost"],
        "mana_value": row["mana_value"],
        "type_line": row["type_line"],
        "oracle_text": row["oracle_text"],
        "power": row["power"],
        "toughness": row["toughness"],
        "loyalty": row["loyalty"],
        "color_identity": row["color_identity"],
        "rarity": row["rarity"],
        "set_code": row["set_code"],
        "set_name": row["set_name"],
        "collector_num": row["collector_num"],
        "layout": row["layout"],
        "released_at": row["released_at"],
        "flavor_text": raw.get("flavor_text"),
        "artist": raw.get("artist"),
        "keywords": raw.get("keywords") or [],
        "legalities": raw.get("legalities") or {},
        "price_usd": row["price_usd"],
        "price_usd_foil": row["price_usd_foil"],
        "price_usd_etched": row["price_usd_etched"],
        "faces": faces,
        "copies": [dict(c) for c in copies],
        "owned": held,
        "decks": [dict(d) for d in decks],
        "tokens": [dict(t) for t in tokens],
    }


def faces_of(conn: sqlite3.Connection, card_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT face_index, name, mana_cost, type_line, oracle_text, image_normal "
        "FROM card_faces WHERE card_id = ? ORDER BY face_index", (card_id,)).fetchall()
    return [dict(r) for r in rows]


def tokens_of(conn: sqlite3.Connection, oracle_ids: list[str]) -> list[dict[str, Any]]:
    """The packing list: every token the given cards can create."""
    if not oracle_ids:
        return []
    marks = ",".join("?" * len(oracle_ids))
    rows = conn.execute(f"""
        SELECT DISTINCT p.name, p.type_line
        FROM card_parts p
        JOIN cards c ON c.id = p.card_id
        WHERE p.component = 'token' AND c.oracle_id IN ({marks})
        ORDER BY p.name
    """, oracle_ids).fetchall()
    return [dict(r) for r in rows]


def collection(conn: sqlite3.Connection, limit: int = 200,
               offset: int = 0) -> list[dict[str, Any]]:
    """Every physical copy, with where it is. 'Where is this card?' is the
    question asked most often and the one neither Archidekt nor Scryfall
    answers."""
    rows = conn.execute("""
        SELECT cp.id, cp.finish, cp.condition, cp.language, cp.lent_to,
               cp.acquired_at, cp.notes,
               c.id AS card_id, c.oracle_id, c.name, c.set_code, c.set_name,
               c.collector_num, c.type_line, c.mana_cost,
               COALESCE(l.name, 'Unfiled') AS location,
               (CASE cp.finish WHEN 'etched' THEN c.price_usd_etched
                     WHEN 'foil' THEN c.price_usd_foil
                     ELSE c.price_usd END) AS value
        FROM copies cp
        JOIN cards c ON c.id = cp.card_id
        LEFT JOIN locations l ON l.id = cp.location_id
        ORDER BY c.name, c.set_code
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def locations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT l.id, l.name, COUNT(cp.id) AS copies
        FROM locations l LEFT JOIN copies cp ON cp.location_id = l.id
        GROUP BY l.id ORDER BY l.name
    """).fetchall()
    return [dict(r) for r in rows]
