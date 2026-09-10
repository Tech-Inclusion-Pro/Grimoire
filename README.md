<p align="center">
  <img src="docs/icon.png" alt="" width="140" height="140">
</p>

<h1 align="center">Grimoire</h1>

<p align="center">
  A personal Magic: The Gathering library, deck builder, and play journal that
  runs on your own machine.
</p>

---

Grimoire only ever searches cards you physically own, keeps deck notes as
markdown in your own Obsidian vault rather than in someone else's database, and
uses a local model to find cards for a plan you describe in plain language.

It is **not** a social deckbuilding site. No sharing, no public decks, no
following, no comments.

---

## What it does

**Collection** — Import your collection once, then search it with Scryfall-style
syntax, or use the **advanced search** form if you would rather not remember it.
Every card shows as a card, with a Cards/Table toggle and a multi-select for
what to show underneath.

```
t:creature c:g mv<=3 -is:deck        cheap green creatures in no deck
loc:"binder a" is:foil               foils in a specific binder
value>=40                            cards you hold that are worth $40+
!"Llanowar Elves"                    that card exactly
(r:rare or r:mythic) t:artifact      grouping with or
```

The advanced form covers name, rules text, type, colours (including, exactly or
at most), commander colour identity, mana value, power, toughness, price, what
your own copy is worth, release year, rarity, set, where a card is filed, and
thirteen conditions that can each be required or excluded. It writes the query
into the search box rather than searching behind your back, so the syntax stays
visible and you pick it up by using it.

**Deck builder** — Build from cards you own. Add one at a time or paste a whole
list. Group by category, type, mana value, colour or tag; filter the deck with
the same search syntax. Panels show buildable-now percentage, mana curve, value,
tokens to bring, and **cards promised to another deck** — without that last one,
two decks each report 100% buildable while sharing one physical Sol Ring.

Wishlist cards are allowed in a deck and counted separately, so the buildable
figure stays honest while you plan purchases.

**Play journal** — Timestamped entries per deck, optionally tagged win or loss,
written into your Obsidian vault as markdown. A generated overview reads *your
journal entries only* — never the decklist, which would just produce generic
Commander advice.

**Find synergies** — Describe what you want the deck to do in plain language and
get back cards you already own, ranked, each with a stated reason. Weak matches
name their weakness rather than being hidden.

**Shelf** — Synergy results land here, never straight into a deck: a search
result should not quietly change a list you have already built. Park cards with
a note, then send them to a deck as a batch.

**Playtester** — Goldfishing with no rules enforcement. Battlefield, hand and
the library/graveyard/exile/command piles all sit on one playmat, so a card is
never more than a short drag from where it needs to go. Free-form battlefield,
click to tap, opening hand, mulligans, counters, life totals and keyboard
shortcuts.

Everything works in a phone or tablet browser over your tailnet — no app, no
install.

---

## How it works

```
┌──────────────────────────────────────────────┐
│  Your machine                                │
│                                              │
│   FastAPI  ─────►  SQLite   (local disk)     │
│      │             card data + collection    │
│      │                                       │
│      ├──────────►  Ollama   (embeddings,     │
│      │              overviews — all local)   │
│      │                                       │
│      └──────────►  Obsidian vault (markdown) │
│                                              │
│   Tailscale Serve  ──►  https://…ts.net      │
└──────────────────────────────────────────────┘
              │ your tailnet
     ┌────────┴────────┬──────────┐
   Laptop            Tablet     Phone
```

- **Backend** — Python 3.12, FastAPI, SQLite with FTS5.
- **Frontend** — React + Vite + TypeScript, built into `backend/static/`.
- **Card data** — Scryfall's daily bulk file, ~108,000 printings, imported in
  about 25 seconds. Card images are fetched once and cached on disk, so
  browsing needs no network after the first look.
- **AI** — Ollama, on your machine. Nothing is sent anywhere.

### The distinction everything depends on

Scryfall gives every card two identifiers. `oracle_id` is the card as a game
object — all printings of Llanowar Elves share one. `id` is a specific printing.

**Decks reference `oracle_id`. Your collection tracks printings.** So a card you
own in three printings is one deck slot with three copies available, and search
returns one row rather than three. Getting this wrong looks fine until it
doesn't.

---

## Requirements

