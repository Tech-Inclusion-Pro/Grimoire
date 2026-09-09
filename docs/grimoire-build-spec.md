# Grimoire — Build Spec

**A personal Magic: The Gathering library, deck builder, and play journal.**
Self-hosted on the home server, reached over Tailscale, notes written into the Obsidian vault.

Status: specification. Nothing built yet.
Last updated: 9 September 2026

---

## 1. What this is

A single-user replacement for Archidekt that only ever searches cards you physically own, keeps deck notes in your vault instead of in someone else's database, and uses a local model to help you find cards for a plan you describe in plain language.

It is not a social deckbuilding site. No sharing, no public decks, no following, no comments, no custom cards.

### Design commitments

| Commitment | What it means here |
|---|---|
| Local-first | Card data, images, and the database live on the server. Nothing required from the network to browse or build. |
| Owned-only by default | Search returns cards you own unless you explicitly opt out. |
| Vault as source of truth for prose | Deck notes and journal entries are markdown files you can read without this app. |
| WCAG 2.2 AA floor | Non-negotiable, including the parts only you will ever see. |
| Your data leaves only when you say so | Cloud model calls are an explicit per-action choice, never a default. |

---

## 2. Architecture

```
┌─────────────────────────────────────────────┐
│  M1 Mac Mini (16GB) — existing server        │
│                                              │
│  Docker                                      │
│   ├── grimoire-web    (static Vite build)    │
│   ├── grimoire-api    (Python / FastAPI)     │
│   └── SQLite  ──► local disk, NOT iCloud     │
│                                              │
│  Ollama (existing)  ──► embeddings only      │
│  iCloud Drive       ──► Obsidian vault       │
│  Tailscale Serve    ──► https://…ts.net      │
└─────────────────────────────────────────────┘
              │ tailnet
    ┌─────────┴──────────┬──────────────┐
  MacBook              iPad          Phone
  (browser)          (browser)      (browser)
```

### Stack

- **Frontend:** React + Vite + TypeScript, built to static files
- **Backend:** Python + FastAPI
- **Database:** SQLite, with FTS5 for oracle text search
- **Local inference:** Ollama, already installed
- **Access:** Tailscale Serve

### Why not Electron

An earlier draft assumed an Electron desktop app. Dropped: a browser client over the tailnet reaches every device without packaging, code signing, or a separate mobile path. Updates are a pull and a restart.

---

## 3. Deployment rules

These are specific to this machine and this setup. Each one has a failure mode that is quiet rather than loud.

### Tailscale

- **Use Tailscale Serve, not a bare port.** `http://100.x.x.x:8080` is not a secure context, so `getUserMedia` is blocked and camera scanning silently fails. Serve provides a real certificate on a `ts.net` hostname.
- **Write an ACL scoping this service to your own devices.** The tailnet is shared with friends for Jellyfin. Without a rule, they can reach the collection, its dollar value, and the deck journal.

### iCloud and the vault

The Obsidian vault lives on the Mini and syncs via iCloud. Four rules:

1. **SQLite never goes in the vault or any synced folder.** Sync engines copy files mid-write; the result is a corrupt database. Database on local disk, markdown only in the vault.
2. **Disable Optimize Mac Storage on the Mini.** iCloud evicts cold files and leaves placeholder stubs. The service would read a 0-byte file and overwrite real content.
3. **Write atomically.** Write to a temp file, then `rename()` into place. Rename is atomic, so iCloud never uploads a half-written note.
4. **Debounce writes.** Save on entry submission, not on keystroke. Rapid small writes to a synced folder produce conflict copies.

Also confirm the Mini stays in a logged-in GUI session — iCloud Drive does not sync reliably from an SSH-only login, and the failure is silent staleness rather than an error.

### Disk

The Scryfall bulk file is several hundred MB, plus an image cache. Check free space alongside Jellyfin before starting.

---

## 4. Data model

### The central distinction

Scryfall gives every card two identifiers:

- **`oracle_id`** — the card as a game object. All printings of Llanowar Elves share one.
- **`id`** — a specific printing. Dominaria's Llanowar Elves differs from Magic 2015's.

