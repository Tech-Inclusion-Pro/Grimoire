"""Import a collection CSV.

    uv run python scripts/import_csv.py "<file.csv>"            # dry run
    uv run python scripts/import_csv.py "<file.csv>" --apply    # write it
    uv run python scripts/import_csv.py "<file.csv>" --apply --location "Binder A"
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from backend import csv_import, db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=pathlib.Path)
    ap.add_argument("--apply", action="store_true", help="write it (default is a dry run)")
    ap.add_argument("--location", default=None, help="file every copy into this location")
    ap.add_argument("--show", type=int, default=12, help="how many problem rows to list")
    args = ap.parse_args()

    conn = db.open_db()
    headers, rows = csv_import.read_csv(args.file.read_text(encoding="utf-8-sig"))
    fmt = csv_import.detect_format(headers)
    mapping = csv_import.detect_mapping(headers)

    print(f"file      : {args.file.name}")
    print(f"detected  : {fmt}")
    print(f"columns   : {len(headers)}, rows: {len(rows):,}\n")
    print("column mapping")
    for field, col in mapping.items():
        mark = " " if col else "-"
        print(f"  {mark} {field:<16} <- {col or '(not present)'}")
    unmapped = [h for h in headers if h not in set(filter(None, mapping.values()))]
    if unmapped:
        print(f"\n  ignored columns: {', '.join(unmapped)}")

    plan = csv_import.build_plan(conn, rows, mapping, fmt)
    s = plan.summary()
    print(f"\nplan")
    print(f"  rows              {s['rows']:,}")
    print(f"  physical cards    {s['cards']:,}")
    print(f"  matched by id     {s['matched_exactly']:,}")
    print(f"  substituted       {s['substitutions']:,}")
    print(f"  unresolved        {s['unresolved']:,}")
    print(f"  finishes          {s['finishes']}")

    if plan.substitutions:
        print(f"\nsubstitutions (first {args.show}):")
        for c in plan.substitutions[:args.show]:
            print(f"  line {c.line:<6} {c.name[:38]:<38} {c.note}")
    if plan.unresolved:
        print(f"\nunresolved (first {args.show}) — these would NOT be imported:")
        for c in plan.unresolved[:args.show]:
            print(f"  line {c.line:<6} {c.name[:38]:<38} {c.note}")

    if not args.apply:
        print("\nDry run. Nothing was written. Re-run with --apply to import.")
        return 0

    location_id = None
    if args.location:
        row = conn.execute("SELECT id FROM locations WHERE name=?", (args.location,)).fetchone()
        if row:
            location_id = row["id"]
        else:
            location_id = conn.execute(
                "INSERT INTO locations (name) VALUES (?)", (args.location,)).lastrowid
            conn.commit()

    result = csv_import.apply_plan(conn, plan, location_id=location_id)
    print(f"\nwrote {result['copies_written']:,} copies")
    n = conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0]
    d = conn.execute("SELECT COUNT(DISTINCT cards.oracle_id) FROM copies "
                     "JOIN cards ON cards.id=copies.card_id").fetchone()[0]
    print(f"collection now: {n:,} copies / {d:,} distinct cards")

    # Spec section 7: recompute on import. Newly-owned cards are invisible to
    # synergy search until they have vectors.
    from backend import embeddings  # noqa: PLC0415
    state = embeddings.status(conn)
    if state["stale"] > 0:
        print(f"\n{state['stale']:,} cards are not in the semantic index yet. "
              f"Run:\n  uv run python -c \"from backend import db, embeddings; "
              f"embeddings.build(db.open_db())\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
