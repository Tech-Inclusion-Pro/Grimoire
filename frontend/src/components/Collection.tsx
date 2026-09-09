import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type CardRow, type Location, type Printing,
         type SavedSearch, type SearchResult, type Stats } from '../api'
import { CardArt } from './CardArt'
import CardDetail from './CardDetail'
import ViewControls, { CardMeta, useViewPrefs } from './ViewControls'

const money = (n: number | null | undefined) => n == null ? '—' : `$${n.toFixed(2)}`

const SYNTAX: [string, string][] = [
  ['llanowar', 'Name contains “llanowar”'],
  ['!"Llanowar Elves"', 'That card exactly'],
  ['t:creature', 'Type line contains “creature”'],
  ['o:"target creature"', 'Rules text, both faces of a double-faced card'],
  ['c:g   c=g   c:simic', 'Contains green · exactly green · blue-green'],
  ['id<=g', 'Legal in a mono-green commander deck'],
  ['mv<=3   pow>=5', 'Mana value, power'],
  ['usd>=40', 'Some printing costs that much'],
  ['value>=40', 'A copy you actually own is worth that'],
  ['r:rare   s:dom   year>=2024', 'Rarity, set code, release year'],
  ['is:foil   is:etched   is:lent', 'How you hold it'],
  ['-is:deck', 'Cards in no deck'],
  ['loc:"binder a"', 'Where the card physically is'],
  ['(r:rare or r:mythic) t:artifact', 'Grouping with or'],
]

