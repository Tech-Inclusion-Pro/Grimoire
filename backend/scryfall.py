"""Reading Scryfall data into Grimoire's shape.

Everything here exists because Scryfall's card object is wrong in plausible
looking ways if you read it naively:

*   ``oracle_id`` is the card as a game object; ``id`` is one printing. Decks
    reference the former, the collection tracks the latter.
*   When ``layout`` is transform/modal_dfc/flip/meld, the root object has **no**
    ``mana_cost``, ``oracle_text``, ``colors`` or ``image_uris`` at all -- they
    live per entry in ``card_faces``. Reading ``oracle_text`` off the root gives
    an empty string, and those cards then vanish from search.
*   ``split`` and ``adventure`` are the awkward middle: root keeps ``mana_cost``
    and ``image_uris``, but still has no ``oracle_text``.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

# Deliberately NOT a list of multi-face layouts.
#
# The obvious implementation is `if layout in {"transform", "modal_dfc",
# "split", ...}`. Checked against the full 108k-printing bulk file, the layouts
# that actually carry `card_faces` are: art_series, transform, adventure,
# split, modal_dfc, double_faced_token, reversible_card, flip -- and `prepare`,
# which appears in no such list anywhere and would have silently lost 96 cards.
# Scryfall adds layouts whenever a set does something new, so branch on whether
# `card_faces` is present, never on the layout name.

FACE_JOIN = "\n//\n"


def _face_image(face: dict[str, Any], variant: str = "normal") -> str | None:
    uris = face.get("image_uris") or {}
    if variant == "normal":
        return uris.get("normal") or uris.get("large") or uris.get("small")
    return uris.get(variant)


def image_of(card: dict[str, Any], variant: str) -> str | None:
    """Front-face art. transform and modal_dfc carry no image_uris at the root
    at all, so the first face is the only place to look."""
    direct = _face_image(card, variant)
    if direct:
        return direct
    for face in card.get("card_faces") or []:
        found = _face_image(face, variant)
        if found:
            return found
    return None


def faces_of(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalised faces. A single-faced card reports one synthetic face, so
    callers never branch on layout."""
    raw_faces = card.get("card_faces") or []
    if not raw_faces:
        return [{
            "name": card.get("name", ""),
            "mana_cost": card.get("mana_cost"),
            "type_line": card.get("type_line"),
            "oracle_text": card.get("oracle_text"),
            "image_normal": _face_image(card),
        }]
    out = []
    for f in raw_faces:
        out.append({
            "name": f.get("name", ""),
            "mana_cost": f.get("mana_cost"),
            "type_line": f.get("type_line"),
            "oracle_text": f.get("oracle_text"),
            # split/adventure keep art at the root; transform/mdfc keep it per face
            "image_normal": _face_image(f) or (_face_image(card) if len(raw_faces) else None),
        })
    return out


def searchable_text(card: dict[str, Any]) -> str:
    """Oracle text for FTS. Faces joined, so a double-faced card is findable by
    text printed on either side."""
    root = card.get("oracle_text")
    if root:
        return root
    parts = [f.get("oracle_text") or "" for f in card.get("card_faces") or []]
    return FACE_JOIN.join(p for p in parts if p)


def mana_cost_of(card: dict[str, Any]) -> str | None:
    """Root cost when present (split cards have one), otherwise faces joined."""
    root = card.get("mana_cost")
    if root:
        return root
    costs = [f.get("mana_cost") or "" for f in card.get("card_faces") or []]
    costs = [c for c in costs if c]
    return " // ".join(costs) if costs else None


def extract(card: dict[str, Any]) -> dict[str, Any]:
    """One Scryfall card object -> one row for the ``cards`` table."""
    faces = faces_of(card)
    prices = card.get("prices") or {}

    def price(key: str) -> float | None:
        v = prices.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "id": card["id"],
        "oracle_id": card.get("oracle_id") or _oracle_id_fallback(card),
        "name": card.get("name", ""),
        "set_code": card.get("set"),
        "set_name": card.get("set_name"),
        "collector_num": card.get("collector_number"),
        "layout": card.get("layout"),
        "mana_cost": mana_cost_of(card),
        "mana_value": card.get("cmc"),
        "type_line": card.get("type_line"),
        "oracle_text": searchable_text(card),
        # Kept as text on purpose: power can be '*', '1+*', '½' or '∞'.
        # Numeric comparison casts at query time and lets the odd ones fall out.
        "power": _first(card, "power"),
        "toughness": _first(card, "toughness"),
        "loyalty": _first(card, "loyalty"),
        # Stored as a sorted string so 'WU' and 'UW' are never two things.
        "color_identity": "".join(sorted(card.get("color_identity") or [])),
        "colors": "".join(sorted(_colors_of(card))),
        "rarity": card.get("rarity"),
        "price_usd": price("usd"),
        "price_usd_foil": price("usd_foil"),
        "price_usd_etched": price("usd_etched"),
        "edhrec_rank": card.get("edhrec_rank"),
        "image_status": card.get("image_status"),
        "image_normal": faces[0]["image_normal"],
        "image_small": image_of(card, "small"),
        "image_art_crop": image_of(card, "art_crop"),
        "released_at": card.get("released_at"),
        "digital": 1 if card.get("digital") else 0,
        "raw": json.dumps(card, ensure_ascii=False, separators=(",", ":")),
    }