**Decks reference `oracle_id`. Collection tracks printings. The join between them is the application.** Getting this wrong is the single most expensive mistake available, because it looks fine until it doesn't.

### Multi-face cards

Cards have a `layout` field. When it is `transform`, `modal_dfc`, `split`, `flip`, `adventure`, or `meld`, the text and mana cost live in a `card_faces` array rather than at the top level. A naive importer reading `oracle_text` off the root produces empty strings for every double-faced card, and those cards then vanish from search.

### Schema sketch

```sql
-- One row per printing, from the Scryfall bulk file
CREATE TABLE cards (
  id            TEXT PRIMARY KEY,      -- Scryfall printing id
  oracle_id     TEXT NOT NULL,
  name          TEXT NOT NULL,
  set_code      TEXT,
  collector_num TEXT,
  layout        TEXT,
  mana_value    REAL,
  type_line     TEXT,
  oracle_text   TEXT,                  -- faces joined for search
  color_identity TEXT,                 -- 'WU', 'G', '' for colorless
  rarity        TEXT,
  price_usd     REAL,
  price_usd_foil REAL,
  edhrec_rank   INTEGER,
  image_status  TEXT,
  raw           TEXT NOT NULL          -- full JSON blob, see note
);
CREATE INDEX idx_cards_oracle ON cards(oracle_id);

-- One row per physical copy you own
CREATE TABLE copies (
  id          INTEGER PRIMARY KEY,
  card_id     TEXT NOT NULL REFERENCES cards(id),
  foil        INTEGER NOT NULL DEFAULT 0,
  condition   TEXT,
  language    TEXT DEFAULT 'en',
  location_id INTEGER REFERENCES locations(id),
  lent_to     TEXT,                    -- NULL unless out of your hands
  acquired_at TEXT,
  notes       TEXT
);

CREATE TABLE locations (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL                   -- 'Binder A', 'Bulk box', 'Elfball box'
);

CREATE TABLE decks (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  commander_oracle_id TEXT,
  format       TEXT,
  bracket_claimed INTEGER,
  vault_path   TEXT,
  created_at   TEXT,
  updated_at   TEXT
);

CREATE TABLE deck_cards (
  deck_id     INTEGER NOT NULL REFERENCES decks(id),
  oracle_id   TEXT NOT NULL,
  quantity    INTEGER NOT NULL DEFAULT 1,
  category    TEXT,                    -- user-defined group
  tag         TEXT,                    -- user-defined colour tag
  wishlist    INTEGER NOT NULL DEFAULT 0,
  print_pref  TEXT REFERENCES cards(id),  -- which printing to use
  PRIMARY KEY (deck_id, oracle_id)
);

CREATE TABLE deck_history (
  id        INTEGER PRIMARY KEY,
  deck_id   INTEGER NOT NULL REFERENCES decks(id),
  at        TEXT NOT NULL,
  summary   TEXT,
  snapshot  TEXT                       -- full list as JSON
);

CREATE TABLE journal (
  id        INTEGER PRIMARY KEY,
  deck_id   INTEGER NOT NULL REFERENCES decks(id),
  at        TEXT NOT NULL,             -- ISO 8601
  body      TEXT NOT NULL,
  result    TEXT                       -- 'win' | 'loss' | NULL
);

CREATE TABLE shelf (
  oracle_id TEXT PRIMARY KEY,
  added_at  TEXT,
  note      TEXT
);

CREATE TABLE packages (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
CREATE TABLE package_cards (
  package_id INTEGER REFERENCES packages(id),
  oracle_id  TEXT NOT NULL
);

CREATE TABLE saved_searches (
  id    INTEGER PRIMARY KEY,
  name  TEXT NOT NULL,
  query TEXT NOT NULL
);

-- Oracle text search
CREATE VIRTUAL TABLE cards_fts USING fts5(
  name, type_line, oracle_text, content='cards', content_rowid='rowid'
);

-- Embeddings for owned cards only
CREATE TABLE embeddings (
  oracle_id TEXT PRIMARY KEY,
  vector    BLOB NOT NULL,
  model     TEXT NOT NULL,
  built_at  TEXT
);
```

