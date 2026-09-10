# Grimoire

A personal Magic: The Gathering library, deck builder, and play journal.
Self-hosted on the Mac mini, reached over Tailscale.

Full specification: [`docs/grimoire-build-spec.md`](docs/grimoire-build-spec.md).
Decisions and the Scryfall traps: [`CLAUDE.md`](CLAUDE.md).

## Status

**Build order step 1 of 9 complete** — importer and data model, against the
fixture set, with nothing but a table view.

| Step | | |
|---|---|---|
| 1 | Importer and data model | done |
| 2 | Collection browsing — search, filters, locations, saved searches | done |
| — | Login, matching the server dashboard's auth | done |
| 3 | Deck editor | done |
| 4 | Journal | done |
| 5 | Vault sync | done |
| 6 | Synergy search | done |
| — | CSV import (ManaBox / Moxfield / generic) | done, CLI only |
| 7 | Shelf and packages | done |
| 8 | Camera capture | not started |
| 9 | Playtester | done |

Schema for every module is already in `backend/schema.sql`, so later steps add
routes rather than migrations.

## Setup

```sh
uv sync --extra dev
(cd frontend && npm install && npm run build)
uv run python scripts/import_bulk.py     # ~78 MB download, ~25s import
uv run pytest
```

The frontend is React + Vite + TypeScript and builds into `backend/static`,
which FastAPI serves directly — the same arrangement as the server dashboard.
During development run the backend and the Vite dev server side by side:

```sh
uv run uvicorn backend.main:app --reload --port 8787   # terminal 1
cd frontend && npm run dev                              # terminal 2, :5174
```

Then either run it in the foreground:

