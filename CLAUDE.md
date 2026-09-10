# Grimoire

A personal Magic: The Gathering library, deck builder, and play journal.
Single user. Self-hosted on the M1 Mac mini, reached over Tailscale, deck notes
written into the Obsidian vault.

The full specification is `docs/grimoire-build-spec.md`. This file carries the
**decisions**, so a fresh session does not quietly reverse them.

## Decisions — do not re-litigate without being asked

| Decision | Reasoning |
|---|---|
| Wishlist cards allowed in decks, counted separately | A hard owned-only rule makes purchase planning impossible. Counts stay honest. |
| Type-in search is owned-only unless explicitly opted out | Prevents building a deck you cannot sleeve. Opt-in additions are marked wishlist on entry. |
| Quantity tracked per printing, not per card | Foils, sets, and conditions differ. More import friction, correct data. |
| Search returns cards; deck membership is a filter | The alternative is deck-first navigation, a different information architecture. |
| Categories user-defined, suggested once, then untouched | Rigid categories do not match how the user thinks about a list. |
| Shelf sits between synergy results and decks | A search result should never quietly change a built deck. |
| Vault sync is one-way | Two-way sync is a much harder build and a real data-loss risk. |
| Overview reads the journal, not the decklist | Otherwise it produces generic Commander advice. |
| Model choice explicit per generation | The journal is the most personal data here. |
| No social features | Not what this is for. |

**Not built, deliberately:** set completion tracking, price history graphs, deck
comparison, EDHREC integration, custom cards, anything involving other people.

## The distinction everything depends on

`oracle_id` is the card as a game object. `id` is one printing.
**Decks reference `oracle_id`. The collection tracks `id`.** The join between
them is the application's job. Getting it wrong looks fine until it doesn't.

Search results are grouped by `oracle_id`: a card owned in three printings is
one row reporting three copies, never three rows.

## Scryfall facts that are wrong in plausible-looking ways

These were all verified against the live bulk file, not assumed.

- **Bulk data is gzipped JSONL** (`jsonl_download_uri`), not the single JSON
  array the older docs describe. There is no `download_uri` or `size` field
  any more — the entry carries `compressed_size`. Stream it line by line.
- **Never branch on `layout` to decide whether a card is multi-face.** The
  layouts that actually carry `card_faces` in the current bulk file are
  art_series, transform, adventure, split, modal_dfc, double_faced_token,
  reversible_card, flip — **and `prepare`**, which appears in no published list
  and would silently lose 96 cards. Branch on whether `card_faces` is present.
- `transform` and `modal_dfc` have **no** `mana_cost`, `oracle_text`, `colors`
  or `image_uris` at the root at all. `split` and `adventure` are the awkward
  middle: root keeps `mana_cost` and `image_uris`, but still no `oracle_text`.
  Reading `oracle_text` off the root loses 2,273 cards from search.
- **`/cards/collection` resolves the front-face name, not `"A // B"`.** Sending
  the combined name returns `not_found`. Split on `" // "` first.
- `all_parts` lists the card itself as a `combo_piece` of itself. Skip it.
- **`oracle_tags` and `art_tags` are now bulk files.** The spec says they are
  API-only and defers them behind a rate-limited crawl; that is out of date.
  Synergy search can have them cheaply.
- Art cards, tokens, and emblems are in the bulk file — 3,322 distinct — and
  bury real results. `queries.NON_PLAYABLE_LAYOUTS` filters them from search;
  tokens stay reachable through `card_parts` for the packing list.
- `raw` holds the full JSON blob per printing. Keep it. Re-importing to recover
  a field you did not extract is miserable.

## Deployment rules for this machine

- **SQLite lives in `data/`, never in the vault or any synced folder.** Sync
  engines copy files mid-write; the result is a corrupt database.
- **Tailscale Serve, never a bare port.** `http://100.x.x.x:8787` is not a
  secure context, so `getUserMedia` is blocked and camera scanning fails
  silently. Serve provides a real cert on the `ts.net` hostname.
