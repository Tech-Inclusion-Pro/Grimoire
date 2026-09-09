"""CSV collection import.

Nothing is written until the plan is approved (spec section 6.6). Importing
runs in two steps:

    plan   = build_plan(conn, rows, mapping)   # pure, touches nothing
    result = apply_plan(conn, plan)            # writes

Resolution order for each row, best first:

1. **Scryfall printing id**, when the export carries one. ManaBox and Moxfield
   both do, and it is exact -- no name matching, no ambiguity.
2. **Set code + collector number**, which identifies a printing precisely.
3. **Name**, preferring a paper printing and the most recent set.

Step 1 can still land on a printing you cannot own: ManaBox happily records
MTGO-only printings for cards held in paper, and those are filtered out of the
card database as unownable. Such rows fall through to step 2/3 and are reported
as substitutions rather than silently dropped or silently accepted.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import io
import re
from typing import Any, Iterable, Sequence

# Grimoire's own field names, and the column headers that map onto them.
# Longest, most specific aliases first: 'Scryfall ID' must beat 'ID'.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "scryfall_id": ("scryfall id", "scryfall_id", "scryfallid", "id"),
    "name":        ("name", "card name", "card"),
    "set_code":    ("set code", "set_code", "setcode", "edition code", "set"),
    "set_name":    ("set name", "set_name", "edition"),
    "collector_num": ("collector number", "collector_number", "card number",
                      "collectornumber", "number", "cn"),
    "quantity":    ("quantity", "count", "qty", "amount", "have"),
    "finish":      ("foil", "finish", "printing", "is foil"),
    "condition":   ("condition", "card condition"),
    "language":    ("language", "lang"),
    "purchase_price": ("purchase price", "purchase_price", "price bought", "cost"),
    "acquired_at": ("added", "date added", "acquired", "added at"),
    "notes":       ("notes", "comment", "comments"),
}

# Values a 'foil' column can hold, normalised to Grimoire's three finishes.
FINISH_VALUES = {
    "": "normal", "normal": "normal", "nonfoil": "normal", "non-foil": "normal",
    "false": "normal", "0": "normal", "no": "normal", "regular": "normal",
    "foil": "foil", "true": "foil", "1": "foil", "yes": "foil", "holo": "foil",
    "etched": "etched", "etched foil": "etched",
    "glossy": "foil", "gilded": "foil", "textured": "foil", "surge": "foil",
}

CONDITION_VALUES = {
    "near_mint": "NM", "near mint": "NM", "nm": "NM", "mint": "M", "m": "M",
    "lightly_played": "LP", "lightly played": "LP", "lp": "LP",
    "moderately_played": "MP", "moderately played": "MP", "mp": "MP",
    "heavily_played": "HP", "heavily played": "HP", "hp": "HP",
    "damaged": "DMG", "dmg": "DMG", "excellent": "LP", "good": "MP",
    "played": "MP", "poor": "DMG",
}


class ImportError_(ValueError):
    """Raised for a CSV that cannot be read at all."""


# --------------------------------------------------------------------------- #
# Column mapping
# --------------------------------------------------------------------------- #

def detect_mapping(headers: Sequence[str]) -> dict[str, str | None]:
    """Guess which column feeds which field. The review screen shows this and
    the user corrects it; nothing here is trusted blindly."""
    lowered = {h.strip().lower(): h for h in headers}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for field, aliases in FIELD_ALIASES.items():
        chosen = None
        for alias in aliases:
            if alias in lowered and lowered[alias] not in used:
                chosen = lowered[alias]
                used.add(chosen)
                break
        mapping[field] = chosen
    return mapping


def detect_format(headers: Sequence[str]) -> str:
    h = {x.strip().lower() for x in headers}
    if "manabox id" in h:
        return "ManaBox"
    if "moxfield id" in h or ("tradelist count" in h and "purchase price" in h):
        return "Moxfield"
    if "delver" in " ".join(h):
        return "Delver Lens"
    if "product line" in h or "tcgplayer id" in h:
        return "TCGplayer"
    if "scryfall id" in h:
        return "Scryfall-based export"
    return "Unknown"


def read_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """Parse CSV text. Handles a UTF-8 BOM, and quoted fields containing commas
    -- 'Kongming, "Sleeping Dragon"' is a real card name."""
    if text.startswith("﻿"):
        text = text[1:]
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ImportError_("That file has no header row.")
    rows = [r for r in reader]
    if not rows:
        raise ImportError_("That file has a header but no rows.")
    return list(reader.fieldnames), rows


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class PlannedCopy:
    line: int
    name: str
    card_id: str | None
    quantity: int
    finish: str
    condition: str | None
    language: str
    purchase_price: float | None
    acquired_at: str | None
    how: str                 # 'id' | 'set+number' | 'name' | 'unresolved'
    note: str = ""


@dataclasses.dataclass
class Plan:
    copies: list[PlannedCopy]
    unresolved: list[PlannedCopy]
    substitutions: list[PlannedCopy]
    mapping: dict[str, str | None]
    source_format: str

    @property
    def total_cards(self) -> int:
        return sum(c.quantity for c in self.copies)

    def summary(self) -> dict[str, Any]:
        return {
            "source_format": self.source_format,
            "mapping": self.mapping,
            "rows": len(self.copies) + len(self.unresolved),
            "cards": self.total_cards,
            "matched_exactly": sum(1 for c in self.copies if c.how == "id"),
            "substitutions": len(self.substitutions),
            "unresolved": len(self.unresolved),
            "finishes": _count(c.finish for c in self.copies),
        }


def _count(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _norm_finish(raw: str) -> str:
    return FINISH_VALUES.get((raw or "").strip().lower(), "normal")


def _norm_condition(raw: str) -> str | None:
    v = (raw or "").strip().lower()
    return CONDITION_VALUES.get(v, raw.strip() or None)


def _norm_date(raw: str) -> str | None:
    v = (raw or "").strip()
    if not v:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", v)
    return m.group(1) if m else v


def _to_float(raw: str) -> float | None:
    v = (raw or "").strip().replace("$", "").replace(",", "")
    try:
        return float(v) if v else None
    except ValueError:
        return None


def build_plan(conn, rows: list[dict[str, str]], mapping: dict[str, str | None],
               source_format: str = "Unknown") -> Plan:
    """Work out what would be written. Touches nothing."""
    get = lambda row, field: (row.get(mapping[field]) or "").strip() if mapping.get(field) else ""

    copies: list[PlannedCopy] = []
    unresolved: list[PlannedCopy] = []
    substitutions: list[PlannedCopy] = []

    for i, row in enumerate(rows, start=2):     # line 1 is the header
        name = get(row, "name")
        try:
            quantity = int(get(row, "quantity") or 1)
        except ValueError:
            quantity = 1
        if quantity < 1:
            continue

        planned = PlannedCopy(
            line=i, name=name, card_id=None, quantity=quantity,
            finish=_norm_finish(get(row, "finish")),
            condition=_norm_condition(get(row, "condition")),
            language=(get(row, "language") or "en").lower(),
            purchase_price=_to_float(get(row, "purchase_price")),
            acquired_at=_norm_date(get(row, "acquired_at")),
            how="unresolved",
        )

        card_id, how, note = _resolve(conn, row, get)
        planned.card_id, planned.how, planned.note = card_id, how, note

        if card_id is None:
            unresolved.append(planned)
        else:
            copies.append(planned)
            if how != "id":
                substitutions.append(planned)

    return Plan(copies, unresolved, substitutions, mapping, source_format)


def _resolve(conn, row: dict[str, str], get) -> tuple[str | None, str, str]:
    scryfall_id = get(row, "scryfall_id")
    name = get(row, "name")
    set_code = get(row, "set_code").lower()
    collector = get(row, "collector_num")

    if scryfall_id:
        hit = conn.execute("SELECT id FROM cards WHERE id = ?", (scryfall_id,)).fetchone()
        if hit:
            return hit["id"], "id", ""

    if set_code and collector:
        hit = conn.execute(
            "SELECT id FROM cards WHERE LOWER(set_code)=? AND LOWER(collector_num)=? "
            "AND digital=0 LIMIT 1", (set_code, collector.lower())).fetchone()
        if hit:
            note = ("the export's Scryfall id is an MTGO-only printing; "
                    "matched the paper card by set and number") if scryfall_id else ""
            return hit["id"], "set+number", note

    if name:
        # Prefer the same set, then the most recent paper printing.
        hit = conn.execute(
            "SELECT id, set_code FROM cards WHERE LOWER(name)=LOWER(?) AND digital=0 "
            "ORDER BY (LOWER(set_code)=?) DESC, released_at DESC LIMIT 1",
            (name, set_code)).fetchone()
        if hit:
            return hit["id"], "name", f"matched by name onto set {hit['set_code'].upper()}"
        # Multi-face cards are stored under their combined 'A // B' name.
        hit = conn.execute(
            "SELECT id, set_code FROM cards WHERE LOWER(name) LIKE LOWER(?) AND digital=0 "
            "ORDER BY released_at DESC LIMIT 1", (f"{name} // %",)).fetchone()
        if hit:
            return hit["id"], "name", f"matched front face onto set {hit['set_code'].upper()}"

    return None, "unresolved", "no printing matched by id, set+number, or name"


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #

def apply_plan(conn, plan: Plan, location_id: int | None = None,
               batch_size: int = 500) -> dict[str, Any]:
    """Write the plan. One `copies` row per physical card, because condition,
    finish, and location are per-copy facts."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    written = 0
    batch: list[tuple] = []

    for planned in plan.copies:
        for _ in range(planned.quantity):
            batch.append((
                planned.card_id,
                planned.finish,
                1 if planned.finish in ("foil", "etched") else 0,
                planned.condition,
                planned.language,
                location_id,
                planned.acquired_at,
                planned.purchase_price,
                planned.note or None,
            ))
        if len(batch) >= batch_size:
            written += _flush(conn, batch)
    written += _flush(conn, batch)

    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_csv_import', ?)",
                 (now,))
    conn.commit()
    return {"copies_written": written, "at": now,
            "unresolved": len(plan.unresolved),
            "substitutions": len(plan.substitutions)}


def _flush(conn, batch: list[tuple]) -> int:
    if not batch:
        return 0
    conn.executemany(
        "INSERT INTO copies (card_id, finish, foil, condition, language, "
        "location_id, acquired_at, purchase_price, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?)", batch)
    n = len(batch)
    batch.clear()
    return n
