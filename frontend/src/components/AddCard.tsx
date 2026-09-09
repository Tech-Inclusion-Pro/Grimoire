import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type CardRow } from '../api'
import { CardArt } from './CardArt'

/** Search the collection and add cards to a deck one at a time.
 *
 * Owned-only by default, like every other search: adding a card you cannot
 * sleeve should be a deliberate act. Opting out marks the addition as wishlist
 * on the way in, so the buildable count stays honest.
 */
export default function AddCard(
  { deckId, inDeck, onAdded }: {
    deckId: number
    inDeck: Map<string, number>
    onAdded: () => void
  },
) {
  const [term, setTerm] = useState('')
  const [ownedOnly, setOwnedOnly] = useState(true)
  const [hits, setHits] = useState<CardRow[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [justAdded, setJustAdded] = useState<string | null>(null)
  const seq = useRef(0)

  useEffect(() => {
    const q = term.trim()
    if (q.length < 2) { setHits([]); setError(''); return }
    // Debounced: a request per keystroke would hammer a 108k-row scan.
    const mine = ++seq.current
    const timer = setTimeout(async () => {
      setBusy(true)
      try {
        const r = await api.search(q, ownedOnly, 12)
        if (mine === seq.current) { setHits(r.results); setError('') }
      } catch (e) {
        if (mine === seq.current) {
          setError(e instanceof ApiError && e.isUserFixable ? e.message : 'Search failed.')
          setHits([])
        }
      } finally {
        if (mine === seq.current) setBusy(false)
      }
    }, 220)
    return () => clearTimeout(timer)
  }, [term, ownedOnly])

  async function add(card: CardRow) {
    const current = inDeck.get(card.oracle_id) ?? 0
    await api.setCard(deckId, {
      oracle_id: card.oracle_id,
      quantity: current + 1,
      // An unowned card is a purchase plan, not a lie about what you hold.
      wishlist: card.owned === 0 ? true : undefined,
    })
    setJustAdded(card.name)
    onAdded()
  }

  return (
    <div className="tile">
      <h3>Add a card</h3>
      <p className="muted" style={{ marginBottom: 10 }}>
        Type a name and add cards one at a time. Search is owned-only unless you
        opt out; anything you add that you do not own is marked as a wishlist card.
      </p>

      <div className="row">
        <div style={{ flex: '1 1 220px', minWidth: 0 }}>
          <label htmlFor="addcard">Card name or rules text</label>
          <input id="addcard" type="search" value={term} style={{ width: '100%' }}
                 placeholder="Llanowar Elves" autoComplete="off"
                 autoCapitalize="none" spellCheck={false}
                 onChange={e => setTerm(e.target.value)} />
        </div>
        <div style={{ paddingBottom: 8 }}>
          <label htmlFor="addowned" style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
            <input id="addowned" type="checkbox" checked={ownedOnly}
                   
                   onChange={e => setOwnedOnly(e.target.checked)} />
            Only cards I own
          </label>
        </div>
      </div>

      <p className="muted" role="status" style={{ minHeight: '1.3em', margin: '10px 0 0' }}>
        {error ? <span className="err">{error}</span>
          : busy ? 'Searching…'
          : justAdded ? `Added ${justAdded}.`
          : term.trim().length === 1 ? 'Keep typing…' : ''}
      </p>

      {hits.length > 0 && (
        <div className="cardgrid" style={{ marginTop: 12, ['--card-w' as string]: '132px' }}>
          {hits.map(card => {
            const already = inDeck.get(card.oracle_id) ?? 0
            return (
              <div className="cardtile" key={card.oracle_id}>
                <CardArt cardId={card.card_id} name={card.name}
                         typeLine={card.type_line} variant="small" />
                {already > 0 && <span className="qty">{already}</span>}
                <div className="meta">
                  <span className="cname">{card.name}</span>
                  <span className="line">
                    <span>{card.mana_cost || '—'}</span>
                    {card.owned > 0
                      ? <span className="pill pill-own">✓ {card.owned}</span>
                      : <span className="pill pill-warn">! not owned</span>}
                  </span>
                </div>
                <div className="steppers">
                  <button className="btn btn-sm" onClick={() => add(card)}>
                    {already > 0 ? 'Add another' : 'Add'}
                    <span className="vh"> — {card.name}</span>
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
