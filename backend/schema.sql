-- Grimoire schema. See grimoire-build-spec.md section 4.
--
-- The load-bearing distinction: decks reference oracle_id (the card as a game
-- object), the collection tracks id (a specific printing). The join between
-- them is the application's job, and getting it wrong looks fine until it
-- doesn't.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One row per printing, from the Scryfall bulk file.
CREATE TABLE IF NOT EXISTS cards (
  id             TEXT PRIMARY KEY,     -- Scryfall printing id
  oracle_id      TEXT NOT NULL,
  name           TEXT NOT NULL,        -- full name, "A // B" for multi-face
  set_code       TEXT,
  set_name       TEXT,
  collector_num  TEXT,
  layout         TEXT,
  mana_cost      TEXT,                 -- faces joined where root has none
  mana_value     REAL,
  type_line      TEXT,
  oracle_text    TEXT,                 -- faces joined for search
  power          TEXT,                 -- text, not numeric: '*', '1+*', '∞' exist
  toughness      TEXT,
  loyalty        TEXT,
  color_identity TEXT,                 -- 'WU', 'G', '' for colorless
  colors         TEXT,
  rarity         TEXT,
  price_usd      REAL,
  price_usd_foil REAL,
  price_usd_etched REAL,             -- etched foils have ONLY this price;
                                     -- usd and usd_foil are both null
  edhrec_rank    INTEGER,
  image_status   TEXT,
  image_normal   TEXT,                 -- front face; see faces table for backs
  image_small    TEXT,
  -- Scryfall's OWN crop. The licence forbids us cropping their art; using a
  -- variant they publish is a different thing and is fine.
  image_art_crop TEXT,
  released_at    TEXT,
  digital        INTEGER NOT NULL DEFAULT 0,
  raw            TEXT NOT NULL         -- full JSON blob; you will want a field
                                       -- you did not think to extract, and
                                       -- re-importing 110k cards is miserable
);
CREATE INDEX IF NOT EXISTS idx_cards_oracle ON cards(oracle_id);
CREATE INDEX IF NOT EXISTS idx_cards_name   ON cards(name);
CREATE INDEX IF NOT EXISTS idx_cards_set    ON cards(set_code, collector_num);

-- Multi-face cards keep text, cost, and art per face. transform and modal_dfc
-- carry NOTHING at the root; split carries mana_cost and image_uris but no
-- oracle_text. Normalising it here means no downstream code re-learns that.
CREATE TABLE IF NOT EXISTS card_faces (
  card_id      TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  face_index   INTEGER NOT NULL,
  name         TEXT NOT NULL,
  mana_cost    TEXT,
  type_line    TEXT,
  oracle_text  TEXT,
  image_normal TEXT,
  PRIMARY KEY (card_id, face_index)
);

-- Tokens and meld parts a card points at, for the "tokens to bring" checklist.
CREATE TABLE IF NOT EXISTS card_parts (
  card_id     TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  component   TEXT NOT NULL,           -- 'token' | 'meld_part' | 'combo_piece' | ...
  part_id     TEXT NOT NULL,           -- Scryfall id of the related card
  name        TEXT NOT NULL,
  type_line   TEXT,
  PRIMARY KEY (card_id, part_id, component)
);
CREATE INDEX IF NOT EXISTS idx_parts_component ON card_parts(component);

-- One row per physical copy you own.
CREATE TABLE IF NOT EXISTS copies (
  id          INTEGER PRIMARY KEY,
  card_id     TEXT NOT NULL REFERENCES cards(id),
  -- 'normal' | 'foil' | 'etched'. Etched is a real third finish with its own
  -- price, not a kind of foil, so a boolean loses information.
  finish      TEXT NOT NULL DEFAULT 'normal',
  -- LEGACY, derived from finish. Kept so old rows still read, but nothing
  -- should query it: two columns for one fact drift, and when they did, a
  -- foil copy was valued at the non-foil price.
  foil        INTEGER NOT NULL DEFAULT 0,
  condition   TEXT,
  language    TEXT DEFAULT 'en',
  location_id INTEGER REFERENCES locations(id),
  lent_to     TEXT,                    -- NULL unless out of your hands
  acquired_at TEXT,
  purchase_price REAL,               -- what you paid, not what it is worth
  notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_copies_card     ON copies(card_id);
CREATE INDEX IF NOT EXISTS idx_copies_location ON copies(location_id);

CREATE TABLE IF NOT EXISTS locations (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE            -- 'Binder A', 'Bulk box', 'Elfball box'
);

CREATE TABLE IF NOT EXISTS decks (
  id                  INTEGER PRIMARY KEY,
  name                TEXT NOT NULL,
  commander_oracle_id TEXT,
  format              TEXT,
  bracket_claimed     INTEGER,
  vault_path          TEXT,
  created_at          TEXT,
  updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS deck_cards (
  deck_id     INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  oracle_id   TEXT NOT NULL,
  quantity    INTEGER NOT NULL DEFAULT 1,
  category    TEXT,                    -- user-defined group
  tag         TEXT,                    -- user-defined colour tag
  wishlist    INTEGER NOT NULL DEFAULT 0,
  print_pref  TEXT REFERENCES cards(id),
  PRIMARY KEY (deck_id, oracle_id)
);

CREATE TABLE IF NOT EXISTS deck_history (
  id       INTEGER PRIMARY KEY,
  deck_id  INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  at       TEXT NOT NULL,
  summary  TEXT,
  snapshot TEXT                        -- full list as JSON
);
CREATE INDEX IF NOT EXISTS idx_history_deck ON deck_history(deck_id, at);

CREATE TABLE IF NOT EXISTS journal (
  id      INTEGER PRIMARY KEY,
  deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  at      TEXT NOT NULL,               -- ISO 8601
  body    TEXT NOT NULL,
  result  TEXT                         -- 'win' | 'loss' | NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_deck ON journal(deck_id, at);

CREATE TABLE IF NOT EXISTS shelf (
  oracle_id TEXT PRIMARY KEY,
  added_at  TEXT,
  note      TEXT
);

CREATE TABLE IF NOT EXISTS packages (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS package_cards (
  package_id INTEGER NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
  oracle_id  TEXT NOT NULL,
  PRIMARY KEY (package_id, oracle_id)
);

CREATE TABLE IF NOT EXISTS saved_searches (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  query      TEXT NOT NULL,
  created_at TEXT
);

-- Embeddings for owned cards only. ~1,200 vectors brute-forced in memory;
-- no vector database, no SQLite extension.
CREATE TABLE IF NOT EXISTS embeddings (
  oracle_id TEXT PRIMARY KEY,
  vector    BLOB NOT NULL,
  model     TEXT NOT NULL,
  built_at  TEXT
);

-- Bookkeeping for the bulk importer.
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- Oracle text search. Populated explicitly by the importer rather than by
-- triggers: a 110k-row bulk load with FTS triggers live is dramatically slower.
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
  name, type_line, oracle_text,
  content='cards', content_rowid='rowid'
);