- **The tailnet is shared with friends for Jellyfin.** Without an ACL scoping
  this service to the user's own devices, they can reach the collection, its
  dollar value, and the deck journal.
- **Vault writes are atomic and debounced.** Write to a temp file, `rename()`
  into place, save on submit rather than on keystroke.
- Keep "Optimize Mac Storage" off on the mini; iCloud evicts cold files and
  leaves 0-byte stubs the service would happily overwrite.
- The mini must stay in a logged-in GUI session — iCloud Drive does not sync
  reliably from an SSH-only login, and the failure is silent staleness.

## Search

`query_lang.py` parses Scryfall-style syntax to an AST and compiles it to SQL.

- **Predicates are oracle-scoped**, compiled into
  `c.oracle_id IN (SELECT ... FROM cards cq WHERE <pred>)`. Compiled inline
  instead, a printing-level filter like `s:dom` would also decide which
  printings the outer aggregate counted, so a card owned in four printings
  would report owning one. Same trap as everywhere else here.
- **Ownership filters must be non-correlated `IN`, never correlated `EXISTS`.**
  The EXISTS form makes SQLite scan all 37k oracle_ids and re-run the subquery
  for each: `is:foil` took 8.8 seconds. The `IN` form takes 20 ms.
- **Power and toughness are TEXT.** `CAST('*' AS REAL)` is 0, so any numeric
  comparison needs the `NOT GLOB '*[^0-9.]*'` guard or every `*`-power creature
  reads as a 0-power one. 874 printings have power `*`.
- **Unknown syntax raises `QueryError`**, surfaced as a 400 whose message is
  written to be shown to the user verbatim. A typo'd filter that silently
  matches everything is the failure mode worth designing against.
- Colour identity defaults to *subset* (`id:g` == `id<=g`), colour defaults to
  *contains* (`c:g` == `c>=g`), matching Scryfall.

## Collection import

- The real collection is a **ManaBox** export: 9,080 rows, 13,953 physical
  cards. It carries a `Scryfall ID` column, so resolution is exact and no
  name-matching is needed for 99.9% of rows.
- **Etched is a third finish**, not a kind of foil, and etched cards carry
  *only* `prices.usd_etched` — `usd` and `usd_foil` are both null. Valuing them
  as normal cards silently drops them from the collection total.
- `copies.finish` is the single source of truth. `copies.foil` is a legacy
  derived column: nothing should read it. When the add-copy endpoint wrote only
  `foil`, a foil copy was valued at the non-foil price.
- ManaBox assigns **MTGO-only printing ids** to cards held in paper (5 rows in
  the real export). Those ids are absent from the card table by design, so the
  importer falls back to set+number, then name, and reports the substitution.

## Decks

- `deck_cards` references `oracle_id`. A slot is satisfied by any printing
  owned, so three printings of Llanowar Elves is one slot with three copies.
- **Wishlist cards stay in the list and are counted separately.** `buildable_now`
  never includes a card not held; a hard owned-only rule would make purchase
  planning impossible.
- **Conflicts matter.** Without checking cards committed to other decks, two
  decks each report 100% buildable while sharing one physical copy.
- A category the user set is never overwritten. Suggested once, then untouched.
- Arena export must use the front-face name; it rejects `A // B`.

## Frontend

React + Vite + TypeScript in `frontend/`, building into `backend/static`, which
FastAPI serves as a single-page app. Same arrangement as the server dashboard.

- **The SPA catch-all must 404 unknown `/api/` paths.** A *defined* API route is
  matched before the catch-all, but an undefined one is not: without an explicit
  check, `/api/typo` came back as index.html with a 200 and the frontend tried
  to parse HTML as JSON.
- The static fallback resolves and re-checks every path against the static root,
  so `../pyproject.toml` cannot escape it.
- Routing is real (`/decks/12`), not local state, so a refresh on an iPad keeps
  you in the deck and the back button behaves.
