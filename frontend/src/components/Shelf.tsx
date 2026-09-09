import { useCallback, useEffect, useState } from 'react'
import { api, type DeckSummary, type Package, type ShelfItem } from '../api'
import { CardArt } from './CardArt'
import CardDetail from './CardDetail'

export default function Shelf() {
  const [items, setItems] = useState<ShelfItem[]>([])
  const [packages, setPackages] = useState<Package[]>([])
  const [decks, setDecks] = useState<DeckSummary[]>([])
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [deckId, setDeckId] = useState('')
  const [keep, setKeep] = useState(false)
  const [status, setStatus] = useState('')
  const [detail, setDetail] = useState<{ o: string; c: string | null } | null>(null)

  const load = useCallback(async () => {
    const s = await api.shelf()
    setItems(s.items)
    setPackages(s.packages)
    setDecks(await api.decks())
    setChosen(new Set())
  }, [])
  useEffect(() => { void load() }, [load])

  // No selection means "everything on the shelf".
  const selected = [...chosen]

  function toggle(id: string) {
    const next = new Set(chosen)
    next.has(id) ? next.delete(id) : next.add(id)
    setChosen(next)
  }

  return (
    <>
      <h1>Shelf</h1>
      <p className="lede">
        A staging area between thinking and building. Synergy results land here
        rather than in a deck, so a search can never quietly change a list you
        have already built — one extra click per accepted suggestion.
      </p>

      <div className="tile">
        <div className="spread">
          <h2 style={{ margin: 0 }}>
            {items.length} card{items.length === 1 ? '' : 's'} parked
          </h2>
          {items.length > 0 && (
            <div className="row" style={{ alignItems: 'flex-end' }}>
              <div>
                <label htmlFor="senddeck">Send to</label>
                <select id="senddeck" value={deckId} onChange={e => setDeckId(e.target.value)}>
                  <option value="">Choose a deck</option>
                  {decks.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
              <div style={{ paddingBottom: 8 }}>
                <label htmlFor="keep" style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                  <input id="keep" type="checkbox" checked={keep} 
                         onChange={e => setKeep(e.target.checked)} />
                  Keep on the shelf
                </label>
              </div>
              <button className="btn btn-primary" disabled={!deckId}
                onClick={async () => {
                  const r = await api.shelfSend(Number(deckId), selected.length ? selected : null, keep)
                  setStatus(`Added ${r.added} card${r.added === 1 ? '' : 's'}. A snapshot was taken first, so Restore undoes it.`)
                  await load()
                }}>
                Send {selected.length ? `${selected.length} selected` : `all ${items.length}`}
              </button>
              <button className="btn" disabled={!selected.length}
                onClick={async () => {
                  const name = prompt('Name this package', 'Green ramp suite')
                  if (!name) return
                  await api.createPackage(name, selected)
                  setStatus(`Saved "${name}" as a reusable package.`)
                  await load()
                }}>Save as package</button>
            </div>
          )}
        </div>

        {status && <p className="muted" role="status" style={{ marginTop: 10 }}>{status}</p>}

        {items.length === 0 && (
          <p className="muted" style={{ marginTop: 12 }}>
            Nothing shelved. Find cards under <strong>Find synergies</strong> and
            send the ones worth considering here.
          </p>
        )}

        {items.map(item => (
          <article key={item.oracle_id} style={{
            borderTop: '1px solid rgba(255,255,255,.07)', padding: '12px 0',
          }}>
            <div className="spread">
              <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
                              flex: '1 1 300px', margin: 0, cursor: 'pointer' }}>
                <input type="checkbox" checked={chosen.has(item.oracle_id)}
                       style={{ marginTop: 2 }}
                       onChange={() => toggle(item.oracle_id)} />
                <button className="open" style={{ width: 78, flex: 'none' }}
                        onClick={e => { e.preventDefault(); setDetail({ o: item.oracle_id, c: item.card_id }) }}>
                  <CardArt cardId={item.card_id} name={item.name} typeLine={item.type_line}
                           style={{ borderRadius: 5 }} />
                  <span className="vh">Open {item.name}</span>
                </button>
                <span>
                  <strong style={{ color: 'var(--ink)' }}>{item.name}</strong>{' '}
                  <span className="muted">{item.mana_cost}</span>
                  <br />
                  <span className="muted">{item.type_line}</span>
                </span>
              </label>
              <button className="btn btn-sm btn-quiet" onClick={async () => {
                await api.unshelve(item.oracle_id); await load()
              }}>Remove<span className="vh"> {item.name} from the shelf</span></button>
            </div>
            <label className="vh" htmlFor={`note-${item.oracle_id}`}>
              Why did you keep {item.name}?
            </label>
            <textarea id={`note-${item.oracle_id}`} rows={2} defaultValue={item.note ?? ''}
              placeholder="Why did you keep this?" style={{ marginTop: 8 }}
              onBlur={e => { void api.shelfNote(item.oracle_id, e.target.value || null) }} />
          </article>
        ))}
      </div>

      {detail && (
        <CardDetail oracleId={detail.o} cardId={detail.c} onClose={() => setDetail(null)} />
      )}

      <div className="tile">
        <h2>Packages</h2>
        <p className="muted">
          Reusable card groups — a green ramp suite, a mono-green landbase —
          loaded onto the shelf, which is still the only route into a deck.
        </p>
        {packages.length === 0 && (
          <p className="muted" style={{ marginTop: 12 }}>
            No packages yet. Select cards above and save them as one.
          </p>
        )}
        <ul style={{ listStyle: 'none', margin: '12px 0 0', padding: 0,
                     display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {packages.map(p => (
            <li key={p.id} style={{ display: 'flex' }}>
              <button className="btn btn-sm" onClick={async () => {
                const r = await api.packageToShelf(p.id)
                setStatus(`Loaded ${r.added} card${r.added === 1 ? '' : 's'} from "${p.name}" onto the shelf.`)
                await load()
              }}>{p.name} <span className="muted">{p.cards} cards</span></button>
              <button className="btn btn-sm btn-quiet" aria-label={`Delete package ${p.name}`}
                onClick={async () => { await api.deletePackage(p.id); await load() }}>✕</button>
            </li>
          ))}
        </ul>
      </div>
    </>
  )
}