**On the `raw` column:** store the full JSON blob alongside the extracted columns. You will want fields you did not think to extract, and re-importing 110,000 cards to get them is miserable.

---

## 5. External data

### Scryfall

- **Bulk data**, downloaded daily, is the primary source. Never call the API for anything the bulk file already answers.
- The file is several hundred MB of JSON. **Stream-parse and insert in batches** — a single `json.load()` will exhaust memory.
- **Rate limits** if you do call the API: 2 requests/second for search, named, and collection endpoints; 10/second for others. Send a `User-Agent` naming the app and an `Accept` header.
- **`POST /cards/collection`** takes up to 75 identifiers per request — use it for CSV resolution, not one lookup per row.
- **Rulings** are a separate bulk file, keyed by `oracle_id`.
- **Images:** fetch once per card, cache on disk, never re-fetch.

### Scryfall licence terms

Two constraints that affect design, not just legal hygiene:

- **Images may not be cropped, distorted, recoloured, or watermarked.** Rules out tightening the frame for a denser grid.
- **You may not paywall access to Scryfall data.** Irrelevant for a single-user app, but relevant if this ever ships.

### Commander Spellbook

Combo detection uses the `find-my-combos` endpoint: send the list of card identifiers, receive combos that are complete and combos where you are one piece short. Do not attempt to build combo detection yourself.

### Oracle tags

Scryfall's community function tags (ramp, board wipe, tutor) are API-only, not in the bulk download. Useful for sharpening synergy search, but acquiring them means a rate-limited crawl. Defer.

---

## 6. Modules

### 6.1 Collection

- Search with Scryfall syntax over owned cards. Two additions that are yours alone: `-is:deck` for cards in no deck, and `is:foil`.
- Filter chips, saved searches, collection value.
- Every copy has a location you define. **This is the question you will ask most often** and neither Archidekt nor Scryfall answers it.
- Lent-out state uses the same field: a copy with `lent_to` set is yours but not on hand.
- Condition, language, and printing tracked per copy.

### 6.2 Deck editor

- Categories are user-defined and user-named. The app suggests groupings on import by reading oracle text, then does not touch them again.
- **View modes:** stacks, text, grid, table. **Group by:** category, type, mana value, colour, tag, owned/wishlist. **Sort by:** name, mana value, price, type, date added.
- **In-deck filter accepting Scryfall syntax** — `o:target` shows only cards with "target" in the rules text. High value, low cost.
- Colour tags per card. Tags render as a shape *plus* the tag name in text — colour alone fails WCAG 1.4.1 and is unreadable past four tags anyway.
- Printing picker per slot.
- Panels: buildable-now percentage, mana curve, rules check, combos, value, tokens to bring, bracket check, record, notes.
- **Bracket check** reads the list itself — tutor count, fast mana, two-card combos, Game Changers — and compares to the bracket you claimed.
- **Tokens to bring:** Scryfall knows what each card creates. A packing list for your deck box.
- **History** with restore. Every change writes a `deck_history` row.
- **Export:** plain text, Arena, MTGO `.dek`, CSV, markdown, proxy print sheet.

### 6.3 Shelf

A staging area between thinking and building. Cards parked with a note, then sent to a deck as a batch.

**Synergy results land here, never straight into a deck.** A search result should not quietly modify a list you have already built. Cost: one extra click per accepted suggestion.

Packages live here too — reusable card groups (green ramp suite, mono-green landbase) droppable into any new deck.

### 6.4 Find synergies

Plain-language goal in, ranked owned cards out, each with a stated reason.

Constrained by colour, format, and decks already built. Weak matches are shown with their weakness named rather than hidden — a 54% match labelled "surfaced because of a curve gap, not because it fits the theme" is more useful than silence.

### 6.5 Journal

Timestamped entries per deck, optionally tagged win or loss. Appended to the deck's vault file.

The **overview** is generated from journal entries, not from the card list. Four sections:

- How it feels to play
- When to bring it
- Tricks and lines
- What you keep running into