- **A deck slot's representative printing prefers one you own**, then the newest
  paper printing; its price is the cheapest paper printing across all printings.
  Taking the price from the representative row showed a dash for Sol Ring while
  43 copies were owned, because the newest printing had no price yet.
- Tabs implement the WAI-ARIA pattern: arrow keys, Home, End, roving tabindex.
- Charts carry `role="img"` with the data described in words, and the same
  numbers are repeated as text below.

## Playtesting

- Goldfishing only. No rules enforcement anywhere — no cost checks, no timing,
  no legality. The endpoint returns cards and zones; shuffling, drawing and
  every zone move happen in the browser, and nothing is persisted.
- Each copy gets its own `uid` (`<oracle_id>:<index>`), so two copies of the
  same card are distinguishable when moved.
- **The battlefield is a positioned canvas**, matching Archidekt: cards carry
  `x`/`y`, drag repositions, a press that never moved is a tap. Other zones are
  plain grids — nothing about a graveyard is spatial.
- **Use pointer events, not HTML5 drag-and-drop.** HTML5 drag does not fire on
  touch, which is most of how this app is used. `touch-action: none` on the
  field stops the browser scrolling instead of dragging.
- **A drag threshold measures distance from the press, never `e.movementX`.**
  `movementX` is the delta since the *previous* event: a real mouse moved slowly
  reports 1-2px and never crosses any threshold, and on touch it is 0 in most
  browsers. Dragging worked only for synthetic events that set `movementX` by
  hand — the test passed and the feature was broken on every real device.
  When simulating input, simulate what devices actually send.
- **One drag system for every zone**, hoisted into `Playtest`. Drop targets are
  whole `<section>`s carrying `data-zone`: an inner wrapper meant a drop on the
  section's padding hit the `<section>` itself, and `closest()` cannot reach a
  `data-zone` that is a child rather than an ancestor. The battlefield is the
  same — its `data-zone` is on the panel and `data-field` marks the canvas, so
  a drop on the heading or the padding still lands, clamped into the canvas.
  While only the canvas was a target, those drops were silently no-ops and the
  card appeared to snap back.
- **Nothing that changes layout may appear or disappear mid-drag.** Selecting a
  card on pointerdown rendered a panel above the battlefield and pushed it down,
  so cards landed well away from where they were dropped. Selection UI is
  suppressed while a ghost exists, and only the battlefield selects on press.
- **Feedback for a selection must be pinned to the viewport, not placed in the
  flow.** The destination bar rendered above the playmat, which put it ~370px
  off the top of the screen whenever the hand was in view — clicking a card
  worked perfectly and looked like it did nothing. If an action's only feedback
  can scroll out of sight, the feature is invisible and therefore broken.
- **Handle `pointercancel`.** The browser takes a gesture over for a scroll,
  an edge swipe or a second finger; without cleanup the ghost sticks and the
  card never lands. The hand is a horizontal scroll container, which is exactly
  where that happens.
- **Never leave dragging as the only way to move a card.** Rebuilding the hand
  as a strip dropped its Move button, so a failed drag left no way out of hand
  at all. Clicking a card outside the battlefield selects it and offers
  destinations; dragging is the shortcut, not the mechanism.
- **Dragging is never the only route.** WCAG 2.5.7: zone changes are buttons
  and arrow keys nudge the selection. A 4px slop distinguishes a shaky tap from
  a drag.
- Leaving the battlefield resets tapped state and counters, because they only
  mean anything there.

## The deck tile's front card

The commander when one is set; otherwise **the first card listed**, which is
how any non-commander format is identified. Not the priciest card — that was an
earlier guess and it picked arbitrary-looking art.

## Card detail

- `/api/cards/{oracle_id}/detail` returns the printed text, the numbers, held
  copies and deck membership. `card_id` picks a printing; without one, a
  printing you own wins, then the newest paper one.
- Multi-face cards return a `faces` array read from `raw`, because the root
  carries no text for transform/modal_dfc. The modal flips between them.
