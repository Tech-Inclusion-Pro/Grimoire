"""Play journal, and the overview generated from it.

The overview reads **the journal, never the decklist** (spec section 6.5). A
generator that also saw the card list would produce generic Commander advice
that says nothing about how this deck actually plays for you. The cost is that
it says nothing useful until several entries exist, which is the right trade.

Four fixed sections, each of which has to trace back to something written:

    How it feels to play
    When to bring it
    Tricks and lines
    What you keep running into
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.request
from typing import Any

from . import decks, vault

import re

OLLAMA_URL = "http://127.0.0.1:11434"

_LEADING_NUMBER = re.compile(r"^\s*\d+[.)]\s*")

SECTIONS = (
    "How it feels to play",
    "When to bring it",
    "Tricks and lines",
    "What you keep running into",
)

# Enough entries for a pattern to exist. Below this the overview would be
# paraphrasing a single game back at you.
MIN_ENTRIES = 3


class GenerationError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Entries
# --------------------------------------------------------------------------- #

def add_entry(conn, deck_id: int, body: str, result: str | None = None,
              at: str | None = None) -> int:
    if result not in (None, "", "win", "loss"):
        raise ValueError("result must be 'win', 'loss', or nothing")
    cur = conn.execute(
        "INSERT INTO journal (deck_id, at, body, result) VALUES (?,?,?,?)",
        (deck_id, at or now(), body.strip(), result or None))
    conn.commit()
    return cur.lastrowid


def entries(conn, deck_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, at, body, result FROM journal WHERE deck_id=? "
        "ORDER BY at ASC, id ASC", (deck_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_entry(conn, entry_id: int) -> bool:
    cur = conn.execute("DELETE FROM journal WHERE id=?", (entry_id,))
    conn.commit()
    return cur.rowcount > 0


def record(conn, deck_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT result, COUNT(*) n FROM journal WHERE deck_id=? GROUP BY result",
        (deck_id,)).fetchall()
    counts = {r["result"] or "unrecorded": r["n"] for r in rows}
    return {"wins": counts.get("win", 0), "losses": counts.get("loss", 0),
            "unrecorded": counts.get("unrecorded", 0)}


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

def available_models() -> list[dict[str, Any]]:
    """What can generate an overview right now, with the data-flow consequence
    attached to each. The spec is firm that this choice is shown at the moment
    of generation, not buried in settings -- the journal is the most personal
    data in the app."""
    options: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            tags = json.load(r).get("models", [])
    except (urllib.error.URLError, OSError, ValueError):
        tags = []

    for m in tags:
        name = m.get("name", "")
        options.append({
            "id": name,
            "label": name,
            "where": "This Mac mini",
            "consequence": "Your journal entries never leave the machine.",
            "caveat": ("A 7B model competing with a Jellyfin transcode on 16 GB "
                       "will be slow. Try the 3B first."
                       if name.startswith("qwen2.5:7b") or ":7b" in name else None),
            "available": True,
        })
    if not options:
        options.append({
            "id": "", "label": "No local model reachable",
            "where": "—",
            "consequence": "Ollama is not answering on 127.0.0.1:11434.",
            "caveat": None, "available": False,
        })
    return options


def generate_overview(conn, deck_id: int, model: str) -> dict[str, Any]:
    """Generate the overview from journal entries alone."""
    written = entries(conn, deck_id)
    if len(written) < MIN_ENTRIES:
        raise GenerationError(
            f"Only {len(written)} journal "
            f"{'entry' if len(written) == 1 else 'entries'} so far. The overview "
            f"reads what you wrote rather than the decklist, so it needs at "
            f"least {MIN_ENTRIES} to say anything you did not already know.")

    prompt = build_prompt(written)
    text = _ask_ollama(model, prompt)
    sections = parse_sections(text)
    if not any(v.strip() for v in sections.values()):
        raise GenerationError(
            "The model returned nothing usable. Try again, or a different model.")
    return {"sections": sections, "model": model, "at": now(),
            "entries_used": len(written)}


def build_prompt(written: list[dict[str, Any]]) -> str:
    lines = [
        "You are helping someone summarise their own Magic: The Gathering play "
        "journal for one deck.",
        "",
        "You are given ONLY their journal entries. You do not know the decklist "
        "and must not guess at one. Never mention a card that does not appear "
        "in the entries below.",
        "",
        "Every sentence must trace back to something they actually wrote. If "
        "the entries do not support a section, write 'Not enough written down "
        "yet.' for that section rather than inventing something.",
        "",
        "Write in second person ('you'), plainly, no hype. Two to four "
        "sentences per section.",
        "",
        "Use exactly these four headings, each on its own line:",
        *[f"## {s}" for s in SECTIONS],
        "",
        "--- JOURNAL ENTRIES ---",
    ]
    for e in written:
        tag = {"win": "WIN", "loss": "LOSS"}.get(e["result"] or "", "no result recorded")
        lines.append(f"\n[{e['at'][:16].replace('T', ' ')} — {tag}]\n{e['body']}")
    lines.append("\n--- END ---")
    return "\n".join(lines)


def parse_sections(text: str) -> dict[str, str]:
    """Pull the four sections back out. Models drift on heading format, so
    match loosely on the section name rather than on '## ' exactly."""
    out = {s: "" for s in SECTIONS}
    current: str | None = None
    for line in text.split("\n"):
        # Models drift: '## Heading', '**Heading**', 'Heading:', '3. Heading'
        # are all things they return. Strip the decoration and compare names.
        stripped = line.strip()
        stripped = _LEADING_NUMBER.sub("", stripped)
        stripped = stripped.strip("#*_ \t").rstrip(":").strip("#*_ \t")
        matched = next((s for s in SECTIONS if stripped.lower() == s.lower()), None)
        if matched:
            current = matched
            continue
        if current:
            out[current] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


def _ask_ollama(model: str, prompt: str, timeout: int = 300) -> str:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        # Low temperature: this is summarising what someone wrote, not writing
        # fiction about it.
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r).get("response", "")
    except urllib.error.HTTPError as e:
        raise GenerationError(f"Ollama rejected the request: {e.read()[:200]!r}") from e
    except (urllib.error.URLError, OSError) as e:
        raise GenerationError(
            f"Could not reach Ollama on {OLLAMA_URL}. Is it running?") from e


# --------------------------------------------------------------------------- #
# Vault sync
# --------------------------------------------------------------------------- #

def sync_deck(conn, deck_id: int, *, overview: dict[str, Any] | None = None,
              root: Any = None) -> dict[str, Any]:
    """Write this deck's note into the vault.

    The whole journal is rebuilt from the database each time rather than
    appended blindly, so deleting an entry in Grimoire removes it from the note
    too. Everything outside the sections Grimoire owns is left alone."""
    deck = conn.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
    if deck is None:
        raise LookupError("no such deck")

    stats = decks.deck_stats(conn, deck_id)
    cards = decks.deck_cards(conn, deck_id)
    colors = sorted({ch for c in cards for ch in (c["color_identity"] or "")})

    commander = None
    if deck["commander_oracle_id"]:
        row = conn.execute("SELECT name FROM cards WHERE oracle_id=? LIMIT 1",
                           (deck["commander_oracle_id"],)).fetchone()
        commander = row["name"] if row else None

    rendered = [vault.render_entry(e["at"], e["body"], e["result"])
                for e in entries(conn, deck_id)]

    overview_text = None
    if overview:
        overview_text = vault.render_overview(
            overview["sections"], overview["model"], overview["at"][:16].replace("T", " "))

    path = vault.write_deck_note(
        {"name": deck["name"], "commander": commander, "colors": colors,
         "format": deck["format"], "bracket_claimed": deck["bracket_claimed"]},
        stats,
        entries=rendered,
        overview=overview_text,
        root=root,
    )
    conn.execute("UPDATE decks SET vault_path=? WHERE id=?", (str(path), deck_id))
    conn.commit()
    return {"path": str(path), "entries": len(rendered),
            "overview_written": overview_text is not None}


def rebuild_journal_section(conn, deck_id: int) -> list[str]:
    return [vault.render_entry(e["at"], e["body"], e["result"])
            for e in entries(conn, deck_id)]
