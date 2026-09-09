"""Writing deck notes into the Obsidian vault.

**Sync is one way.** Grimoire writes the frontmatter it owns and the sections it
generates. Everything else in the file belongs to the user and is never
touched, because the alternative is silently eating prose that only exists here.

Four rules from spec section 3, all of which fail *quietly* rather than loudly
if ignored:

1. The database never lives in the vault. Sync engines copy files mid-write.
2. iCloud's "Optimize Mac Storage" evicts cold files and leaves placeholder
   stubs. Writing over one destroys real content, so `write_deck_note` refuses
   when it sees the eviction marker rather than trusting what it reads.
3. Write to a temp file and `os.replace()` it into place. Rename is atomic, so
   iCloud never uploads a half-written note.
4. Debounce. Save on submit, not on keystroke -- rapid small writes to a synced
   folder produce conflict copies. That one is the caller's job.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import re
import tempfile
from typing import Any

# Point GRIMOIRE_VAULT at your Obsidian vault. The default is a folder beside
# the repo, so a fresh install writes somewhere harmless rather than guessing
# at someone's iCloud path.
DEFAULT_VAULT = pathlib.Path(__file__).resolve().parent.parent / "vault"

# Deck notes live together so a Bases view can pick them up in one filter.
DECK_FOLDER = "MTG/Decks"

# The project note these all hang off, matching the vault's existing layout.
PROJECT_LINK = "[[PROJECT - MTG Decks, Play, and Events]]"

# Frontmatter keys Grimoire owns and will overwrite. Anything else the user
# adds to the block survives untouched.
MANAGED_KEYS = ("Time", "type", "project", "tags", "deck", "commander", "colors",
                "format", "bracket", "owned", "total", "updated", "generated")

# `tags` is the one managed key that is MERGED rather than replaced. Grimoire
# adds its own two, but any tag added by hand in Obsidian stays — overwriting
# someone's curated tags is exactly the silent loss the one-way rule exists to
# prevent.
MERGED_KEYS = ("tags",)

# Section headings Grimoire generates. Everything else in the body is the
# user's and is copied through byte for byte.
OVERVIEW_HEADING = "## Overview"
JOURNAL_HEADING = "## Journal"
INTENT_HEADING = "## What this deck is trying to do"

# Inside "## Journal", only the text between these markers belongs to Grimoire.
# Entries are rebuilt from the database on every sync, so deleting one in the
# app removes it from the note too -- but anything written by hand above or
# below the block is left exactly where it is. Without the markers, a rebuild
# would either duplicate every entry or silently eat hand-written notes.
BEGIN_MARKER = "<!-- grimoire:entries:begin -->"
END_MARKER = "<!-- grimoire:entries:end -->"

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class VaultError(RuntimeError):
    """Raised when writing would risk losing data."""


def vault_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("GRIMOIRE_VAULT", DEFAULT_VAULT))


def deck_filename(name: str) -> str:
    """A safe, still-readable filename. Deck names can contain anything --
    'Kongming, "Sleeping Dragon"' is a legal commander."""
    cleaned = _ILLEGAL.sub("", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "Untitled deck")[:120] + ".md"


def deck_path(name: str, root: pathlib.Path | None = None) -> pathlib.Path:
    return (root or vault_root()) / DECK_FOLDER / deck_filename(name)


def deck_tag(name: str) -> str:
    """A hashtag in the style already used in the vault: #edgar-vampires."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "deck"


# --------------------------------------------------------------------------- #
# Eviction guard
# --------------------------------------------------------------------------- #

def eviction_stub(path: pathlib.Path) -> pathlib.Path:
    """iCloud replaces an evicted file with a hidden '.<name>.icloud' stub."""
    return path.with_name(f".{path.name}.icloud")


