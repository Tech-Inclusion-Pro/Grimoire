import { useEffect, useState } from 'react'
import { api, type DeckDetail, type HistoryEntry } from '../api'
import ManaCurve from './ManaCurve'

const money = (n: number | null | undefined) => n == null ? '—' : `$${n.toFixed(2)}`

export default function DeckPanels(
  { detail, onRestored }: { detail: DeckDetail; onRestored: () => void },
) {
  const { stats, tokens, deck } = detail
  const [history, setHistory] = useState<HistoryEntry[] | null>(null)

  useEffect(() => { void api.history(deck.id).then(setHistory) }, [deck.id, detail])

  return (
    <div>
      <div className="tile">
        <h3>Buildable right now</h3>
        <p style={{ fontSize: 30, fontWeight: 'bold', color: 'var(--tint-mag)', margin: '0 0 6px' }}>
          {stats.buildable_pct}%
        </p>
        <div role="img" aria-label={
          `${stats.buildable_now} of ${stats.cards} cards are ones you own.`}
          style={{ height: 8, borderRadius: 4, background: 'rgba(255,255,255,.1)', overflow: 'hidden' }}>
          <div style={{
            width: `${stats.buildable_pct}%`, height: '100%',
            background: 'var(--magenta)',
          }} />
        </div>
        <p className="muted" style={{ marginTop: 8 }}>
          {stats.buildable_now} of {stats.cards} cards on hand.
          {stats.missing > 0 && <> {stats.missing} still to buy.</>}
        </p>
      </div>

      <div className="tile">
        <h3>Mana curve</h3>
        <ManaCurve curve={stats.curve} />
      </div>

      <div className="tile">
        <h3>Value</h3>
        <p className="muted">
          Cheapest printings total <strong style={{ color: 'var(--ink)' }}>{money(stats.value)}</strong>.
          {stats.wishlist > 0 && <> {stats.wishlist} card{stats.wishlist === 1 ? '' : 's'} not yet owned.</>}
        </p>
      </div>

      {stats.conflicts.length > 0 && (
        <div className="tile">
          <h3>Cards promised to another deck</h3>
          <p className="muted" style={{ marginBottom: 10 }}>
            Counted as buildable here, but another deck also uses them. Without
            this, two decks each read 100% while sharing one physical card.
          </p>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {stats.conflicts.map(c => (
              <li key={c.oracle_id} style={{ fontSize: 13, marginBottom: 4 }}>
                <span className="pill pill-warn">! Shared</span>{' '}
                {c.name} — need {c.needed}, own {c.owned}, {c.used_elsewhere} used elsewhere
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="tile">
        <h3>Tokens to bring</h3>
        {tokens.length === 0
          ? <p className="muted">Nothing in this deck makes a token.</p>
          : <ul style={{ margin: 0, paddingLeft: 18 }}>
              {tokens.map(t => (
                <li key={t.name} style={{ fontSize: 13, marginBottom: 3 }}>
                  {t.name} <span className="muted">{t.type_line}</span>
                </li>
              ))}
            </ul>}
      </div>

      <div className="tile">
        <h3>History</h3>
        {!history?.length
          ? <p className="muted">No snapshots yet. One is taken before each bulk add.</p>
          : <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {history.slice(0, 8).map(h => (
                <li key={h.id} style={{ display: 'flex', gap: 8, alignItems: 'center',
                                        marginBottom: 6, fontSize: 13 }}>
                  <span className="muted" style={{ flex: 1 }}>
                    {h.at.slice(0, 16).replace('T', ' ')} — {h.summary ?? 'snapshot'}
                  </span>
                  <button className="btn btn-sm" onClick={async () => {
                    await api.restore(deck.id, h.id)
                    onRestored()
                  }}>Restore</button>
                </li>
              ))}
            </ul>}
      </div>
    </div>
  )
}