- **Held copies are grouped, not listed individually.** 41 identical Arcane
  Signets pushed everything else out of the panel; `owned` still counts every
  physical card.
- The dialog traps Tab and closes on Esc.

## Responsive layout

The app is used on a phone and an iPad far more than on a desktop, so layout
regressions matter.

- **`minmax(0, 1fr)` and `min-width: 0`, not `1fr` and the default.** Grid
  tracks and flex items default to `min-width: auto` and will not shrink below
  their content; a single 340px input basis dragged the entire page wider than
  a 360px phone. Every `.row`/`.spread` child gets `min-width: 0`.
- **`minmax(min(var(--card-w), 100%), 1fr)`** for card grids — a fixed 200px
  track overflows a 360px screen otherwise.
- Tap targets are 24px minimum (WCAG 2.5.8). Checkboxes were 18px inline; the
  inline sizes were removed so the stylesheet governs.
- Verify with measurement, not screenshots: Chrome headless clamps
  `--window-size` to 500px wide, so a "phone" screenshot is not one. Use a
  same-origin iframe at the real width and read `scrollWidth` vs `clientWidth`.

## Card images

- Served from `/api/images/{card_id}/{variant}`, cached to `data/images/`,
  fetched from Scryfall exactly once. Hotlinking would have broken the
  local-first commitment the first time the network went away.
- **Never crop, distort, recolour or watermark.** The licence forbids it. The
  card grid therefore gives each card its whole frame rather than tightening it
  for density. `art_crop` is Scryfall's own variant and is fine to use.
- Downloads go through a 6-slot semaphore: a grid of 60 cards would otherwise
  blow straight past Scryfall's 10-requests-per-second guidance.
- Written temp-file-then-rename, like the vault. A half-written image that
  exists is worse than one that does not, because it is never retried.
- Search results and deck rows carry a `card_id` chosen to prefer **a printing
  actually owned**, so the art shown is the copy in the binder.
- `image_small` and `image_art_crop` were backfilled from the stored `raw`
  blob with `json_extract` — no re-download, 9 seconds. That column exists
  precisely so a field nobody thought to extract can be recovered later.

## Synergy and the shelf

- Embeddings cover **owned cards only**, one vector per oracle_id, normalised
  at write time so a query is a dot product rather than a cosine. Loaded fresh
  per query (34 ms for 8,361) so the index can never be stale in memory.
- **Ranking blends similarity with detected role**, it is not pure similarity.
  Pure similarity put Metalwork Colossus above every removal spell for "I need
  cheap removal". `match_pct` stays the honest semantic score; `rank_score`
  carries the blend.
- **Role regexes match against lowercased text.** Writing `add \{[WUBRGC]\}`
  meant "Add {G}" — the most common ramp line in Magic — matched nothing, and
  every mana dork was invisible to ramp detection.
- **Goal roles and card roles need separate patterns.** A goal says "dump mana
  into a finisher"; a card says "add {G}". Every name in `GOAL_PATTERNS` must
  also exist in `ROLES` or role pairing silently does nothing; there is a test.
- **Never claim a card does not do something on thin evidence.** A strong
  similarity plus shared vocabulary suppresses the caveat — telling the user
  Eaten Alive is not a sacrifice card because a regex wanted a colon is worse
  than saying nothing.
- **There is deliberately no route from synergy results into a deck.** The
  shelf is the only path, and there is a test asserting no such endpoint
  exists. Sending from the shelf snapshots the deck first.

## Vault and journal

- **One way, always.** Grimoire owns its frontmatter keys, `## Overview`, and
  the block between `<!-- grimoire:entries:begin/end -->` inside `## Journal`.
  Nothing else in the note is ever rewritten.
- `tags:` is the one managed key that **merges** rather than replaces —
  overwriting hand-curated tags is exactly the silent loss the rule exists to
  prevent.
- The entry block is rebuilt from the database on every sync, not appended.
  Appending duplicated every entry on the second sync; replacing the whole
  `## Journal` section would have eaten hand-written notes. The markers are how
  both are avoided.