def check_readable(path: pathlib.Path) -> None:
    """Refuse to touch a file iCloud has evicted.

    The dangerous case is not a missing file, it is a file that *looks* present
    but whose contents have been replaced by a placeholder. Reading it yields
    nothing and the merge would then write that nothing back."""
    if eviction_stub(path).exists():
        raise VaultError(
            f"{path.name} has been evicted by iCloud (Optimize Mac Storage). "
            f"Open it in Finder to download it, or turn that setting off. "
            f"Refusing to write, because the note on disk is a placeholder and "
            f"writing over it would destroy the real content.")
    if path.exists() and path.stat().st_size == 0:
        raise VaultError(
            f"{path.name} exists but is empty, which usually means iCloud has "
            f"not finished downloading it. Refusing to write over it.")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def split_frontmatter(text: str) -> tuple[dict[str, str], list[str], str]:
    """Return (parsed keys, raw frontmatter lines, body).

    Deliberately not a YAML parser: the point is to preserve the user's exact
    lines, including comments and odd formatting, and only replace the specific
    keys Grimoire owns."""
    if not text.startswith("---"):
        return {}, [], text
    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, [], text
    raw = lines[1:end]
    parsed: dict[str, str] = {}
    for line in raw:
        m = re.match(r"^([A-Za-z_][\w -]*):\s*(.*)$", line)
        if m:
            parsed[m.group(1)] = m.group(2)
    return parsed, raw, "\n".join(lines[end + 1:]).lstrip("\n")


def split_sections(body: str) -> list[tuple[str | None, str]]:
    """Split a markdown body into (heading, content) pairs at '## ' headings.
    Content before the first heading gets a heading of None."""
    out: list[tuple[str | None, str]] = []
    current: str | None = None
    buf: list[str] = []
    for line in body.split("\n"):
        if line.startswith("## "):
            out.append((current, "\n".join(buf).strip("\n")))
            current = line.strip()
            buf = []
        else:
            buf.append(line)
    out.append((current, "\n".join(buf).strip("\n")))
    return [(h, c) for h, c in out if h is not None or c.strip()]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def existing_list(raw: list[str], key: str) -> list[str]:
    """Read a block-list frontmatter value out of the raw lines."""
    out: list[str] = []
    collecting = False
    for line in raw:
        m = re.match(r"^([A-Za-z_][\w -]*):\s*(.*)$", line)
        if m:
            if m.group(1) != key:
                collecting = False
                continue
            collecting = True
            inline = m.group(2).strip()
            if inline.startswith("[") and inline.endswith("]"):
                out.extend(v.strip().strip("\"'") for v in inline[1:-1].split(",") if v.strip())
                collecting = False
            continue
        if collecting:
            stripped = line.strip()
            if stripped.startswith("- "):
                out.append(stripped[2:].strip().strip("\"'"))
            elif stripped:
                collecting = False
    return [v for v in out if v]


def build_frontmatter(existing_raw: list[str], values: dict[str, Any]) -> list[str]:
    """Replace the keys Grimoire owns; keep every other line as written."""
    values = dict(values)
    for key in MERGED_KEYS:
        mine = values.get(key) or []
        theirs = existing_list(existing_raw, key)
        # Theirs first, so the file keeps looking the way they left it.
        values[key] = theirs + [v for v in mine if v not in theirs]
    rendered = {k: _render_value(v) for k, v in values.items() if v is not None}

    # Managed keys are emitted first, always in MANAGED_KEYS order, so the block
    # looks the same every write. Appending new ones at the end instead made
    # `commander` show up below `updated` the first time a deck got one.
    out: list[str] = [line for key in MANAGED_KEYS if key in rendered
                      for line in f"{key}:{rendered[key]}".split("\n")]

    # Then every line the user wrote, in their order, untouched.
    skipping_list = False
    for line in existing_raw:
        m = re.match(r"^([A-Za-z_][\w -]*):\s*(.*)$", line)
        if m:
            skipping_list = m.group(1) in MANAGED_KEYS
            if not skipping_list:
                out.append(line)
            continue
        # Continuation lines of a block list belong to the key above them.
        if skipping_list and (line.startswith("  ") or line.startswith("\t")
                              or line.strip().startswith("- ") or not line.strip()):
            continue
        skipping_list = False
        out.append(line)
    return out


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return " []"
        return "\n" + "\n".join(f"  - {v}" for v in value)
    text = str(value)
    if text.startswith("[[") or ":" in text or text.startswith("#"):
        return f' "{text}"'
    return f" {text}"


def render_overview(sections: dict[str, str], model: str, at: str) -> str:
    """The generated overview, in the vault's own callout style."""
    lines = [
        "> [!info] Generated from your journal entries",
        f"> Written by {model} on {at}. Replaced each time you regenerate.",
        "> Every claim here should trace to something you wrote below.",
        "",
    ]
    for title, text in sections.items():
        if not text.strip():
            continue
        lines.append(f"### {title}")
        lines.append(text.strip())
        lines.append("")
    return "\n".join(lines).strip()