```sh
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

or install it as a LaunchAgent and publish it on the tailnet:

```sh
./deploy/install.sh
```

## Search syntax

Scryfall-style, compiled to SQL. Terms combine with spaces (and), `or`, `-` to
negate, and brackets to group.

| Example | Finds |
|---|---|
| `llanowar` | Name contains "llanowar" |
| `!"Llanowar Elves"` | That card exactly |
| `t:creature`, `o:"target creature"` | Type line, rules text (both faces) |
| `c:g` / `c=g` / `c:simic` | Contains green / exactly green / blue-green |
| `id<=g` | Legal in a mono-green commander deck |
| `mv<=3`, `pow>=5`, `usd>=20`, `year>=2024` | Numeric comparisons |
| `r:rare`, `s:dom`, `layout:transform` | Rarity, set code, layout |
| `is:foil`, `is:owned`, `is:lent`, `-is:deck` | How you hold it |
| `loc:"binder a"` | Where the card physically is |

Two rules worth knowing:

- **Predicates are oracle-scoped.** A query resolves to a set of matching
  cards, and every printing of those cards is then counted. `s:dom` means
  "this card was printed in Dominaria", not "only count the Dominaria copy" —
  otherwise a card owned in four printings would report owning one.
- **Unknown syntax is an error.** A misspelled filter that silently matched
  everything would be far worse than a message naming the bad field.

## Playtesting

`/decks/<id>/playtest`. Goldfishing only — **nothing enforces rules**, checks
costs, or stops an illegal play, exactly as the spec scopes it.

Battlefield, hand and the piles share **one playmat**, so nothing has to be
scrolled to reach a drop target. The battlefield is a free-form canvas the way
Archidekt plays it: drag a permanent anywhere and it stays there, click it to
tap (it turns 90°), and cards may overlap. New permanents cascade into open
space; **Tidy up** re-lays them out.

The hand is a strip that scrolls sideways, and library, graveyard, exile and
command are **piles with a count** rather than grids — seventeen library cards
laid out individually pushed everything else off the screen. Click the library
to draw; drag the top card off any pile.

Opening hand, mulligan counter, six zones, tap/untap, +1/+1 counters, life
totals, a turn counter and a log. Starting life follows the format: 40 for
Commander, 20 otherwise, and the commander begins in the command zone.

**Two ways to move a card**, always: click it and pick a destination, or drag
it. Clicking a permanent on the battlefield taps it instead, since that is what
a click means there; its action row carries the destinations.

Keyboard: <kbd>D</kbd> draw, <kbd>U</kbd> untap all, <kbd>N</kbd> next turn,
<kbd>M</kbd> mulligan, <kbd>F</kbd> full screen, <kbd>Esc</kbd> deselect.

Dragging is never the *only* way to do anything: WCAG 2.5.7 forbids that. Zone
changes are buttons, and the arrow keys nudge whichever permanent is selected
(hold Shift to move further). Pointer events rather than HTML5 drag-and-drop,
because HTML5 drag does not work on touch at all.

## Reading a card

Click any card anywhere — collection, deck, shelf, synergy results, playtest —
and it opens full height with the printed text written out beside it: mana
cost, colour identity, mana value, type line, rules text line by line,
power/toughness or loyalty, flavour, set, rarity, price, artist, format
legality, which decks use it, and **where your copies physically are**.

Side by side above 900px, stacked below it. Double-faced cards get a button to
turn them over. <kbd>Esc</kbd> closes, and Tab stays inside the dialog.

In the playtester, tapping a card reads it and a **Move** button starts a zone
change — reading a card mid-goldfish is the more common action, and it keeps
the gesture the same as everywhere else.

## Phones and tablets

Works in any modern mobile browser over the tailnet — no app, no install.
Verified by measurement at 360px (Galaxy S24), 834px (iPad Pro 11 portrait) and
1194px (landscape):

- No horizontal page scroll on any view at any of those widths.
- Smallest interactive target is 24px, meeting WCAG 2.2 AA (2.5.8).
- The rail becomes a wrapping tab strip below 780px, so all four sections stay
  visible rather than hiding behind a sideways scroll.

Two CSS rules do most of the work and are easy to undo by accident:
`grid-template-columns: minmax(0, 1fr)` and `min-width: 0` on flex children.
Both default to `auto`, which refuses to shrink below content — one wide input
was dragging the whole page sideways.

Needs iOS 15.4+ or Chrome 105+ for `:has()`; older browsers just lose the
highlight on selected field chips, nothing functional.

## Card images

Cards render as cards. Art is fetched from Scryfall once, written to
`data/images/`, and served locally from then on — so browsing needs no network,
which is what local-first actually means.

Three variants are cached on demand: `small` (~11 KB, used below 165px),
`normal` (~68 KB) and `art_crop` (~35 KB, for deck banners). Only cards you
actually look at are downloaded. Browsing the whole owned collection at full
size would come to roughly 550 MB.

**Images are never cropped, distorted, recoloured or watermarked** — the
Scryfall licence forbids all four, which is also why the grid gives every card
its full frame instead of tightening it for density. `art_crop` is Scryfall's
own published variant, which is a different thing from cropping their art.

The deck editor also has a card picker: type a name, see matches with art, and
add them one at a time. Owned-only by default; adding something you do not own
marks it wishlist on the way in, so the buildable count stays honest.

Both the collection and the deck editor have a **Cards / Table** toggle, a
multi-select for what to show under each card (name, mana cost, mana value,
colours, type, price, copies held), and a size slider. Choices are remembered
per view in `localStorage`.

## Synergy search

Describe a plan in plain language; get back cards you already own, ranked, each
with a stated reason. Needs a semantic index built first:

```sh
ollama pull nomic-embed-text
uv run python -c "from backend import db, embeddings; print(embeddings.build(db.open_db()))"
```

One 768-dimension vector per owned card, ~2 minutes for 8,300 cards, held in
memory and brute-forced at query time — no vector database, no SQLite
extension. Rebuild after importing cards; only the new ones are processed.

Ranking blends semantic similarity with **what the card actually does**, read
off its rules text. Similarity alone ranked Metalwork Colossus above every
removal spell for "I need cheap removal", because short goals embed vaguely.

Every result states why it surfaced, and **weak matches name their weakness**
rather than being hidden — "surfaced because it does lifegain, not because it
fits the theme" tells you what the search actually did.

## The shelf

Synergy results land on the shelf, **never straight into a deck**: a search
result must not quietly modify a list already built. Cards are parked with a
note, then sent to a deck as a batch — and a deck snapshot is taken first, so a
bad batch is one Restore away. Packages are reusable card groups that load onto
the shelf, which stays the only route into a deck.

## The vault

One note per deck at `MTG/Decks/<Deck Name>.md` inside the Obsidian vault
(`GRIMOIRE_VAULT`, set in the LaunchAgent). Frontmatter follows this vault's own
conventions — `type: mtg-deck`, a `project:` wikilink, `Time:` — so the notes
appear in the Bases dashboard alongside everything else.

**Sync is one way.** Grimoire owns the frontmatter keys it writes, the
`## Overview` section, and the entry block between the
`<!-- grimoire:entries:begin -->` markers inside `## Journal`. Everything else
in the file is yours and is never touched: hand-written sections, extra
frontmatter keys, tags you added, and notes typed inside the Journal section
around the marked block all survive a resync.

