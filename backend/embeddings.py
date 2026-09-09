"""Semantic search over the cards you own.

Keyword search finds cards that share vocabulary. Embeddings find cards that
share a *function* — "put lands onto the battlefield" and "search your library
for a basic land" describe the same job in different words, and only one of
those matches a search for "ramp".

Scope is deliberately small (spec section 7): **owned cards only**, one vector
per game object. That is 8,363 vectors here, roughly 25 MB as float32 — small
enough to hold in memory and brute-force at query time. No vector database, no
SQLite extension, nothing to keep in sync.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable, Sequence

import numpy as np

OLLAMA_URL = "http://127.0.0.1:11434"
MODEL = "nomic-embed-text"
DIMS = 768
BATCH = 64


class EmbeddingError(RuntimeError):
    pass


def card_text(name: str, type_line: str | None, oracle_text: str | None) -> str:
    """What gets embedded. Name and type line are included because they carry
    real signal -- 'Elf Druid' says more about function than most rules text."""
    return "\n".join(p for p in (name, type_line or "", oracle_text or "") if p)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def embed(texts: Sequence[str], model: str = MODEL, timeout: int = 180) -> list[list[float]]:
    body = json.dumps({"model": model, "input": list(texts)}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "replace")
        if "not found" in detail:
            raise EmbeddingError(
                f"Ollama does not have '{model}'. Run: ollama pull {model}") from e
        raise EmbeddingError(f"Ollama rejected the request: {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise EmbeddingError(f"Could not reach Ollama on {OLLAMA_URL}.") from e

    vectors = payload.get("embeddings")
    if not vectors or len(vectors) != len(texts):
        raise EmbeddingError("Ollama returned a different number of vectors than inputs.")
    return vectors


def to_blob(vector: Sequence[float]) -> bytes:
    """float32, L2-normalised on the way in.

    Normalising at write time means a query is a plain dot product rather than
    a cosine with two magnitudes to divide out on every one of 8,000 rows."""
    arr = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm:
        arr = arr / norm
    return arr.tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #

def owned_cards(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """One row per owned game object, using a paper printing for the text."""
    rows = conn.execute("""
        SELECT c.oracle_id,
               MIN(c.name)        AS name,
               MIN(c.type_line)   AS type_line,
               MIN(c.oracle_text) AS oracle_text
        FROM copies cp
        JOIN cards c ON c.id = cp.card_id
        WHERE c.digital = 0
          AND c.layout NOT IN ('art_series','token','double_faced_token','emblem')
        GROUP BY c.oracle_id
        ORDER BY c.oracle_id
    """).fetchall()
    return [dict(r) for r in rows]


def build(conn: sqlite3.Connection, *, model: str = MODEL, force: bool = False,
          on_progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Embed every owned card that does not already have a current vector.

    Cards whose text has not changed are skipped, so a rebuild after buying ten
    cards costs ten embeddings rather than eight thousand."""
    cards = owned_cards(conn)
    existing: dict[str, str] = {}
    if not force:
        existing = {r["oracle_id"]: r["model"] for r in
                    conn.execute("SELECT oracle_id, model FROM embeddings")}

    todo = []
    for card in cards:
        text = card_text(card["name"], card["type_line"], card["oracle_text"])
        # The stored 'model' column carries model + text hash, so a card whose
        # oracle text was errata'd gets re-embedded.
        stamp = f"{model}:{text_hash(text)}"
        if existing.get(card["oracle_id"]) != stamp:
            todo.append((card["oracle_id"], text, stamp))

    written = 0
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        vectors = embed([t for _, t, _ in chunk], model=model)
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings (oracle_id, vector, model, built_at) "
            "VALUES (?,?,?,datetime('now'))",
            [(oid, to_blob(vec), stamp) for (oid, _, stamp), vec in zip(chunk, vectors)])
        conn.commit()
        written += len(chunk)
        if on_progress:
            on_progress(written, len(todo))

    # Drop vectors for cards no longer owned, so the index cannot suggest a
    # card that has left the collection.
    owned_ids = {c["oracle_id"] for c in cards}
    stale = [r[0] for r in conn.execute("SELECT oracle_id FROM embeddings")
             if r[0] not in owned_ids]
    if stale:
        conn.executemany("DELETE FROM embeddings WHERE oracle_id=?",
                         [(o,) for o in stale])
        conn.commit()

    return {"owned": len(cards), "embedded": written, "skipped": len(cards) - len(todo),
            "removed": len(stale), "model": model}


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    owned = conn.execute("""
        SELECT COUNT(DISTINCT c.oracle_id) FROM copies cp JOIN cards c ON c.id=cp.card_id
        WHERE c.digital=0
          AND c.layout NOT IN ('art_series','token','double_faced_token','emblem')
    """).fetchone()[0]
    built = conn.execute("SELECT MAX(built_at) FROM embeddings").fetchone()[0]
    return {"embedded": total, "owned": owned, "stale": owned - total,
            "built_at": built, "model": MODEL, "ready": total > 0}


# --------------------------------------------------------------------------- #
# Searching
# --------------------------------------------------------------------------- #

class Index:
    """Every owned card's vector in one matrix. Rebuilt per query — loading
    8,000 rows from SQLite takes a few milliseconds, and it means the index can
    never be stale relative to the database."""

    def __init__(self, ids: list[str], matrix: np.ndarray) -> None:
        self.ids = ids
        self.matrix = matrix

    def __len__(self) -> int:
        return len(self.ids)

    @classmethod
    def load(cls, conn: sqlite3.Connection,
             limit_to: Iterable[str] | None = None) -> "Index":
        rows = conn.execute(
            "SELECT oracle_id, vector FROM embeddings ORDER BY oracle_id").fetchall()
        if limit_to is not None:
            allowed = set(limit_to)
            rows = [r for r in rows if r["oracle_id"] in allowed]
        if not rows:
            return cls([], np.zeros((0, DIMS), dtype=np.float32))
        ids = [r["oracle_id"] for r in rows]
        matrix = np.stack([from_blob(r["vector"]) for r in rows])
        return cls(ids, matrix)

    def search(self, query_vector: Sequence[float], top: int = 50
               ) -> list[tuple[str, float]]:
        """Cosine similarity, which is a dot product because both sides are
        already normalised."""
        if not len(self.ids):
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        norm = float(np.linalg.norm(q))
        if norm:
            q = q / norm
        scores = self.matrix @ q
        top = min(top, len(scores))
        # argpartition finds the top N without sorting all 8,000.
        idx = np.argpartition(-scores, top - 1)[:top]
        idx = idx[np.argsort(-scores[idx])]
        return [(self.ids[i], float(scores[i])) for i in idx]
