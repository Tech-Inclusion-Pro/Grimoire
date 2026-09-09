import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type BulkResult, type DeckCard, type DeckDetail, type DeckSummary } from '../api'
import DeckPanels from './DeckPanels'
import Journal from './Journal'
import { navigate, useRoute } from '../router'
import { CardArt, Pips } from './CardArt'
import ViewControls, { CardMeta, useViewPrefs } from './ViewControls'
import AddCard from './AddCard'
import CardDetail from './CardDetail'
import Playtest from './Playtest'

export default function Decks() {
  const route = useRoute()
  const toLibrary = () => navigate({ name: 'decks' })
  const toDeck = (id: number) => navigate({ name: 'deck', id })

  if (route.name === 'newDeck')
    return <NewDeck onCreated={toDeck} onCancel={toLibrary} />
  if (route.name === 'playtest') return <Playtest id={route.id} />
  if (route.name === 'deck')
    return <Editor id={route.id} onBack={toLibrary} />
  return <Library onOpen={toDeck} onNew={() => navigate({ name: 'newDeck' })} />
}

// --------------------------------------------------------------------------- //

function Library({ onOpen, onNew }: { onOpen: (id: number) => void; onNew: () => void }) {
  const [decks, setDecks] = useState<DeckSummary[] | null>(null)
  const load = useCallback(() => { void api.decks().then(setDecks) }, [])
  useEffect(load, [load])

  return (
    <>
      <div className="spread">
        <div>
          <h1>Decks</h1>
          <p className="lede">
            Built from cards you own. Wishlist cards are allowed and counted separately,
            so the buildable figure stays honest.
          </p>
        </div>
        <button className="btn btn-primary" onClick={onNew}>New deck</button>
      </div>

      {decks === null && <p className="muted">Loading…</p>}
      {decks?.length === 0 && (
        <div className="tile">
          <h2>No decks yet</h2>
          <p className="lede">Paste a decklist or build one card by card.</p>
          <button className="btn btn-primary" onClick={onNew}>New deck</button>
        </div>
      )}

      {decks && decks.length > 0 && (
        <div className="library">
          {decks.map(d => (
            <div key={d.id} style={{ position: 'relative' }}>
              <button className="deckcard" style={{ width: '100%' }}
                      onClick={() => onOpen(d.id)}>
                <div className="banner">
                  {d.banner_card_id
                    ? <CardArt cardId={d.banner_card_id} name={d.commander ?? d.name}
                               variant="art_crop" />
                    : <div className="banner-empty" />}
                </div>
                <div className="body">
                  <p className="decktitle">{d.name}</p>
                  <p className="commander">
                    {d.commander ?? <span className="muted">No commander set</span>}
                  </p>
                  <p className="deckmeta">
                    <Pips identity={d.colors} />
                    {d.format && <span className="badge">{d.format}</span>}
                    {d.bracket_claimed && <span className="badge">Bracket {d.bracket_claimed}</span>}
                    <span className="badge">{d.cards} cards</span>
                    {d.wishlist > 0 && <span className="pill pill-warn">! {d.wishlist} to buy</span>}
                  </p>
                </div>
              </button>
              <button className="btn btn-sm btn-quiet"
                style={{ position: 'absolute', top: 8, right: 8,
                         background: 'rgba(9,6,20,.8)' }}
                onClick={async () => {
                  if (!confirm(`Delete "${d.name}"? This cannot be undone.`)) return
                  await api.deleteDeck(d.id)
                  load()
                }}>Delete<span className="vh"> {d.name}</span></button>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

// --------------------------------------------------------------------------- //

function NewDeck({ onCreated, onCancel }: { onCreated: (id: number) => void; onCancel: () => void }) {
  const [name, setName] = useState('')
  const [format, setFormat] = useState('commander')
  const [bracket, setBracket] = useState('')
  const [busy, setBusy] = useState(false)

  return (
    <>
      <button className="btn btn-quiet" style={{ marginBottom: 16 }} onClick={onCancel}>
        ← Back to decks
      </button>
      <h1>New deck</h1>
      <p className="lede">Name it now; paste the list once it exists.</p>
      <div className="tile" style={{ maxWidth: 520 }}>
        <form onSubmit={async e => {
          e.preventDefault()
          setBusy(true)
          const created = await api.createDeck({
            name, format, bracket_claimed: bracket ? Number(bracket) : null,
          })
          onCreated(created.id)
        }}>
          <div style={{ marginBottom: 14 }}>
            <label htmlFor="deckname">Deck name</label>
            <input id="deckname" required value={name} style={{ width: '100%' }}
                   onChange={e => setName(e.target.value)} placeholder="Elfball" />
          </div>
          <div className="row" style={{ marginBottom: 18 }}>
            <div>
              <label htmlFor="format">Format</label>
              <select id="format" value={format} onChange={e => setFormat(e.target.value)}>
                <option value="commander">Commander</option>
                <option value="modern">Modern</option>
                <option value="pioneer">Pioneer</option>
                <option value="legacy">Legacy</option>
                <option value="pauper">Pauper</option>
                <option value="casual">Casual</option>
              </select>
            </div>
            <div>
              <label htmlFor="bracket">Bracket claimed</label>
              <select id="bracket" value={bracket} onChange={e => setBracket(e.target.value)}>
                <option value="">Not stated</option>
                {[1, 2, 3, 4, 5].map(b => <option key={b} value={b}>{b}</option>)}
              </select>
            </div>
          </div>
          <button className="btn btn-primary" disabled={busy || !name.trim()}>Create deck</button>
        </form>
      </div>
    </>
  )
}

// --------------------------------------------------------------------------- //

type GroupBy = 'category' | 'type' | 'mv' | 'color' | 'tag' | 'owned'
type SortBy = 'name' | 'mv' | 'price' | 'type'

const EXPORTS = [
  ['text', 'Plain text'], ['arena', 'MTG Arena'],
  ['csv', 'CSV'], ['markdown', 'Markdown'],
] as const

function Editor({ id, onBack }: { id: number; onBack: () => void }) {
  const [detail, setDetail] = useState<DeckDetail | null>(null)
  const [filter, setFilter] = useState('')
  const [filterError, setFilterError] = useState('')
  const [groupBy, setGroupBy] = useState<GroupBy>('category')
  const [sortBy, setSortBy] = useState<SortBy>('name')
  const [view, setView] = useViewPrefs('deck')
  const [zoom, setZoom] = useState<{ o: string; c: string | null } | null>(null)
  const [paste, setPaste] = useState('')
  const [preview, setPreview] = useState<BulkResult | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (q: string) => {
    try {
      setDetail(await api.deck(id, q))
      setFilterError('')
    } catch (e) {
      if (e instanceof ApiError && e.isUserFixable) setFilterError(e.message)
      else throw e
    }
  }, [id])

  useEffect(() => { void load(filter) }, [load, filter])

  if (!detail) return <p className="muted">Loading…</p>
  const { deck, cards, stats } = detail

  const groups = groupCards(cards, groupBy, sortBy)

  async function bulk(apply: boolean) {
    setBusy(true)
    try {
      const result = await api.bulkAdd(id, paste, apply)
      setPreview(result)
      if (apply) {
        setPaste('')
        await load(filter)
        if (result.unresolved.length === 0) setPreview(null)
      }
    } finally { setBusy(false) }
  }

  return (
    <>
      <button className="btn btn-quiet" style={{ marginBottom: 16 }} onClick={onBack}>
        ← Back to decks
      </button>

      <div className="spread">
        <div>
          <h1>{deck.name}</h1>
          <p style={{ margin: '0 0 18px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {deck.commander && <span className="badge">{deck.commander}</span>}
            {deck.format && <span className="badge">{deck.format}</span>}
            {deck.bracket_claimed && <span className="badge">Bracket {deck.bracket_claimed}</span>}
            <span className="badge">{stats.cards} cards</span>
            <span className={stats.missing ? 'pill pill-warn' : 'badge badge-ok'}>
              {stats.missing ? `! ${stats.missing} to buy` : '✓ Fully owned'}
            </span>
          </p>
        </div>
        <button className="btn btn-primary" disabled={stats.cards === 0}
                onClick={() => navigate({ name: 'playtest', id })}>
          Playtest
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 320px', gap: 18 }}
           className="editorgrid">
        <div>
          <div className="tile">
            <div className="row" style={{ marginBottom: 14 }}>
              <div>
                <label htmlFor="groupby">Group by</label>
                <select id="groupby" value={groupBy}
                        onChange={e => setGroupBy(e.target.value as GroupBy)}>
                  <option value="category">My categories</option>
                  <option value="type">Card type</option>
                  <option value="mv">Mana value</option>
                  <option value="color">Colour</option>
                  <option value="tag">Colour tag</option>
                  <option value="owned">Owned or wishlist</option>
                </select>
              </div>
              <div>
                <label htmlFor="sortby">Sort by</label>
                <select id="sortby" value={sortBy}
                        onChange={e => setSortBy(e.target.value as SortBy)}>
                  <option value="name">Name</option>
                  <option value="mv">Mana value</option>
                  <option value="price">Price</option>
                  <option value="type">Type</option>
                </select>
              </div>
              <div style={{ flex: '1 1 200px', minWidth: 0 }}>
                <label htmlFor="deckfilter">Filter this deck</label>
                <input id="deckfilter" type="search" value={filter} style={{ width: '100%' }}
                       placeholder="o:target, t:land, mv<=2"
                       autoCapitalize="none" spellCheck={false}
                       onChange={e => setFilter(e.target.value)} />
              </div>
            </div>
            {filterError && <p className="err" role="alert">{filterError}</p>}

            <div style={{ margin: '4px 0 14px' }}>
              <ViewControls prefs={view} onChange={setView} idPrefix="deck" />
            </div>

            <div className="row" style={{ marginBottom: 14 }}>
              <div style={{ flex: '1 1 260px' }}>
                <label htmlFor="commander">Commander</label>
                <select id="commander" style={{ width: '100%' }}
                        value={deck.commander_oracle_id ?? ''}
                        onChange={async e => {
                          await api.editDeck(id, e.target.value
                            ? { commander_oracle_id: e.target.value }
                            : { clear_commander: true })
                          await load(filter)
                        }}>
                  <option value="">None chosen</option>
                  {cards.filter(c => c.type_line?.includes('Legendary')
                                  && c.type_line?.includes('Creature'))
                        .map(c => <option key={c.oracle_id} value={c.oracle_id}>{c.name}</option>)}
                </select>
              </div>
              <button className="btn" onClick={async () => {
                const next = prompt('Rename this deck', deck.name)
                if (next && next !== deck.name) { await api.editDeck(id, { name: next }); await load(filter) }
              }}>Rename</button>
            </div>

            <div className="row" style={{ marginBottom: 4 }}>
              <span className="muted">Export:</span>
              {EXPORTS.map(([fmt, label]) => (
                <a key={fmt} className="btn btn-sm" href={api.exportUrl(id, fmt)}
                   target="_blank" rel="noreferrer">{label}</a>
              ))}
            </div>
          </div>

          {groups.length === 0 && (
            <div className="tile">
              <p className="muted">
                {filter ? 'No cards in this deck match that filter.' : 'This deck is empty.'}
              </p>
            </div>
          )}

          {groups.map(([label, list]) => (
            <div className="tile" key={label}>
              <div className="spread" style={{ marginBottom: 8 }}>
                <h3 style={{ margin: 0 }}>{label}</h3>
                <span className="muted">
                  {list.reduce((n, c) => n + c.quantity, 0)} cards
                </span>
              </div>
              {view.mode === 'cards' && (
                <div className="cardgrid"
                     style={{ ['--card-w' as string]: `${view.size}px` }}>
                  {list.map(c => (
                    <div className="cardtile" key={c.oracle_id}>
                      <button className="open"
                              onClick={() => setZoom({ o: c.oracle_id, c: c.card_id })}>
                        <span className="imgwrap">
                          <CardArt cardId={c.card_id} name={c.name} typeLine={c.type_line}
                                   variant={view.size < 165 ? 'small' : 'normal'} />
                          <span className="qty">{c.quantity}</span>
                        </span>
                        <span className="vh">Open {c.name}</span>
                      </button>
                      <div className="corner">
                        {c.owned >= c.quantity
                          ? <span className="pill pill-own">✓ {c.owned}</span>
                          : <span className="pill pill-warn">! need {c.quantity - c.owned}</span>}
                      </div>
                      <CardMeta fields={view.fields} card={c} />
                      <div className="steppers">
                        <button className="btn btn-sm" aria-label={`Add one ${c.name}`}
                          onClick={async () => {
                            await api.setCard(id, { oracle_id: c.oracle_id, quantity: c.quantity + 1 })
                            await load(filter)
                          }}>+</button>
                        <button className="btn btn-sm" aria-label={`Remove one ${c.name}`}
                          onClick={async () => {
                            await api.setCard(id, { oracle_id: c.oracle_id, quantity: c.quantity - 1 })
                            await load(filter)
                          }}>−</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {view.mode === 'table' && (
              <div className="scroll">
                <table>
                  <caption className="vh">{label}</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="num">Qty</th>
                      <th scope="col">Name</th>
                      <th scope="col">Cost</th>
                      <th scope="col">Type</th>
                      <th scope="col">Held</th>
                      <th scope="col" className="num">Price</th>
                      <th scope="col"><span className="vh">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map(c => (
                      <tr key={c.oracle_id}>
                        <td className="num">{c.quantity}</td>
                        <td className="name">
                          <button className="btn-link"
                                  onClick={() => setZoom({ o: c.oracle_id, c: c.card_id })}>
                            {c.name}
                          </button>
                        </td>
                        <td>{c.mana_cost ?? '—'}</td>
                        <td className="muted">{c.type_line}</td>
                        <td>
                          {c.owned >= c.quantity
                            ? <span className="pill pill-own">✓ Own {c.owned}</span>
                            : <span className="pill pill-warn">! Need {c.quantity - c.owned}</span>}
                        </td>
                        <td className="num">{c.price_usd == null ? '—' : `$${c.price_usd.toFixed(2)}`}</td>
                        <td>
                          <button className="btn btn-sm" onClick={async () => {
                            await api.setCard(id, { oracle_id: c.oracle_id, quantity: c.quantity + 1 })
                            await load(filter)
                          }} aria-label={`Add one ${c.name}`}>+</button>{' '}
                          <button className="btn btn-sm" onClick={async () => {
                            await api.setCard(id, { oracle_id: c.oracle_id, quantity: c.quantity - 1 })
                            await load(filter)
                          }} aria-label={`Remove one ${c.name}`}>−</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              )}
            </div>
          ))}

          <AddCard deckId={id}
                   inDeck={new Map(cards.map(c => [c.oracle_id, c.quantity]))}
                   onAdded={() => load(filter)} />

          <div className="tile">
            <h3>Paste a decklist</h3>
            <p className="muted" style={{ marginBottom: 10 }}>
              One card per line, with or without a quantity. Nothing is added until
              you have seen what matched.
            </p>
            <label className="vh" htmlFor="paste">Decklist to add</label>
            <textarea id="paste" rows={5} value={paste} placeholder={'1 Sol Ring\n1 Command Tower'}
                      onChange={e => setPaste(e.target.value)} />
            <div className="row" style={{ marginTop: 12 }}>
              <button className="btn" disabled={!paste.trim() || busy}
                      onClick={() => bulk(false)}>Check the list</button>
              <button className="btn btn-primary" disabled={!preview?.resolved.length || busy}
                      onClick={() => bulk(true)}>
                Add {preview?.resolved.length ?? 0} card{preview?.resolved.length === 1 ? '' : 's'}
              </button>
            </div>

            {preview && (
              <div style={{ marginTop: 16 }} role="status">
                <p className="muted">
                  {preview.added != null
                    ? `Added ${preview.added} cards.`
                    : `${preview.resolved.length} matched, ${preview.unresolved.length} did not.`}
                </p>
                {preview.unresolved.length > 0 && (
                  <>
                    <p className="subhead" style={{ marginTop: 12 }}>Not recognised</p>
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {preview.unresolved.map((u, i) => (
                        <li key={i} className="err" style={{ fontSize: 13 }}>{u.name}</li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        <DeckPanels detail={detail} onRestored={() => load(filter)} />
      </div>

      <div style={{ marginTop: 8 }}>
        <Journal deckId={id} deckName={deck.name} />
      </div>

      {zoom && (
        <CardDetail oracleId={zoom.o} cardId={zoom.c} onClose={() => setZoom(null)} />
      )}
    </>
  )
}

// --------------------------------------------------------------------------- //

const TYPE_ORDER = ['Creature', 'Instant', 'Sorcery', 'Artifact', 'Enchantment',
                    'Planeswalker', 'Battle', 'Land', 'Other']

function primaryType(typeLine: string | null): string {
  for (const t of TYPE_ORDER) if (typeLine?.includes(t)) return t
  return 'Other'
}

const COLOR_NAMES: Record<string, string> = {
  W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', '': 'Colourless',
}

function groupCards(cards: DeckCard[], by: GroupBy, sort: SortBy): [string, DeckCard[]][] {
  const buckets = new Map<string, DeckCard[]>()
  for (const c of cards) {
    let key: string
    switch (by) {
      case 'category': key = c.category ?? primaryType(c.type_line); break
      case 'type': key = primaryType(c.type_line); break
      case 'mv': key = (c.mana_value ?? 0) >= 7 ? '7+' : String(c.mana_value ?? 0); break
      case 'color': key = c.color_identity === ''
        ? 'Colourless'
        : [...c.color_identity].map(ch => COLOR_NAMES[ch] ?? ch).join('/'); break
      case 'tag': key = c.tag ?? 'No tag'; break
      case 'owned': key = c.owned >= c.quantity ? 'Owned' : 'To buy'; break
    }
    const list = buckets.get(key)
    if (list) list.push(c)
    else buckets.set(key, [c])
  }

  const compare = (a: DeckCard, b: DeckCard) => {
    switch (sort) {
      case 'mv': return (a.mana_value ?? 0) - (b.mana_value ?? 0) || a.name.localeCompare(b.name)
      case 'price': return (b.price_usd ?? 0) - (a.price_usd ?? 0) || a.name.localeCompare(b.name)
      case 'type': return primaryType(a.type_line).localeCompare(primaryType(b.type_line))
                          || a.name.localeCompare(b.name)
      default: return a.name.localeCompare(b.name)
    }
  }

  return [...buckets.entries()]
    .map(([k, v]) => [k, v.sort(compare)] as [string, DeckCard[]])
    .sort((a, b) => {
      if (by === 'mv') return Number(a[0].replace('+', '')) - Number(b[0].replace('+', ''))
      if (by === 'type') return TYPE_ORDER.indexOf(a[0]) - TYPE_ORDER.indexOf(b[0])
      return a[0].localeCompare(b[0])
    })
}
