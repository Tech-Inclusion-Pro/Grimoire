"""The shelf: a staging area between thinking and building.

**Synergy results land here, never straight into a deck** (spec section 6.3). A
search result should not quietly modify a list you have already built, so the
cost is one extra click per accepted suggestion and the benefit is that no
search can ever damage a deck.

Packages live here too: reusable groups of cards — a green ramp suite, a
mono-green landbase — droppable into any new deck.
"""
from __future__ import annotations

import sqlite3
from typing import Any

CARD_FIELDS = """
    s.oracle_id, s.added_at, s.note,
    MIN(c.name)           AS name,
    MIN(c.mana_cost)      AS mana_cost,
    MIN(c.mana_value)     AS mana_value,
    MIN(c.type_line)      AS type_line,
    MIN(c.color_identity) AS color_identity,
    MIN(c.price_usd)      AS price_usd,
    (SELECT c9.id FROM cards c9 WHERE c9.oracle_id = s.oracle_id
       AND c9.digital = 0 AND c9.image_normal IS NOT NULL
     ORDER BY c9.released_at DESC LIMIT 1) AS card_id,
    (SELECT COUNT(*) FROM copies cp JOIN cards c2 ON c2.id = cp.card_id
      WHERE c2.oracle_id = s.oracle_id) AS owned
"""


def items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(f"""
        SELECT {CARD_FIELDS}
        FROM shelf s JOIN cards c ON c.oracle_id = s.oracle_id
        WHERE c.digital = 0
        GROUP BY s.oracle_id
        ORDER BY s.added_at DESC, name
    """).fetchall()
    return [dict(r) for r in rows]


def add(conn: sqlite3.Connection, oracle_id: str, note: str | None = None) -> bool:
    """Park a card. Returns False if it was already there — re-adding must not
    wipe a note written earlier."""
    existing = conn.execute("SELECT note FROM shelf WHERE oracle_id=?",
                            (oracle_id,)).fetchone()
    if existing is not None:
        if note:
            conn.execute("UPDATE shelf SET note=? WHERE oracle_id=?", (note, oracle_id))
            conn.commit()
        return False
    conn.execute(
        "INSERT INTO shelf (oracle_id, added_at, note) VALUES (?, datetime('now'), ?)",
        (oracle_id, note))
    conn.commit()
    return True


def set_note(conn: sqlite3.Connection, oracle_id: str, note: str | None) -> bool:
    cur = conn.execute("UPDATE shelf SET note=? WHERE oracle_id=?", (note, oracle_id))
    conn.commit()
    return cur.rowcount > 0


def remove(conn: sqlite3.Connection, oracle_id: str) -> bool:
    cur = conn.execute("DELETE FROM shelf WHERE oracle_id=?", (oracle_id,))
    conn.commit()
    return cur.rowcount > 0


def clear(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM shelf")
    conn.commit()
    return cur.rowcount


def send_to_deck(conn: sqlite3.Connection, deck_id: int,
                 oracle_ids: list[str] | None = None,
                 *, keep_on_shelf: bool = False) -> dict[str, Any]:
    """Move shelved cards into a deck as one batch.

    A snapshot is taken first, so a batch that turns out wrong is one Restore
    away rather than a manual undo of twenty slots."""
    from . import decks

    on_shelf = [r["oracle_id"] for r in conn.execute("SELECT oracle_id FROM shelf")]
    chosen = [o for o in (oracle_ids or on_shelf) if o in set(on_shelf)]
    if not chosen:
        return {"added": 0, "moved": []}

    plural = "" if len(chosen) == 1 else "s"
    decks.snapshot(conn, deck_id,
                   f"before adding {len(chosen)} card{plural} from the shelf")

    added = 0
    for oracle_id in chosen:
        row = conn.execute(
            "SELECT type_line FROM cards WHERE oracle_id=? AND digital=0 LIMIT 1",
            (oracle_id,)).fetchone()
        decks.set_card(conn, deck_id, oracle_id, quantity=1,
                       category=decks.suggest_category(row["type_line"] if row else None))
        added += 1

    if not keep_on_shelf:
        conn.executemany("DELETE FROM shelf WHERE oracle_id=?", [(o,) for o in chosen])
        conn.commit()
    return {"added": added, "moved": chosen}


# --------------------------------------------------------------------------- #
# Packages
# --------------------------------------------------------------------------- #

def packages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT p.id, p.name, COUNT(pc.oracle_id) AS cards
        FROM packages p LEFT JOIN package_cards pc ON pc.package_id = p.id
        GROUP BY p.id ORDER BY p.name""").fetchall()
    return [dict(r) for r in rows]


def package_cards(conn: sqlite3.Connection, package_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT pc.oracle_id,
               MIN(c.name) AS name, MIN(c.mana_cost) AS mana_cost,
               MIN(c.type_line) AS type_line,
               (SELECT COUNT(*) FROM copies cp JOIN cards c2 ON c2.id=cp.card_id
                 WHERE c2.oracle_id = pc.oracle_id) AS owned
        FROM package_cards pc JOIN cards c ON c.oracle_id = pc.oracle_id
        WHERE pc.package_id = ? AND c.digital = 0
        GROUP BY pc.oracle_id ORDER BY name""", (package_id,)).fetchall()
    return [dict(r) for r in rows]


def create_package(conn: sqlite3.Connection, name: str,
                   oracle_ids: list[str]) -> int:
    cur = conn.execute("INSERT INTO packages (name) VALUES (?)", (name,))
    package_id = cur.lastrowid
    conn.executemany(
        "INSERT OR IGNORE INTO package_cards (package_id, oracle_id) VALUES (?,?)",
        [(package_id, o) for o in oracle_ids])
    conn.commit()
    return package_id


def delete_package(conn: sqlite3.Connection, package_id: int) -> bool:
    cur = conn.execute("DELETE FROM packages WHERE id=?", (package_id,))
    conn.commit()
    return cur.rowcount > 0


def package_to_shelf(conn: sqlite3.Connection, package_id: int) -> int:
    """Load a package onto the shelf, which is the only route into a deck."""
    cards = conn.execute(
        "SELECT oracle_id FROM package_cards WHERE package_id=?", (package_id,)).fetchall()
    n = 0
    for row in cards:
        if add(conn, row["oracle_id"], note=None):
            n += 1
    return n