Every claim must trace to something you wrote. An overview that also analysed the decklist would produce generic Commander advice. The cost is that it says nothing useful until several entries exist.

Model choice is presented at generation time, not buried in settings, with the data-flow consequence stated next to the button.

### 6.6 Import and sync

- CSV import with a column-mapping review screen. Nothing is written until you approve.
- Names resolved in batches of 75 via `/cards/collection`.
- **Camera capture:** point a device at a pile, read names off the art, land results in a review queue before touching the collection. Requires the HTTPS context from Tailscale Serve. Recognition runs on-device.
- **First run:** one choice — import a CSV, or start empty. Card data downloads in the background either way, so search works before the first card is entered.
- **Backup:** nightly SQLite snapshot, vault under git, and an off-machine copy. Local-first means the failure mode is yours too.

### 6.7 Playtester

Deferred. Shares almost nothing with the rest of the app and can be built at any point.

Scope when built: opening hand, mulligan counter, drag between zones, counters, life totals, keyboard shortcuts. **No rules enforcement** — goldfishing only.

---

## 7. AI layer

Two jobs with different requirements. Do not run them on the same model or the same machine.

### Embeddings — on the Mini

Embed the oracle text of owned cards only. Roughly 1,200 vectors, small enough to hold in memory and brute-force at query time. **No vector database, no SQLite extension.** Recompute on import.

An embedding model is a few hundred MB and will not disturb Jellyfin.

Semantic search is what makes "find me cards that do X" work where keyword search cannot — it finds cards that *function* similarly without shared vocabulary.

### Generation — not on the Mini

Journal overviews and match explanations need a chat model. A 7B model competing with a Jellyfin transcode on 16GB will be unpleasant.

Route to: the MacBook when awake, or a cloud provider with your key. The choice is explicit per action.

---

## 8. Vault contract

One markdown file per deck at `Vault/MTG/<Deck-Name>.md`.

```markdown
---
deck: Elfball
commander: Marwyn, the Nurturer
colors: [G]
format: commander
bracket: 3
owned: 96
total: 100
updated: 2026-09-09
---

## What this deck is trying to do
Go wide on elves, convert bodies into mana, dump it into one finisher.

## Overview
<!-- generated, replaced on each run -->

## Journal
### 2026-09-06 21:40 — Win
Marwyn got huge because nobody wanted to spend removal on a three-drop.
```

**Sync is one way.** The app writes frontmatter and the generated sections. Everything else is yours and is never overwritten. Frontmatter fields are named for Dataview queries.

---

## 9. Design system — Mycelium Filament

Inherited from Atrium. This spec records what the mockup uses; reconcile against the Atrium Filament spec if it differs.

```css
--magenta: #a23b84;   --indigo: #3a2b95;   --purple: #6f2fa6;
--bg: #0d0a1e;        --bg-2: #130f2b;

--glass:      rgba(255,255,255,0.045);
--glass-edge: rgba(255,255,255,0.10);
--glass-lit:  rgba(238,176,218,0.34);

--ink: #f5f3fb;  --ink-mid: #c3bde0;  --ink-low: #9d96c4;
--tint-mag: #f0b5dc;  --tint-pur: #cfb1f2;
--icon: #d16bb0;
```

- Typography: Arial throughout.
- Glass tiles: `backdrop-filter: blur(16px) saturate(1.25)`, 1px edge, lit seam along the top.
- Filament webbing behind content at low opacity, with nodes where threads cross.
- Rail icons: outline strokes, 1.6px, **one unified colour** across every item.

**Note on the icon colour:** brand magenta `#a23b84` reaches only ~2.98:1 against the substrate, missing the 3:1 that WCAG 1.4.11 requires for non-text UI components. `#d16bb0` clears 5.4:1 and stays in the same family. If exact brand hex is required, lighten the panel behind the rail instead.

---

## 10. Accessibility requirements

Applies to every screen, including the ones only you will see.