- Managed frontmatter is emitted in `MANAGED_KEYS` order every write. Appending
  newly-present keys at the end put `commander` below `updated` the first time
  a deck got one.
- **Refuse to write over an evicted file.** Optimize Mac Storage is ON for this
  Mac; iCloud replaces a cold file with a hidden `.<name>.icloud` stub, and
  writing over it destroys the real content. `vault.check_readable` raises
  instead.
- Writes are atomic (temp file + `os.replace`) and debounced (saved on submit,
  not on keystroke).
- Frontmatter follows this vault's real conventions, not the spec's generic
  example: `type: mtg-deck` (Obsidian **Bases** filters on `type`, this vault
  does not use Dataview), a `project:` wikilink to the MTG project note, and
  `Time: "[[date]]"`.

## The overview

- Generated from **journal entries only**. The decklist is never in the prompt,
  and there is a test asserting it does not leak in. An overview that also read
  the card list would produce generic Commander advice.
- Silent below `journal.MIN_ENTRIES` (3). It has nothing to say about one game.
- The model choice is presented **at generation time with the data-flow
  consequence next to the button**, never in settings — the journal is the most
  personal data in the app.
- `parse_sections` matches section names loosely: models return `## Heading`,
  `**Heading**`, `Heading:` and `1. Heading` interchangeably.

## Auth

Ported from the server dashboard's `backend/auth.py` so there is one pattern
across both services: PBKDF2-SHA256 password hash, HMAC-SHA256 signed session
cookie, stdlib only, CLI to set the password. Grimoire stores it as JSON at
`config/auth.json` rather than YAML, because it has no config loader to share.

- The middleware **fails closed**: a malformed `config/auth.json` returns 503,
  never "auth disabled". On a tailnet shared with other people, degrading to
  open is the one behaviour that must not happen.
- The `Secure` cookie flag is set from `X-Forwarded-Proto`, not the request
  scheme. Tailscale Serve terminates TLS and forwards plain HTTP to uvicorn,
  so `request.url.scheme` is always `http` even when the browser is on HTTPS.
- Non-`/api` paths stay public so the login page and its CSS/JS can paint;
  the page gates its own content via `/api/auth/status`.
- `tests/conftest.py` points `auth.CONFIG_PATH` at a tmpdir for every test.
  Without it the suite reads the real config and the results depend on whether
  the developer happens to have auth enabled.

## Accessibility floor — applies to screens only the user will ever see

WCAG 2.2 AA. 4.5:1 text, 3:1 non-text UI. **Colour never carries meaning
alone**: mana pips include the letter, ownership includes a symbol and a word,
colour tags include the tag name. Visible focus on everything, never
suppressed. `prefers-reduced-motion` kills transitions. Charts get `role="img"`
and a text description. Skip link. Every control labelled.

Brand magenta `#a23b84` is only ~2.98:1 on the substrate. Icons use `#d16bb0`
(5.4:1), same family. Do not "fix" this back to the brand hex.

## Layout

```
backend/
  schema.sql    the data model, commented with why
  db.py         connections; GRIMOIRE_DB overrides the path
  scryfall.py   card object -> rows; all the multi-face handling
  bulk.py       download + streaming import
  queries.py    read queries; owned-only by default
  main.py       FastAPI
  static/       step-1 table view, replaced by the Vite build later
scripts/
  fetch_fixtures.py   refreshes the frozen fixture set from Scryfall
  import_bulk.py      download + import
tests/
  fixtures/scryfall_cards.json   real Scryfall shapes, committed
  test_fixtures.py               the six cases from spec section 12
```

## Working here

- `uv run pytest` before and after any change to the importer or queries.
- The fixture set is **real frozen Scryfall data**, not hand-written JSON.
  Regenerate with `uv run python scripts/fetch_fixtures.py`; do not edit by hand.
- Adding a card-shape bug fix means adding a fixture that reproduces it.