Three safeguards, because every one of these failure modes is silent:

- Writes go to a temp file and are `os.replace()`d into place, so iCloud never
  uploads a half-written note.
- Entries are saved on submit, never on keystroke — rapid small writes into a
  synced folder produce conflict copies.
- If iCloud has evicted a note (Optimize Mac Storage leaves a hidden
  `.<name>.icloud` placeholder), Grimoire **refuses to write** rather than
  overwriting real content with a placeholder's emptiness.

**Optimize Mac Storage is currently ON for this Mac.** Nothing is evicted right
now, but the guard above exists because it can happen at any time. Turning it
off in System Settings → Apple Account → iCloud → iCloud Drive removes the risk.

## Authentication

Same design as the server dashboard: PBKDF2 password hash, HMAC-signed session
cookie, stdlib only. Config lives in `config/auth.json` (mode 0600, never
committed).

```sh
uv run python -m backend.auth set-password 'your password' --days 30
uv run python -m backend.auth status
uv run python -m backend.auth disable      # turn it off without losing the password
```

Omit `--days` for a cookie that dies when the browser closes, which is how the
dashboard behaves. `--days 30` is friendlier on an iPad you pick up mid-game.

This is the belt to the ACL's braces: the tailnet is shared with friends for
Jellyfin, so without a login they can read the collection, its value, and the
journal. If `config/auth.json` is present but malformed the service returns
503 rather than quietly serving everything.

## Importing a collection

```sh
uv run python scripts/import_csv.py "export.csv"                     # dry run
uv run python scripts/import_csv.py "export.csv" --apply             # write it
uv run python scripts/import_csv.py "export.csv" --apply --location "Binder A"
```

Autodetects ManaBox, Moxfield, TCGplayer and Delver Lens headers, and maps what
it can from anything else. Nothing is written until `--apply`.

Rows resolve by Scryfall printing id first, then set code plus collector
number, then name. Exports do contain printing ids that cannot be owned —
ManaBox records MTGO-only printings for cards held in paper — so those fall
through to the later rules and are reported as substitutions rather than
dropped silently.

## Refreshing card data

Scryfall rebuilds the bulk file daily. `import_bulk.py` compares `updated_at`
against what was last imported and does nothing if it is current.

```sh
uv run python scripts/import_bulk.py           # refresh if newer
uv run python scripts/import_bulk.py --force   # reimport regardless
```

## Layout

```
backend/schema.sql    the data model, commented with why
backend/scryfall.py   Scryfall card object -> rows; all multi-face handling
backend/bulk.py       download + streaming import of the gzipped JSONL
backend/queries.py    read queries; owned-only by default
backend/main.py       FastAPI
backend/static/       Vite build output (generated, not committed)
frontend/             React + Vite + TypeScript app
tests/fixtures/       real frozen Scryfall data, not hand-written
deploy/               LaunchAgent, install script, Tailscale ACL snippet
```

## Two things to know before changing the importer

`oracle_id` is the card as a game object; `id` is one printing. Decks reference
the former, the collection tracks the latter.

Never decide "is this multi-face?" by checking `layout` against a list — check
whether `card_faces` is present. `CLAUDE.md` has the full list of Scryfall
behaviours that are wrong in plausible-looking ways, each verified against the
live bulk file.