- WCAG 2.2 AA floor. 4.5:1 for text, 3:1 for non-text UI components.
- **Colour never carries meaning alone.** Mana pips include the letter. Ownership state includes a symbol and a word. Colour tags include the tag name.
- Tabs implement the WAI-ARIA pattern with arrow-key navigation, `Home`, and `End`.
- Visible focus indicator on every interactive element, high contrast, never suppressed.
- Card images get alt text built from name and type line.
- Charts (mana curve, bracket meter) carry `role="img"` and a text description of the data.
- `prefers-reduced-motion` disables transitions and animation.
- Skip link to main content.
- Every form control has a label, visible or programmatic.

---

## 11. Decisions made

Recorded so they are not silently re-litigated later.

| Decision | Reasoning |
|---|---|
| Wishlist cards allowed in decks, counted separately | A hard owned-only rule makes purchase planning impossible. Counts stay honest. |
| Type-in search is owned-only unless explicitly opted out | Prevents building a deck you cannot sleeve. Opt-in additions are marked wishlist on entry. |
| Quantity tracked per printing, not per card | Foils, sets, and conditions differ. More import friction, correct data. |
| Search returns cards; deck membership is a filter | The alternative is deck-first navigation, a different information architecture. |
| Categories user-defined, suggested once, then untouched | Rigid categories do not match how you think about a list. |
| Shelf sits between synergy results and decks | A search result should never quietly change a built deck. |
| Vault sync is one-way | Two-way sync is a much harder build and a real data-loss risk. |
| Overview reads the journal, not the decklist | Otherwise it produces generic advice you did not need. |
| Model choice explicit per generation | The journal is the most personal data here. |
| No social features | Not what this is for. |

### Not built

Set completion tracking, price history graphs, deck comparison, EDHREC integration, custom cards, and anything involving other people.

---

## 12. Fixture set

Build this **before** the importer, not after. Six records that break naive implementations:

1. A transform double-faced card — text lives in `card_faces`
2. A split card — two mana costs, one card
3. A card owned in three printings, one foil — tests the oracle_id/printing join
4. A card in a deck that you do not own — tests wishlist
5. A card that creates a token — tests the token checklist
6. A card whose name contains a comma and an accent — tests CSV parsing and name resolution

---

## 13. Build order

1. **Importer and data model**, against the fixture set, with nothing but a table view. Least enjoyable, and everything downstream is worthless if it is wrong.
2. **Collection browsing** — search, filters, locations, saved searches.
3. **Deck editor** — the largest single module.
4. **Journal** — cheap once decks exist, and immediately useful.
5. **Vault sync** — atomic writes, debounced.
6. **Synergy search** — depends on a working collection.
7. **Shelf and packages**.
8. **Camera capture**.
9. **Playtester** — any time; shares nothing.

---

## 14. Supporting files to write

### `CLAUDE.md` at repo root

Carries the decisions in section 11. Skills carry domain knowledge; this carries *your* choices, and it is what stops a fresh session from quietly reversing them.

### Skills

- **Scryfall data** — oracle_id vs printing id, `card_faces` and `layout`, bulk file types, rate limits, licence terms, image caching. Highest value: these are wrong in plausible-looking ways.
- **Mycelium Filament** — tokens, glass treatment, icon rules, contrast floors. Applies across every Atrium module.
- **Obsidian vault writing** — frontmatter conventions, Dataview field naming, the never-overwrite-below-the-fold rule.
- **Magic vocabulary** — bracket definitions, colour identity vs mana cost, singleton, what legality checks.

The existing accessibility red-teaming skill applies unchanged.

---

## 15. Open questions

1. **What produced the existing CSV?** ManaBox, Delver Lens, TCGplayer, and Moxfield exports have different column shapes. This determines whether the importer is an afternoon or a weekend.
2. **Standalone, or an Atrium module from day one?** Grimoire needs the same vault writer, the same Ollama connection, and the same design system as Atrium. It is also the least critical app, which makes it either a low-risk place to prove the module pattern or a distraction from Atrium's real modules.
3. **Bracket definitions** — verify the five-tier labels against current official wording before building the bracket check.
4. **Should the type-in path be owned-only with no exception**, leaving wishlist additions to the paste flow only?