def _first(card: dict[str, Any], key: str) -> Any:
    """Root value, else the first face that has one. A transform creature keeps
    power and toughness per face, not at the root."""
    if card.get(key) is not None:
        return card[key]
    for f in card.get("card_faces") or []:
        if f.get(key) is not None:
            return f[key]
    return None


def _colors_of(card: dict[str, Any]) -> list[str]:
    if card.get("colors") is not None:
        return card["colors"]
    seen: set[str] = set()
    for f in card.get("card_faces") or []:
        seen.update(f.get("colors") or [])
    return sorted(seen)


def _oracle_id_fallback(card: dict[str, Any]) -> str:
    """reversible_card printings put oracle_id on the faces, not the root."""
    for f in card.get("card_faces") or []:
        if f.get("oracle_id"):
            return f["oracle_id"]
    raise KeyError(f"no oracle_id for card {card.get('id')} ({card.get('name')})")


def parts_of(card: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Related cards -- tokens a card creates, meld halves, combo pieces.

    Scryfall lists the card itself as a combo_piece of itself; skip that."""
    for p in card.get("all_parts") or []:
        if p.get("id") == card.get("id"):
            continue
        yield {
            "component": p.get("component", ""),
            "part_id": p.get("id", ""),
            "name": p.get("name", ""),
            "type_line": p.get("type_line"),
        }


CARD_COLUMNS = (
    "id", "oracle_id", "name", "set_code", "set_name", "collector_num", "layout",
    "mana_cost", "mana_value", "type_line", "oracle_text",
    "power", "toughness", "loyalty", "color_identity",
    "colors", "rarity", "price_usd", "price_usd_foil", "edhrec_rank",
    "image_status", "image_normal", "released_at", "digital", "raw",
    "price_usd_etched", "image_small", "image_art_crop",
)


def load_cards(conn, cards: Iterable[dict[str, Any]], batch_size: int = 2000) -> int:
    """Insert Scryfall card objects. Returns the number of printings written.

    FTS is rebuilt once at the end rather than maintained per row -- with
    triggers live, a 110k-row load takes many times longer.
    """
    placeholders = ",".join("?" * len(CARD_COLUMNS))
    sql = (f"INSERT OR REPLACE INTO cards ({','.join(CARD_COLUMNS)}) "
           f"VALUES ({placeholders})")

    n = 0
    card_batch: list[tuple] = []
    face_batch: list[tuple] = []
    part_batch: list[tuple] = []

    def flush() -> None:
        if card_batch:
            conn.executemany(sql, card_batch)
            conn.executemany(
                "INSERT OR REPLACE INTO card_faces "
                "(card_id, face_index, name, mana_cost, type_line, oracle_text, image_normal) "
                "VALUES (?,?,?,?,?,?,?)", face_batch)
            conn.executemany(
                "INSERT OR REPLACE INTO card_parts "
                "(card_id, component, part_id, name, type_line) VALUES (?,?,?,?,?)",
                part_batch)
            conn.commit()
        card_batch.clear()
        face_batch.clear()
        part_batch.clear()

    for card in cards:
        row = extract(card)
        card_batch.append(tuple(row[c] for c in CARD_COLUMNS))
        for i, f in enumerate(faces_of(card)):
            face_batch.append((row["id"], i, f["name"], f["mana_cost"],
                               f["type_line"], f["oracle_text"], f["image_normal"]))
        for p in parts_of(card):
            part_batch.append((row["id"], p["component"], p["part_id"],
                               p["name"], p["type_line"]))
        n += 1
        if len(card_batch) >= batch_size:
            flush()
    flush()
    rebuild_fts(conn)
    return n


def rebuild_fts(conn) -> None:
    conn.execute("INSERT INTO cards_fts(cards_fts) VALUES('rebuild')")
    conn.commit()