export default function Collection({ onChanged }: { onChanged: () => void }) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [query, setQuery] = useState('')
  const [ownedOnly, setOwnedOnly] = useState(true)
  const [result, setResult] = useState<SearchResult | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<SavedSearch[]>([])
  const [picking, setPicking] = useState<CardRow | null>(null)
  const [view, setView] = useViewPrefs('collection')
  const [detail, setDetail] = useState<{ o: string; c: string | null } | null>(null)

  const refresh = useCallback(async () => {
    setStats(await api.stats())
    setSaved(await api.savedSearches())
  }, [])
  useEffect(() => { void refresh() }, [refresh])

  const run = useCallback(async (q: string, owned: boolean) => {
    setBusy(true)
    setError('')
    try {
      setResult(await api.search(q, owned))
    } catch (e) {
      if (e instanceof ApiError && e.isUserFixable) { setError(e.message); setResult(null) }
      else throw e
    } finally { setBusy(false) }
  }, [])

  useEffect(() => { void run('', true) }, [run])

  function submit(e: FormEvent) { e.preventDefault(); void run(query, ownedOnly) }

  return (
    <>
      <h1>Collection</h1>
      <p className="lede">
        Search is owned-only unless you opt out, so you never build a deck you cannot sleeve.
      </p>

      {stats && (
        <div className="tile">
          <dl className="stats">
            <Stat label="Cards on hand" value={stats.copies_owned.toLocaleString()} />
            <Stat label="Distinct" value={stats.distinct_owned.toLocaleString()} />
            <Stat label="Market value" value={money(stats.collection_value)} />
            <Stat label="Paid" value={money(stats.purchase_total)} />
            <Stat label="Lent out" value={stats.lent_out.toLocaleString()} />
            <Stat label="Printings known" value={stats.printings.toLocaleString()} />
          </dl>
        </div>
      )}

      <div className="tile">
        <form className="row" role="search" onSubmit={submit}>
          <div style={{ flex: '1 1 220px', minWidth: 0 }}>
            <label htmlFor="q">Query</label>
            <input id="q" type="search" value={query} style={{ width: '100%' }}
                   placeholder="t:creature c:g mv<=3 -is:deck"
                   autoCapitalize="none" spellCheck={false} autoComplete="off"
                   onChange={e => setQuery(e.target.value)} />
          </div>
          <div style={{ paddingBottom: 8 }}>
            <label htmlFor="owned" style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
              <input id="owned" type="checkbox" checked={ownedOnly} 
                     onChange={e => { setOwnedOnly(e.target.checked); void run(query, e.target.checked) }} />
              Only cards I own
            </label>
          </div>
          <button className="btn btn-primary" disabled={busy}>Search</button>
          <button className="btn" type="button" disabled={!query.trim()} onClick={async () => {
            const name = prompt('Name this search', query)
            if (!name) return
            try { await api.saveSearch(name, query); setSaved(await api.savedSearches()) }
            catch (e) { if (e instanceof ApiError) setError(e.message); else throw e }
          }}>Save this search</button>
        </form>

        {error && <p className="err" role="alert" style={{ marginTop: 12 }}>{error}</p>}

        {result && result.terms.length > 0 && (
          <p style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
            <span className="vh">Your query was read as:</span>
            {result.terms.map((t, i) => <span className="badge" key={i}>{t}</span>)}
          </p>
        )}

        {saved.length > 0 && (
          <div style={{ marginTop: 18 }}>
            <p className="subhead" id="saved-h">Saved searches</p>
            <ul aria-labelledby="saved-h"
                style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {saved.map(s => (
                <li key={s.id} style={{ display: 'flex' }}>
                  <button className="btn btn-sm" onClick={() => { setQuery(s.query); void run(s.query, ownedOnly) }}>
                    {s.name} <code>{s.query}</code>
                  </button>
                  <button className="btn btn-sm btn-quiet" aria-label={`Delete saved search ${s.name}`}
                          onClick={async () => { await api.deleteSearch(s.id); setSaved(await api.savedSearches()) }}>
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <details style={{ marginTop: 18, borderTop: '1px solid rgba(255,255,255,.07)', paddingTop: 14 }}>
          <summary style={{ cursor: 'pointer', color: 'var(--tint-mag)', fontSize: 14 }}>
            Search syntax
          </summary>
          <div className="scroll">
            <table style={{ marginTop: 12 }}>
              <caption>
                Combine terms with spaces (and), <code>or</code>, <code>-</code> to negate,
                and brackets to group.
              </caption>
              <thead><tr><th scope="col">Example</th><th scope="col">Finds</th></tr></thead>
              <tbody>
                {SYNTAX.map(([ex, what]) => (
                  <tr key={ex}><td><code>{ex}</code></td><td className="muted">{what}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>

      <div className="tile">
        <div className="spread" style={{ marginBottom: 14 }}>
          <ViewControls prefs={view} onChange={setView} idPrefix="coll" />
        </div>
        <div className="spread">
          <h2 style={{ margin: 0 }}>Results</h2>
          <p className="muted" role="status" style={{ margin: 0 }}>
            {busy ? 'Searching…'
              : result ? `${result.total.toLocaleString()} card${result.total === 1 ? '' : 's'}` +
                         (result.total > result.results.length ? `, showing ${result.results.length}` : '')
              : '—'}
          </p>
        </div>
        {view.mode === 'cards' && (
          <div className="cardgrid" style={{ marginTop: 14, ['--card-w' as string]: `${view.size}px` }}>
            {result?.results.map(c => (
              <div className="cardtile" key={c.oracle_id}>
                <button className="open" onClick={() => setDetail({ o: c.oracle_id, c: c.card_id })}>
                  <CardArt cardId={c.card_id} name={c.name} typeLine={c.type_line}
                           variant={view.size < 165 ? 'small' : 'normal'} />
                  <span className="vh">Open {c.name}</span>
                </button>
                <div className="corner">
                  {c.owned > 0
                    ? <span className="pill pill-own">✓ {c.owned}</span>
                    : <span className="pill pill-none">✕ 0</span>}
                  {c.foils > 0 && <span className="pill pill-foil">★ {c.foils}</span>}
                </div>
                <CardMeta fields={view.fields} card={c} />
                <div className="steppers">
                  <button className="btn btn-sm" onClick={() => setPicking(c)}>
                    Add<span className="vh"> a copy of {c.name}</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {view.mode === 'table' && (
        <div className="scroll" style={{ marginTop: 12 }}>
          <table>
            <caption>
              One row per card, not per printing. A card owned in three printings is
              one row reporting three copies.
            </caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Cost</th>
                <th scope="col">Type</th>
                <th scope="col" className="num">Printings</th>
                <th scope="col">Held</th>
                <th scope="col">Where</th>
                <th scope="col" className="num">Cheapest</th>
                <th scope="col" className="num">Mine</th>
                <th scope="col"><span className="vh">Add a copy</span></th>
              </tr>
            </thead>
            <tbody>
              {result?.results.map(c => (
                <tr key={c.oracle_id}>
                  <td className="name">
                    <button className="btn-link"
                            onClick={() => setDetail({ o: c.oracle_id, c: c.card_id })}>
                      {c.name}
                    </button>
                  </td>
                  <td>{c.mana_cost ?? '—'}</td>
                  <td className="muted">{c.type_line}</td>
                  <td className="num">{c.printings}</td>
                  <td>
                    {c.owned > 0
                      ? <><span className="pill pill-own">✓ Own {c.owned}</span>
                          {c.foils > 0 && <> <span className="pill pill-foil">★ Foil ×{c.foils}</span></>}</>
                      : <span className="pill pill-none">✕ None</span>}
                  </td>
                  <td className="muted">{c.locations ?? '—'}</td>
                  <td className="num muted">{money(c.price_usd)}</td>
                  <td className="num">{money(c.value)}</td>
                  <td>
                    <button className="btn btn-sm" onClick={() => setPicking(c)}>
                      Add<span className="vh"> a copy of {c.name}</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
        {result?.results.length === 0 && (
          <p className="muted" style={{ padding: '18px 2px' }}>
            {ownedOnly
              ? 'No owned cards matched. Untick “Only cards I own” to search everything.'
              : 'No cards matched.'}
          </p>
        )}
      </div>

      {detail && (
        <CardDetail oracleId={detail.o} cardId={detail.c} onClose={() => setDetail(null)} />
      )}

      {picking && (
        <PrintingPicker card={picking} onClose={() => setPicking(null)}
                        onAdded={async () => { await run(query, ownedOnly); await refresh(); onChanged() }} />
      )}
    </>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="stat"><dt>{label}</dt><dd>{value}</dd></div>
}

function PrintingPicker(
  { card, onClose, onAdded }: { card: CardRow; onClose: () => void; onAdded: () => void },
) {
  const [printings, setPrintings] = useState<Printing[] | null>(null)
  const [locations, setLocations] = useState<Location[]>([])
  const [finish, setFinish] = useState('normal')
  const [locationId, setLocationId] = useState('')

  useEffect(() => {
    void api.printings(card.oracle_id).then(setPrintings)
    void api.locations().then(setLocations)
  }, [card.oracle_id])

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="picker-h"
         style={{ position: 'fixed', inset: 0, background: 'rgba(5,3,14,.72)',
                  display: 'grid', placeItems: 'center', zIndex: 50, padding: 16 }}>
      <div className="tile" style={{ maxWidth: 660, width: '100%', maxHeight: '84vh',
                                     overflowY: 'auto', background: '#16112e' }}>
        <h2 id="picker-h">Choose a printing — {card.name}</h2>
        <p className="muted">
          Quantity is tracked per printing, not per card: foils, sets and conditions differ.
        </p>
        <div className="row" style={{ margin: '14px 0' }}>
          <div>
            <label htmlFor="finish">Finish</label>
            <select id="finish" value={finish} onChange={e => setFinish(e.target.value)}>
              <option value="normal">Normal</option>
              <option value="foil">Foil</option>
              <option value="etched">Etched foil</option>
            </select>
          </div>
          <div>
            <label htmlFor="loc">Location</label>
            <select id="loc" value={locationId} onChange={e => setLocationId(e.target.value)}>
              <option value="">Unfiled</option>
              {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
          </div>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Set</th><th scope="col">No.</th><th scope="col">Rarity</th>
                <th scope="col" className="num">Price</th><th scope="col">Held</th>
                <th scope="col"><span className="vh">Add</span></th>
              </tr>
            </thead>
            <tbody>
              {printings?.slice(0, 60).map(p => (
                <tr key={p.id}>
                  <td>{p.set_name} <span className="muted">{p.set_code?.toUpperCase()}</span></td>
                  <td className="muted">{p.collector_num}</td>
                  <td className="muted">{p.rarity}</td>
                  <td className="num">{money(p.price_usd)}</td>
                  <td>{p.owned
                    ? <span className="pill pill-own">✓ {p.owned}</span>
                    : <span className="pill pill-none">✕ None</span>}</td>
                  <td>
                    <button className="btn btn-sm" onClick={async () => {
                      await api.addCopy({
                        card_id: p.id, quantity: 1, finish,
                        location_id: locationId ? Number(locationId) : null,
                      })
                      setPrintings(await api.printings(card.oracle_id))
                      onAdded()
                    }}>Add<span className="vh"> {p.set_name} printing</span></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
