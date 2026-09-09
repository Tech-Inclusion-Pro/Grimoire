"""Decks: lists, contents, statistics, and history.

Decks reference `oracle_id` -- the card as a game object. Which printing you
sleeve is a separate, optional choice (`print_pref`), because a deck slot is
satisfied by any printing you own.

Two things the spec is firm about and this module implements literally:

* **Wishlist cards are allowed in a deck and counted separately.** A hard
  owned-only rule makes purchase planning impossible, so the counts stay honest
  instead: `buildable_now` never includes a card you do not hold.
* **Categories are user-defined**, suggested once on import and then never
  touched again.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Iterable

from . import query_lang

# Suggested once, on bulk import, then left alone. Order matters: the first
# rule that matches wins, so 'Land' has to be checked before 'Ramp'.
CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("Land", r"\bLand\b"),
    ("Creature", r"\bCreature\b"),
    ("Planeswalker", r"\bPlaneswalker\b"),
    ("Battle", r"\bBattle\b"),
    ("Artifact", r"\bArtifact\b"),
    ("Enchantment", r"\bEnchantment\b"),
    ("Instant", r"\bInstant\b"),
    ("Sorcery", r"\bSorcery\b"),
)

# "1 Sol Ring", "1x Sol Ring", "Sol Ring", "1 Sol Ring (C21) 263", "SB: 1 Card"
LINE_RE = re.compile(
    r"^\s*(?:(?P<side>SB|MB|CMDR|Commander)\s*[:.]?\s*)?"
    r"(?:(?P<qty>\d+)\s*[xX]?\s+)?"
    r"(?P<name>.+?)"
    r"(?:\s+\((?P<set>[0-9A-Za-z]{3,6})\)(?:\s+(?P<cn>\S+))?)?"
    r"\s*$")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #

def list_decks(conn) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT d.*,
               COALESCE(SUM(dc.quantity), 0) AS cards,
               COALESCE(SUM(CASE WHEN dc.wishlist=1 THEN dc.quantity ELSE 0 END), 0) AS wishlist,
               -- Art for the tile: the commander when there is one, otherwise
               -- the first card listed, which is how every other format is
               -- identified by its front card.
               COALESCE(
                 (SELECT c1.id FROM cards c1
                   WHERE c1.oracle_id = d.commander_oracle_id
                     AND c1.image_art_crop IS NOT NULL AND c1.digital = 0
                   ORDER BY c1.released_at DESC LIMIT 1),
                 (SELECT c2.id FROM deck_cards dk
                    JOIN cards c2 ON c2.oracle_id = dk.oracle_id
                   WHERE dk.deck_id = d.id AND c2.image_art_crop IS NOT NULL
                     AND c2.digital = 0
                   ORDER BY c2.name ASC LIMIT 1)
               ) AS banner_card_id
        FROM decks d LEFT JOIN deck_cards dc ON dc.deck_id = d.id
        GROUP BY d.id ORDER BY d.updated_at DESC, d.name
    """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d["commander_oracle_id"]:
            c = conn.execute("SELECT name FROM cards WHERE oracle_id=? LIMIT 1",
                             (d["commander_oracle_id"],)).fetchone()
            d["commander"] = c["name"] if c else None
        else:
            d["commander"] = None
        d["colors"] = "".join(sorted({
            ch for (ci,) in conn.execute(
                "SELECT DISTINCT c.color_identity FROM deck_cards dk "
                "JOIN cards c ON c.oracle_id = dk.oracle_id WHERE dk.deck_id = ?",
                (d["id"],)) for ch in (ci or "")}))
        out.append(d)
    return out


