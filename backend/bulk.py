"""Scryfall bulk data download and import.

The bulk file is the primary source; never call the API for anything it
already answers.

Note on format: Scryfall now publishes bulk data as **gzipped JSONL** only
(``jsonl_download_uri``), not as the single large JSON array the older docs
describe. That is good news -- the file streams line by line, so there is no
need for an incremental JSON parser and no way to accidentally exhaust memory
with ``json.load()``. It is still ~78 MB compressed and several hundred MB
expanded, so it is never held in memory whole.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import pathlib
import shutil
import tempfile
from typing import Callable, Iterator

import httpx

from . import scryfall

import os

# Scryfall asks that clients identify themselves. Set GRIMOIRE_CONTACT to your
# own email or URL so they can reach you if this ever misbehaves.
CONTACT = os.environ.get("GRIMOIRE_CONTACT", "https://github.com/Tech-Inclusion-Pro/Grimoire")
UA = f"Grimoire/0.1 ({CONTACT})"
HEADERS = {"User-Agent": UA, "Accept": "application/json"}
CATALOG = "https://api.scryfall.com/bulk-data"

# 'default_cards' is one row per printing in the default language -- what the
# collection needs. 'oracle_cards' collapses printings and would break the
# collection model entirely.
DEFAULT_KIND = "default_cards"

# Rough expansion factor from the gzipped download to JSONL on disk, used only
# for the free-space check.
EXPANSION = 8


def catalog() -> dict:
    with httpx.Client(headers=HEADERS, timeout=30) as c:
        r = c.get(CATALOG)
        r.raise_for_status()
        return r.json()


def find_bulk(kind: str = DEFAULT_KIND, cat: dict | None = None) -> dict:
    for entry in (cat or catalog())["data"]:
        if entry["type"] == kind:
            return entry
    raise LookupError(f"no bulk data of type {kind!r}")


def download(entry: dict, dest: pathlib.Path,
             on_progress: Callable[[int, int], None] | None = None) -> pathlib.Path:
    """Download to a temp file and rename into place, so an interrupted
    download never leaves a truncated file that looks complete."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = entry.get("compressed_size", 0)
    got = 0
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
    tmp_path = pathlib.Path(tmp)
    try:
        with open(fd, "wb") as out, httpx.Client(headers=HEADERS, timeout=None,
                                                 follow_redirects=True) as c:
            with c.stream("GET", entry["jsonl_download_uri"]) as r:
                r.raise_for_status()
                for chunk in r.iter_bytes(1 << 20):
                    out.write(chunk)
                    got += len(chunk)
                    if on_progress:
                        on_progress(got, total)
        tmp_path.replace(dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return dest


def stream_cards(path: pathlib.Path) -> Iterator[dict]:
    """Yield objects one at a time from a (possibly gzipped) JSONL file."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def import_file(conn, path: pathlib.Path, *, batch_size: int = 2000,
                skip_digital: bool = True,
                on_progress: Callable[[int], None] | None = None) -> int:
    """Load a bulk card file into the cards table. Returns printings written."""
    def source() -> Iterator[dict]:
        for i, card in enumerate(stream_cards(path), 1):
            # Arena/MTGO-only printings can never be a physical copy you own.
            if skip_digital and card.get("digital"):
                continue
            if on_progress and i % 5000 == 0:
                on_progress(i)
            yield card

    n = scryfall.load_cards(conn, source(), batch_size=batch_size)
    _stamp(conn, "bulk_imported_at",
           dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    _stamp(conn, "bulk_card_count", str(n))
    conn.commit()
    return n


def _stamp(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))


def refresh(conn, data_dir: pathlib.Path, kind: str = DEFAULT_KIND,
            *, force: bool = False, verbose: bool = True) -> int:
    """Download the latest bulk file if it is newer than what was imported,
    then import it."""
    entry = find_bulk(kind)
    updated = entry["updated_at"]
    have = conn.execute(
        "SELECT value FROM meta WHERE key='bulk_updated_at'").fetchone()

    if have and have["value"] == updated and not force:
        if verbose:
            print(f"already current ({updated}); nothing to do")
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / f"{kind}.jsonl.gz"
    size = entry.get("compressed_size", 0)
    free = shutil.disk_usage(data_dir).free
    needed = size * EXPANSION
    if size and free < needed:
        raise RuntimeError(
            f"need roughly {needed / 1e9:.1f} GB free for the bulk file and "
            f"import, only {free / 1e9:.1f} GB available")

    if verbose:
        print(f"downloading {kind} ({size / 1e6:.0f} MB gzipped), updated {updated}")

    def prog(got: int, total: int) -> None:
        if total:
            print(f"\r  {got * 100 // total:3d}%  "
                  f"{got / 1e6:6.1f} / {total / 1e6:.0f} MB", end="", flush=True)

    download(entry, dest, on_progress=prog if verbose else None)
    if verbose:
        print("\nimporting...")

    n = import_file(
        conn, dest,
        on_progress=(lambda i: print(f"\r  {i:,} cards read", end="", flush=True))
        if verbose else None)

    _stamp(conn, "bulk_updated_at", updated)
    conn.commit()
    if verbose:
        print(f"\n{n:,} printings imported")
    return n