- macOS or Linux (the LaunchAgent installer is macOS; the app itself is portable)
- Python 3.12+ and [uv](https://github.com/astral-sh/uv)
- Node 20+ (to build the frontend)
- ~2 GB disk for card data, plus image cache as you browse
- Optional: [Ollama](https://ollama.com) for synergy search and journal overviews
- Optional: [Tailscale](https://tailscale.com) to reach it from other devices

---

## Install

```sh
git clone https://github.com/Tech-Inclusion-Pro/Grimoire.git
cd Grimoire

uv sync --extra dev
(cd frontend && npm install && npm run build)

# ~78 MB download, ~25s import
uv run python scripts/import_bulk.py

uv run uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

Open <http://127.0.0.1:8787>.

### Run it as a service and publish it on your tailnet

```sh
./deploy/install.sh
```

That installs a LaunchAgent, builds the frontend, and runs
`tailscale serve` so the app is reachable at `https://<machine>.<tailnet>.ts.net:8444`
from any of your devices.

**Use Tailscale Serve, not a bare port.** `http://100.x.x.x:8787` is not a
secure context, so camera access is blocked and any future scanning feature
fails silently. Serve gives you a real certificate.

---

## Set a password

Until you do, anyone who can reach the page can read everything.

```sh
uv run python -m backend.auth set-password 'your password' --days 30
uv run python -m backend.auth status
```

PBKDF2-SHA256 hash and an HMAC-signed session cookie, stdlib only. Config lives
in `config/auth.json` (mode 0600, never committed). Omit `--days` for a cookie
that dies when the browser closes.

If your tailnet is shared with anyone — for a media server, say — **also scope
this service to your own devices**. Tailscale Serve is reachable by everyone on
the tailnet; the login protects the data, the ACL stops them reaching the page.
See [`deploy/acl-snippet.hujson`](deploy/acl-snippet.hujson).

---

## Import your collection

Export a CSV from **ManaBox**, **Moxfield**, **TCGplayer** or **Delver Lens**,
then:

```sh
uv run python scripts/import_csv.py "export.csv"            # dry run
uv run python scripts/import_csv.py "export.csv" --apply    # write it
uv run python scripts/import_csv.py "export.csv" --apply --location "Binder A"
```

The dry run shows the detected format, the column mapping, and exactly what
would be written. **Nothing is written until `--apply`.**

Rows resolve by Scryfall printing id first, then set code plus collector number,
then name. Exports do contain printing ids you cannot own — ManaBox records
MTGO-only printings for cards held in paper — so those fall through to the later
rules and are **reported as substitutions** rather than silently dropped or
silently accepted.

Etched foil is treated as a third finish, not a kind of foil, because Scryfall
prices it separately and etched cards carry *only* a `usd_etched` price.

After importing, rebuild the semantic index so the new cards are searchable:

```sh
uv run python -c "from backend import db, embeddings; print(embeddings.build(db.open_db()))"
```

---

## Connect a local model

Grimoire uses [Ollama](https://ollama.com) for two jobs. Both are optional, and
neither sends anything off your machine.

```sh
ollama pull nomic-embed-text   # semantic search over your collection
ollama pull qwen2.5:3b         # journal overviews
```

**Embeddings** power *Find synergies*. One 768-dimension vector per owned card,
held in memory and brute-forced at query time — no vector database, no SQLite
extension. Building the index takes about two minutes for 8,000 cards; after
that, queries run in a few hundred milliseconds.

Ranking blends semantic similarity with what the card demonstrably *does*, read
off its rules text. Similarity alone ranked an artifact creature above every
removal spell for "I need cheap removal", because short goals embed vaguely.

**Overviews** summarise your play journal. The model choice is presented at
generation time with the data-flow consequence next to the button, never buried
in settings — the journal is the most personal thing in the app.

Point Grimoire at a different Ollama with `OLLAMA_URL`. A 7B model on a
16 GB machine that is also transcoding video will be slow; try a 3B first.

---

## Your Obsidian vault

Set `GRIMOIRE_VAULT` to your vault, and Grimoire writes one note per deck at
`MTG/Decks/<Deck Name>.md`.

**Sync is one way.** Grimoire owns the frontmatter keys it writes, the
`## Overview` section, and the entry block between its markers inside
`## Journal`. Everything else is yours and is never touched — hand-written
sections, extra frontmatter keys, tags you added, and notes typed inside the
journal all survive a resync.

Three safeguards, because every one of these failure modes is silent:

- Writes go to a temp file and are renamed into place, so a sync engine never
  uploads a half-written note.
- Entries save on submit, never on keystroke — rapid small writes into a synced
  folder produce conflict copies.
- If iCloud has evicted a note (Optimize Mac Storage leaves a hidden
  `.icloud` placeholder), Grimoire **refuses to write** rather than overwriting
  real content with a placeholder's emptiness.

The database never goes in the vault. Sync engines copy files mid-write, and the
result is a corrupt database.

---

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `GRIMOIRE_DB` | `data/grimoire.db` | SQLite file. Never put this in a synced folder. |
| `GRIMOIRE_VAULT` | `./vault` | Obsidian vault for deck notes |
| `GRIMOIRE_AUTH_CONFIG` | `config/auth.json` | Password hash and cookie secret |
| `GRIMOIRE_IMAGE_CACHE` | `data/images` | Cached card art |
| `GRIMOIRE_CONTACT` | repo URL | Sent to Scryfall in the User-Agent |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Where to find Ollama |

---

## Accessibility

WCAG 2.2 AA is a floor, not an aspiration — including on screens only one person
will ever see.

- Colour never carries meaning alone: mana pips include the letter, ownership
  includes a symbol and a word, colour tags include the tag name.
- Tabs implement the WAI-ARIA pattern with arrow keys, Home and End.
- Charts carry `role="img"` with the data described in words, and repeat the
  same numbers as text.
- Card images get alt text built from name and type line.
- Dragging is never the only way to do anything (WCAG 2.5.7): zone changes are
  buttons and arrow keys nudge the selection.
- Tap targets are at least 24px. Verified by measurement at 360px, 834px and
  1194px: no horizontal scrolling on any view.
- `prefers-reduced-motion` disables transitions.

---

## Data sources and licence

Card data and images come from [Scryfall](https://scryfall.com). Their images
**may not be cropped, distorted, recoloured or watermarked**, which is why the
card grid gives every card its full frame rather than tightening it for density.
`art_crop` is Scryfall's own published variant, which is a different thing.

Magic: The Gathering is a trademark of Wizards of the Coast. This project is
unaffiliated with, and unendorsed by, Wizards of the Coast or Scryfall.

Grimoire itself is licensed under [Apache 2.0](LICENSE).

---

## Development

See [docs/DEVELOPING.md](docs/DEVELOPING.md) for the build order, module layout
and test suite, and [CLAUDE.md](CLAUDE.md) for the decisions behind the design
and the list of Scryfall behaviours that are wrong in plausible-looking ways.

```sh
uv run pytest                    # 221 tests
cd frontend && npm run dev       # Vite dev server on :5174
```