def create_deck(conn, name: str, fmt: str = "commander",
                commander_oracle_id: str | None = None,
                bracket_claimed: int | None = None) -> int:
    ts = now()
    cur = conn.execute(
        "INSERT INTO decks (name, format, commander_oracle_id, bracket_claimed, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (name, fmt, commander_oracle_id, bracket_claimed, ts, ts))
    conn.commit()
    return cur.lastrowid


def touch(conn, deck_id: int) -> None:
    conn.execute("UPDATE decks SET updated_at=? WHERE id=?", (now(), deck_id))


def delete_deck(conn, deck_id: int) -> bool:
    cur = conn.execute("DELETE FROM decks WHERE id=?", (deck_id,))
    conn.commit()
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Contents
# --------------------------------------------------------------------------- #

CARD_FIELDS = """
    dc.oracle_id, dc.quantity, dc.category, dc.tag, dc.wishlist, dc.print_pref,
    card.id AS card_id, card.image_art_crop,
    card.name, card.mana_cost, card.mana_value, card.type_line, card.oracle_text,
    card.color_identity, card.colors, card.layout, card.image_normal,
    -- Cheapest paper printing, not the representative row's price. The
    -- representative printing can be from a brand-new set Scryfall has not
    -- priced yet, which showed "-" for Sol Ring while 43 were owned.
    (SELECT MIN(c4.price_usd) FROM cards c4
      WHERE c4.oracle_id = dc.oracle_id AND c4.digital = 0
        AND c4.price_usd IS NOT NULL) AS price_usd,
    (SELECT COUNT(*) FROM copies cp JOIN cards c2 ON c2.id = cp.card_id
      WHERE c2.oracle_id = dc.oracle_id) AS owned
"""

# One representative printing per oracle_id: the chosen printing if there is
# one, otherwise a printing actually owned (that is the copy that will be
# sleeved), otherwise the newest paper printing. Without the ownership
# preference a deck row describes a card the user does not hold.
CARD_JOIN = """
    JOIN cards card ON card.id = COALESCE(
        dc.print_pref,
        (SELECT c3.id FROM cards c3
          WHERE c3.oracle_id = dc.oracle_id AND c3.digital = 0
            AND c3.layout NOT IN ('art_series','token','double_faced_token','emblem')
          ORDER BY (SELECT COUNT(*) FROM copies cp3 WHERE cp3.card_id = c3.id) DESC,
                   c3.released_at DESC
          LIMIT 1))
"""


def deck_cards(conn, deck_id: int, q: str = "") -> list[dict[str, Any]]:
    """Cards in a deck, optionally narrowed by the same search syntax used
    everywhere else -- `o:target`, `t:land`, `mv<=2`."""
    where = ["dc.deck_id = ?"]
    params: list[Any] = [deck_id]

    compiled = query_lang.compile_query(q, alias="cq")
    if compiled is not None:
        pred, pred_params = compiled
        where.append(f"dc.oracle_id IN (SELECT cq.oracle_id FROM cards cq WHERE {pred})")
        params.extend(pred_params)

    rows = conn.execute(
        f"SELECT {CARD_FIELDS} FROM deck_cards dc {CARD_JOIN} "
        f"WHERE {' AND '.join(where)} ORDER BY card.name", params).fetchall()
    return [dict(r) for r in rows]


def set_card(conn, deck_id: int, oracle_id: str, quantity: int = 1,
             category: str | None = None, tag: str | None = None,
             wishlist: bool | None = None, print_pref: str | None = None) -> None:
    """Add or update one slot. Quantity 0 removes it."""
    if quantity <= 0:
        conn.execute("DELETE FROM deck_cards WHERE deck_id=? AND oracle_id=?",
                     (deck_id, oracle_id))
        touch(conn, deck_id)
        conn.commit()
        return

    if wishlist is None:
        wishlist = not _owns_any(conn, oracle_id)

    existing = conn.execute(
        "SELECT category FROM deck_cards WHERE deck_id=? AND oracle_id=?",
        (deck_id, oracle_id)).fetchone()
    # Never overwrite a category the user set: suggested once, then untouched.
    if category is None and existing:
        category = existing["category"]

    conn.execute(
        "INSERT INTO deck_cards (deck_id, oracle_id, quantity, category, tag, "
        "wishlist, print_pref) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(deck_id, oracle_id) DO UPDATE SET "
        "quantity=excluded.quantity, category=excluded.category, "
        "tag=excluded.tag, wishlist=excluded.wishlist, print_pref=excluded.print_pref",
        (deck_id, oracle_id, quantity, category, tag, int(bool(wishlist)), print_pref))
    touch(conn, deck_id)
    conn.commit()


def _owns_any(conn, oracle_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM copies cp JOIN cards c ON c.id=cp.card_id "
        "WHERE c.oracle_id=? LIMIT 1", (oracle_id,)).fetchone() is not None


def suggest_category(type_line: str | None) -> str:
    for name, pattern in CATEGORY_RULES:
        if type_line and re.search(pattern, type_line):
            return name
    return "Other"


# --------------------------------------------------------------------------- #
# Bulk paste
# --------------------------------------------------------------------------- #

def parse_decklist(text: str) -> list[dict[str, Any]]:
    """Parse a pasted decklist. Returns one entry per line that looked like a
    card, with the quantity and any set hint."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if re.fullmatch(r"(deck|sideboard|commander|maybeboard)\b.*", line, re.I) and " " not in line.strip():
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        name = (m.group("name") or "").strip()
        if not name:
            continue
        # A trailing "*F*" or similar foil marker is noise for deck purposes.
        name = re.sub(r"\s*\*[A-Z]\*\s*$", "", name).strip()
        out.append({
            "quantity": int(m.group("qty") or 1),
            "name": name,
            "set_code": (m.group("set") or "").lower() or None,
            "section": (m.group("side") or "").lower() or None,
        })
    return out


def resolve_names(conn, entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Turn parsed lines into oracle_ids. Prefers a printing you own, so the
    deck points at the card you can actually sleeve."""
    resolved, unresolved = [], []
    for e in entries:
        name = e["name"]
        row = conn.execute("""
            SELECT c.oracle_id, c.name, c.type_line,
                   (SELECT COUNT(*) FROM copies cp JOIN cards c2 ON c2.id=cp.card_id
                     WHERE c2.oracle_id = c.oracle_id) AS owned
            FROM cards c
            WHERE c.digital = 0
              AND c.layout NOT IN ('art_series','token','double_faced_token','emblem')
              AND (LOWER(c.name) = LOWER(?) OR LOWER(c.name) LIKE LOWER(?))
            ORDER BY owned DESC, (LOWER(c.set_code) = ?) DESC, c.released_at DESC
            LIMIT 1""", (name, f"{name} // %", e.get("set_code") or "")).fetchone()
        if row:
            resolved.append({**e, "oracle_id": row["oracle_id"],
                             "matched_name": row["name"], "owned": row["owned"],
                             "category": suggest_category(row["type_line"])})
        else:
            unresolved.append(e)
    return {"resolved": resolved, "unresolved": unresolved}


def add_many(conn, deck_id: int, resolved: list[dict[str, Any]]) -> int:
    n = 0
    for e in resolved:
        set_card(conn, deck_id, e["oracle_id"], quantity=e["quantity"],
                 category=e.get("category"))
        n += e["quantity"]
    return n


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #

def deck_stats(conn, deck_id: int) -> dict[str, Any]:
    cards = deck_cards(conn, deck_id)
    total = sum(c["quantity"] for c in cards)

    # A slot is buildable when you hold at least as many copies as it needs.
    buildable = sum(c["quantity"] for c in cards if c["owned"] >= c["quantity"])
    wishlist = sum(c["quantity"] for c in cards if c["wishlist"])

    curve: dict[str, int] = {}
    for c in cards:
        if c["type_line"] and "Land" in c["type_line"]:
            continue          # lands are not part of a mana curve
        mv = int(c["mana_value"] or 0)
        key = "7+" if mv >= 7 else str(mv)
        curve[key] = curve.get(key, 0) + c["quantity"]

    colors: dict[str, int] = {}
    for c in cards:
        for ch in (c["color_identity"] or ""):
            colors[ch] = colors.get(ch, 0) + c["quantity"]

    categories: dict[str, int] = {}
    for c in cards:
        cat = c["category"] or suggest_category(c["type_line"])
        categories[cat] = categories.get(cat, 0) + c["quantity"]

    value = sum((c["price_usd"] or 0) * c["quantity"] for c in cards)

    return {
        "cards": total,
        "distinct": len(cards),
        "buildable_now": buildable,
        "buildable_pct": round(buildable / total * 100, 1) if total else 0.0,
        "wishlist": wishlist,
        "missing": total - buildable,
        "curve": dict(sorted(curve.items())),
        "colors": colors,
        "categories": dict(sorted(categories.items())),
        "value": round(value, 2),
        "conflicts": conflicts(conn, deck_id),
    }


def conflicts(conn, deck_id: int) -> list[dict[str, Any]]:
    """Cards this deck needs that another deck also uses, beyond what you own.

    Without this, two decks can each report 100% buildable while sharing a
    single physical copy."""
    rows = conn.execute("""
        SELECT dc.oracle_id,
               (SELECT name FROM cards WHERE oracle_id = dc.oracle_id
                 AND digital = 0 LIMIT 1) AS name,
               dc.quantity AS needed,
               (SELECT COUNT(*) FROM copies cp JOIN cards c2 ON c2.id = cp.card_id
                 WHERE c2.oracle_id = dc.oracle_id) AS owned,
               (SELECT COALESCE(SUM(d2.quantity), 0) FROM deck_cards d2
                 WHERE d2.oracle_id = dc.oracle_id AND d2.deck_id != dc.deck_id
                   AND d2.wishlist = 0) AS used_elsewhere
        FROM deck_cards dc
        WHERE dc.deck_id = ? AND dc.wishlist = 0
    """, (deck_id,)).fetchall()
    out = []
    for r in rows:
        if r["needed"] + r["used_elsewhere"] > r["owned"]:
            out.append({"oracle_id": r["oracle_id"], "name": r["name"],
                        "needed": r["needed"], "owned": r["owned"],
                        "used_elsewhere": r["used_elsewhere"]})
    return sorted(out, key=lambda x: x["name"] or "")


def tokens_needed(conn, deck_id: int) -> list[dict[str, Any]]:
    """The packing list: every token this deck can make."""
    rows = conn.execute("""
        SELECT DISTINCT p.name, p.type_line
        FROM deck_cards dc
        JOIN cards c ON c.oracle_id = dc.oracle_id
        JOIN card_parts p ON p.card_id = c.id AND p.component = 'token'
        WHERE dc.deck_id = ?
        ORDER BY p.name""", (deck_id,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Playtesting
# --------------------------------------------------------------------------- #

def playtest_deck(conn, deck_id: int) -> dict[str, Any]:
    """The deck expanded to one entry per physical card, ready to shuffle.

    Goldfishing only — no rules are enforced anywhere, so this returns cards
    and zones and nothing else. The commander starts in the command zone; every
    other card starts in the library."""
    deck = conn.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
    if deck is None:
        raise LookupError("no such deck")

    commander_oracle = deck["commander_oracle_id"]
    cards: list[dict[str, Any]] = []
    commanders: list[dict[str, Any]] = []

    for row in deck_cards(conn, deck_id):
        for copy_index in range(row["quantity"]):
            entry = {
                # Stable per copy, so React keys and zone moves do not confuse
                # two copies of the same card.
                "uid": f"{row['oracle_id']}:{copy_index}",
                "oracle_id": row["oracle_id"],
                "card_id": row["card_id"],
                "name": row["name"],
                "mana_cost": row["mana_cost"],
                "mana_value": row["mana_value"],
                "type_line": row["type_line"],
                "oracle_text": row["oracle_text"],
                "color_identity": row["color_identity"],
            }
            if commander_oracle and row["oracle_id"] == commander_oracle:
                commanders.append(entry)
            else:
                cards.append(entry)

    return {
        "deck": {"id": deck["id"], "name": deck["name"], "format": deck["format"]},
        "library": cards,
        "command_zone": commanders,
        "starting_life": 40 if (deck["format"] or "") == "commander" else 20,
    }


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #

def snapshot(conn, deck_id: int, summary: str) -> int:
    cards = conn.execute(
        "SELECT oracle_id, quantity, category, tag, wishlist, print_pref "
        "FROM deck_cards WHERE deck_id=?", (deck_id,)).fetchall()
    cur = conn.execute(
        "INSERT INTO deck_history (deck_id, at, summary, snapshot) VALUES (?,?,?,?)",
        (deck_id, now(), summary,
         json.dumps([dict(r) for r in cards], separators=(",", ":"))))
    conn.commit()
    return cur.lastrowid


def history(conn, deck_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, at, summary FROM deck_history WHERE deck_id=? ORDER BY at DESC, id DESC",
        (deck_id,)).fetchall()
    return [dict(r) for r in rows]


def restore(conn, deck_id: int, history_id: int) -> int:
    row = conn.execute(
        "SELECT snapshot FROM deck_history WHERE id=? AND deck_id=?",
        (history_id, deck_id)).fetchone()
    if row is None:
        raise LookupError("no such history entry for this deck")
    # Snapshot the current state first, so restoring is itself undoable.
    snapshot(conn, deck_id, "before restore")
    cards = json.loads(row["snapshot"])
    conn.execute("DELETE FROM deck_cards WHERE deck_id=?", (deck_id,))
    conn.executemany(
        "INSERT INTO deck_cards (deck_id, oracle_id, quantity, category, tag, "
        "wishlist, print_pref) VALUES (?,?,?,?,?,?,?)",
        [(deck_id, c["oracle_id"], c["quantity"], c["category"], c["tag"],
          c["wishlist"], c["print_pref"]) for c in cards])
    touch(conn, deck_id)
    conn.commit()
    return len(cards)


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def export(conn, deck_id: int, fmt: str = "text") -> str:
    deck = conn.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
    if deck is None:
        raise LookupError("no such deck")
    cards = deck_cards(conn, deck_id)

    if fmt == "text":
        return "\n".join(f"{c['quantity']} {c['name']}" for c in cards) + "\n"

    if fmt == "arena":
        lines = []
        for c in cards:
            row = conn.execute(
                "SELECT set_code, collector_num FROM cards WHERE oracle_id=? "
                "AND digital=0 ORDER BY released_at DESC LIMIT 1",
                (c["oracle_id"],)).fetchone()
            name = c["name"].split(" // ")[0]
            if row:
                lines.append(f"{c['quantity']} {name} ({row['set_code'].upper()}) "
                             f"{row['collector_num']}")
            else:
                lines.append(f"{c['quantity']} {name}")
        return "\n".join(lines) + "\n"

    if fmt == "csv":
        out = ["quantity,name,category,tag,wishlist,owned,mana_value,type_line"]
        for c in cards:
            name = c["name"].replace('"', '""')
            type_line = (c["type_line"] or "").replace('"', '""')
            out.append(f'{c["quantity"]},"{name}",{c["category"] or ""},'
                       f'{c["tag"] or ""},{c["wishlist"]},{c["owned"]},'
                       f'{c["mana_value"] or 0},"{type_line}"')
        return "\n".join(out) + "\n"

    if fmt == "markdown":
        stats = deck_stats(conn, deck_id)
        lines = [f"# {deck['name']}", ""]
        if deck["format"]:
            lines.append(f"Format: {deck['format']}")
        lines.append(f"{stats['cards']} cards, {stats['buildable_pct']}% buildable now")
        lines.append("")
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for c in cards:
            by_cat.setdefault(c["category"] or suggest_category(c["type_line"]), []).append(c)
        for cat in sorted(by_cat):
            lines.append(f"## {cat}")
            for c in sorted(by_cat[cat], key=lambda x: x["name"]):
                mark = "" if c["owned"] >= c["quantity"] else "  *(need to buy)*"
                lines.append(f"- {c['quantity']} {c['name']}{mark}")
            lines.append("")
        return "\n".join(lines)

    raise ValueError(f"unknown export format {fmt!r}")
