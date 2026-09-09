"""Card image cache.

Fetched once from Scryfall, written to disk, never fetched again (spec section
5). Serving them from here rather than hotlinking is what makes the local-first
commitment true: once a card has been seen, browsing it needs no network.

Images are stored exactly as Scryfall sends them. The licence forbids cropping,
distorting, recolouring or watermarking, so nothing here touches the bytes.
`art_crop` is Scryfall's own published variant, which is a different thing from
us cropping their art.
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import threading
import urllib.error
import urllib.request

from . import db

# Scryfall asks that clients identify themselves. Set GRIMOIRE_CONTACT to your
# own email or URL so they can reach you if this ever misbehaves.
CONTACT = os.environ.get("GRIMOIRE_CONTACT", "https://github.com/Tech-Inclusion-Pro/Grimoire")
UA = f"Grimoire/0.1 ({CONTACT})"

# Variants worth caching. 'normal' is the full card; 'small' is the grid
# thumbnail; 'art_crop' is the illustration alone, for deck banners.
VARIANTS = ("small", "normal", "art_crop")

COLUMN = {"small": "image_small", "normal": "image_normal", "art_crop": "image_art_crop"}

# Scryfall asks for no more than 10 image requests a second. A browser opening
# a grid of 60 cards would blow straight past that, so downloads are funnelled
# through a small pool.
_slots = threading.Semaphore(6)


class ImageError(RuntimeError):
    pass


def cache_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get(
        "GRIMOIRE_IMAGE_CACHE", db.db_path().parent / "images"))


def cache_path(card_id: str, variant: str) -> pathlib.Path:
    # Two-character shard: 100k files in one directory makes everything slow.
    return cache_root() / variant / card_id[:2] / f"{card_id}.jpg"


def source_url(conn, card_id: str, variant: str) -> str | None:
    column = COLUMN.get(variant)
    if column is None:
        raise ImageError(f"unknown image variant {variant!r}")
    row = conn.execute(f"SELECT {column} AS url FROM cards WHERE id = ?",
                       (card_id,)).fetchone()
    return row["url"] if row else None


def fetch(conn, card_id: str, variant: str = "normal") -> pathlib.Path:
    """Return a local path for this card's image, downloading it once."""
    path = cache_path(card_id, variant)
    if path.exists() and path.stat().st_size > 0:
        return path

    url = source_url(conn, card_id, variant)
    if not url:
        raise ImageError("no image for that card")

    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with _slots:
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            raise ImageError(f"Scryfall returned {e.code} for that image") from e
        except (urllib.error.URLError, OSError) as e:
            raise ImageError(f"Could not reach Scryfall: {e}") from e

    # Same temp-file-and-rename discipline as the vault: a half-written image
    # that exists is worse than one that does not, because it is never retried.
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".part")
    tmp_path = pathlib.Path(tmp)
    try:
        with open(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return path


def stats() -> dict[str, object]:
    root = cache_root()
    if not root.is_dir():
        return {"files": 0, "bytes": 0, "variants": {}}
    variants: dict[str, int] = {}
    files = total = 0
    for variant in VARIANTS:
        n = 0
        for p in (root / variant).rglob("*.jpg"):
            n += 1
            total += p.stat().st_size
        variants[variant] = n
        files += n
    return {"files": files, "bytes": total, "variants": variants}
