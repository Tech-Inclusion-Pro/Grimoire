import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type DeckSummary, type EmbedStatus, type SynergyResult } from '../api'
import { CardArt } from './CardArt'
import CardDetail from './CardDetail'

const COLORS = [
  ['', 'Any'], ['W', 'White'], ['U', 'Blue'], ['B', 'Black'], ['R', 'Red'],
  ['G', 'Green'], ['WU', 'Azorius'], ['UB', 'Dimir'], ['BR', 'Rakdos'],
  ['RG', 'Gruul'], ['GW', 'Selesnya'], ['WB', 'Orzhov'], ['UR', 'Izzet'],
  ['BG', 'Golgari'], ['RW', 'Boros'], ['GU', 'Simic'], ['WUBRG', 'Five colour'],
] as const

export default function Synergy() {
  const [status, setStatus] = useState<EmbedStatus | null>(null)
  const [decks, setDecks] = useState<DeckSummary[]>([])
  const [goal, setGoal] = useState('')
  const [colors, setColors] = useState('')
  const [format, setFormat] = useState('commander')
  const [excludeDeck, setExcludeDeck] = useState('')
  const [hideCommitted, setHideCommitted] = useState(false)
  const [result, setResult] = useState<SynergyResult | null>(null)
  const [shelved, setShelved] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [building, setBuilding] = useState(false)
  const [detail, setDetail] = useState<{ o: string; c: string | null } | null>(null)

  const refresh = useCallback(async () => {
    setStatus(await api.embedStatus())
    setDecks(await api.decks())
    setShelved(new Set((await api.shelf()).items.map(i => i.oracle_id)))
  }, [])
  useEffect(() => { void refresh() }, [refresh])

  async function run(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      setResult(await api.synergy({
        goal, colors: colors || null, format: format || null,
        exclude_deck: excludeDeck ? Number(excludeDeck) : null,
        hide_committed: hideCommitted, limit: 30,
      }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
      setResult(null)
    } finally { setBusy(false) }
  }

  return (
    <>
      <h1>Find synergies</h1>
      <p className="lede">
        Describe what you want the deck to do. Results are cards you already own,
        ranked, each with a reason — and weak matches say why they are weak
        rather than being hidden.
      </p>

      {status && !status.ready && (
        <div className="tile">
          <h2>Build the index first</h2>
          <p className="lede">
            Semantic search needs one vector per owned card. {status.owned.toLocaleString()} to
            embed, roughly two minutes, all on this machine — nothing leaves it.
          </p>
          <button className="btn btn-primary" disabled={building} onClick={async () => {
            setBuilding(true)
            try { await api.buildEmbeddings(); await refresh() }
            catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
            finally { setBuilding(false) }
          }}>{building ? 'Building… (this takes a couple of minutes)' : 'Build the index'}</button>
          {error && <p className="err" role="alert" style={{ marginTop: 12 }}>{error}</p>}
        </div>
      )}

      {/* Spec 7: recompute on import. Buying cards leaves them unembedded and
          therefore invisible to synergy search, which would look like the
          search simply not knowing about them. */}
      {status?.ready && status.stale > 0 && (
        <div className="tile">
          <h2>{status.stale.toLocaleString()} newer cards are not in the index yet</h2>
          <p className="lede">
            They will not appear in results until they are embedded. Only the
            new ones are processed, so this is quick.
          </p>
          <button className="btn" disabled={building} onClick={async () => {
            setBuilding(true)
            try { await api.buildEmbeddings(); await refresh() }
            finally { setBuilding(false) }
          }}>{building ? 'Embedding…' : `Embed ${status.stale.toLocaleString()} cards`}</button>
        </div>
      )}

      {status?.ready && (
        <div className="tile">
          <form onSubmit={run}>
            <label htmlFor="goal">What should this deck do?</label>
            <textarea id="goal" rows={3} value={goal} onChange={e => setGoal(e.target.value)}
              placeholder="Mono-green, win by making a lot of elves then dumping a huge amount of mana into one finisher." />
            <div className="row" style={{ marginTop: 12 }}>
              <div>
                <label htmlFor="colors">Colour identity</label>
                <select id="colors" value={colors} onChange={e => setColors(e.target.value)}>
                  {COLORS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="fmt">Format</label>
                <select id="fmt" value={format} onChange={e => setFormat(e.target.value)}>
                  <option value="">Any</option>
                  <option value="commander">Commander</option>
                  <option value="modern">Modern</option>
                  <option value="pioneer">Pioneer</option>
                  <option value="legacy">Legacy</option>
                  <option value="pauper">Pauper</option>
                </select>
              </div>
              <div>
                <label htmlFor="exclude">Building for</label>
                <select id="exclude" value={excludeDeck} onChange={e => setExcludeDeck(e.target.value)}>
                  <option value="">No deck in mind</option>
                  {decks.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
              <div style={{ paddingBottom: 8 }}>
                <label htmlFor="hide" style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                  <input id="hide" type="checkbox" checked={hideCommitted}
                         
                         onChange={e => setHideCommitted(e.target.checked)} />
                  Hide cards already in a deck
                </label>
              </div>
              <button className="btn btn-primary" disabled={busy || goal.trim().length < 3}>
                {busy ? 'Searching…' : 'Find cards'}
              </button>
            </div>
          </form>
          <p className="muted" style={{ marginTop: 12 }}>
            Runs entirely on this Mac mini using {status.model}. Your collection and
            your description never leave the machine.
          </p>
          {error && <p className="err" role="alert" style={{ marginTop: 12 }}>{error}</p>}
        </div>
      )}

      {detail && (
        <CardDetail oracleId={detail.o} cardId={detail.c} onClose={() => setDetail(null)} />
      )}

      {result && (
        <div className="tile">
          <div className="spread">
            <h2 style={{ margin: 0 }}>
              {result.results.length} suggestion{result.results.length === 1 ? '' : 's'}
            </h2>
            <p className="muted" style={{ margin: 0 }}>
              from {result.searched.toLocaleString()} owned cards
              {result.goal_roles.length > 0 && <> · read as: {result.goal_roles.join(', ')}</>}
            </p>
          </div>

          {result.results.length === 0 && (
            <p className="muted" style={{ marginTop: 14 }}>
              Nothing matched within those constraints. Try widening the colour
              identity, or describing the plan differently.
            </p>
          )}

          {result.results.map(hit => (
            <article key={hit.oracle_id} style={{
              borderTop: '1px solid rgba(255,255,255,.07)', padding: '14px 0',
            }}>
              <div className="spread" style={{ alignItems: 'flex-start' }}>
                <button className="open" style={{ width: 104, flex: 'none' }}
                        onClick={() => setDetail({ o: hit.oracle_id, c: hit.card_id })}>
                  <CardArt cardId={hit.card_id} name={hit.name} typeLine={hit.type_line}
                           style={{ borderRadius: 6 }} />
                  <span className="vh">Open {hit.name}</span>
                </button>
                <div style={{ flex: '1 1 320px' }}>
                  <p style={{ margin: '0 0 4px', display: 'flex', gap: 8,
                              alignItems: 'baseline', flexWrap: 'wrap' }}>
                    <strong>{hit.name}</strong>
                    <span className="muted">{hit.mana_cost}</span>
                    {/* Strength carries a word and a symbol, never colour alone. */}
                    <span className={
                      hit.strength === 'strong' ? 'pill pill-own'
                      : hit.strength === 'fair' ? 'pill' : 'pill pill-warn'}>
                      {hit.strength === 'strong' ? '✓' : hit.strength === 'fair' ? '~' : '!'}{' '}
                      {hit.match_pct}% {hit.strength}
                    </span>
                    <span className="pill pill-own">✓ Own {hit.owned}</span>
                  </p>
                  <p className="muted" style={{ margin: '0 0 6px' }}>{hit.type_line}</p>
                  <p style={{ margin: 0, fontSize: 14 }}>{hit.reason}</p>
                  {hit.weakness && (
                    <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--warn)' }}>
                      ! {hit.weakness}
                    </p>
                  )}
                  {hit.committed_to.length > 0 && (
                    <p className="muted" style={{ margin: '4px 0 0', fontSize: 13 }}>
                      Already in: {hit.committed_to.join(', ')}
                    </p>
                  )}
                </div>
                <div>
                  {/* Results go to the shelf, never straight into a deck: a
                      search must not quietly change a list already built. */}
                  <button className="btn btn-sm" disabled={shelved.has(hit.oracle_id)}
                    onClick={async () => {
                      await api.shelve(hit.oracle_id, hit.reason)
                      setShelved(new Set([...shelved, hit.oracle_id]))
                    }}>
                    {shelved.has(hit.oracle_id) ? '✓ On the shelf' : 'Send to shelf'}
                    <span className="vh"> — {hit.name}</span>
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  )
}
