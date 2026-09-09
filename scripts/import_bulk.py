"""Download and import the Scryfall bulk file.

    uv run python scripts/import_bulk.py [--force] [--keep-digital]
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from backend import bulk, db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="reimport even if the bulk file has not changed")
    ap.add_argument("--file", type=pathlib.Path,
                    help="import a bulk file already on disk instead of downloading")
    args = ap.parse_args()

    conn = db.open_db()
    data_dir = db.db_path().parent

    if args.file:
        n = bulk.import_file(
            conn, args.file,
            on_progress=lambda i: print(f"\r  {i:,} cards", end="", flush=True))
        print(f"\n{n:,} printings imported from {args.file}")
    else:
        bulk.refresh(conn, data_dir, force=args.force)

    row = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()
    oracle = conn.execute("SELECT COUNT(DISTINCT oracle_id) AS n FROM cards").fetchone()
    print(f"database now holds {row['n']:,} printings / {oracle['n']:,} distinct cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