def render_entry(at: str, body: str, result: str | None) -> str:
    """One journal entry. Oldest first, so the note reads chronologically the
    way the existing MTG notes in this vault do."""
    stamp = at.replace("T", " ")[:16]
    label = {"win": " — Win", "loss": " — Loss"}.get(result or "", "")
    return f"### {stamp}{label}\n{body.strip()}\n"


def replace_managed_block(existing: str, entries: list[str]) -> str:
    """Swap Grimoire's entry block into a Journal section, leaving anything the
    user wrote around it untouched."""
    body = "\n\n".join(e.strip() for e in entries)
    block = f"{BEGIN_MARKER}\n{body}\n{END_MARKER}" if body else f"{BEGIN_MARKER}\n{END_MARKER}"
    start = existing.find(BEGIN_MARKER)
    end = existing.find(END_MARKER)
    if start != -1 and end != -1 and end > start:
        return (existing[:start] + block + existing[end + len(END_MARKER):]).strip()
    # No block yet: put ours first, and keep whatever was already there below.
    return (block + ("\n\n" + existing.strip() if existing.strip() else "")).strip()


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def compose(existing: str, *, frontmatter: dict[str, Any], intent_hint: str,
            overview: str | None, entries: list[str]) -> str:
    """Merge Grimoire's content into an existing note without disturbing
    anything the user wrote.

    `entries` is the complete list rebuilt from the database, not a delta: it
    replaces the marked block, so removing an entry in the app removes it here
    too."""
    _, raw_fm, body = split_frontmatter(existing)
    sections = split_sections(body)

    fm = build_frontmatter(raw_fm, frontmatter)

    rebuilt: list[tuple[str | None, str]] = []
    saw_overview = saw_journal = saw_intent = False

    for heading, content in sections:
        if heading == OVERVIEW_HEADING:
            saw_overview = True
            # Generated section: replaced wholesale, which is why it is the
            # only place the user should not write.
            rebuilt.append((heading, overview if overview is not None else content))
        elif heading == JOURNAL_HEADING:
            saw_journal = True
            rebuilt.append((heading, replace_managed_block(content, entries)))
        else:
            if heading == INTENT_HEADING:
                saw_intent = True
            rebuilt.append((heading, content))

    if not saw_intent:
        rebuilt.append((INTENT_HEADING, intent_hint))
    if not saw_overview:
        rebuilt.append((OVERVIEW_HEADING,
                        overview if overview is not None
                        else "_Nothing generated yet. Write a few journal "
                             "entries first — the overview reads them, not the "
                             "decklist._"))
    if not saw_journal:
        rebuilt.append((JOURNAL_HEADING, replace_managed_block("", entries)))

    parts = ["---", *fm, "---", ""]
    for heading, content in rebuilt:
        if heading:
            parts.append(heading)
        if content.strip():
            parts.append(content.strip())
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


def atomic_write(path: pathlib.Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    Rename is atomic on the same filesystem, so a sync engine never sees a
    half-written file and never uploads one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".grimoire-", suffix=".tmp")
    tmp_path = pathlib.Path(tmp)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def write_deck_note(deck: dict[str, Any], stats: dict[str, Any], *,
                    entries: list[str] | None = None,
                    overview: str | None = None,
                    root: pathlib.Path | None = None) -> pathlib.Path:
    """Write (or update) one deck's note. Returns the path written."""
    path = deck_path(deck["name"], root)
    check_readable(path)

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    today = dt.date.today().isoformat()

    colors = sorted(deck.get("colors") or [])
    frontmatter = {
        "Time": f"[[{today}]]",
        "type": "mtg-deck",
        "project": PROJECT_LINK,
        "tags": ["mtg-deck", deck_tag(deck["name"])],
        "deck": deck["name"],
        "commander": deck.get("commander"),
        "colors": colors or None,
        "format": deck.get("format"),
        "bracket": deck.get("bracket_claimed"),
        "owned": stats.get("buildable_now"),
        "total": stats.get("cards"),
        "updated": today,
    }

    text = compose(
        existing,
        frontmatter=frontmatter,
        intent_hint="_Yours to write. Grimoire never edits this section._",
        overview=overview,
        entries=entries or [],
    )
    atomic_write(path, text)
    return path
