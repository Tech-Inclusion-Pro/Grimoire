import { useEffect, useState } from 'react'
import { Pips } from './CardArt'

/** Which optional fields to show under a card. Multi-select, remembered per
 *  view, because what matters when deckbuilding is not what matters when
 *  pricing a binder. */
export const FIELDS = [
  ['name', 'Name'],
  ['cost', 'Mana cost'],
  ['cmc', 'Mana value'],
  ['colors', 'Colours'],
  ['type', 'Type'],
  ['price', 'Price'],
  ['owned', 'Copies held'],
] as const

export type Field = (typeof FIELDS)[number][0]
export type ViewMode = 'cards' | 'table'

const DEFAULTS: Record<string, { mode: ViewMode; fields: Field[]; size: number }> = {
  collection: { mode: 'cards', fields: ['name', 'cost', 'owned'], size: 200 },
  deck: { mode: 'cards', fields: ['name', 'cost', 'owned'], size: 170 },
}

export function useViewPrefs(key: keyof typeof DEFAULTS | string) {
  const storageKey = `grimoire.view.${key}`
  const fallback = DEFAULTS[key] ?? DEFAULTS.collection
  const [prefs, setPrefs] = useState(() => {
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) return { ...fallback, ...JSON.parse(raw) }
    } catch { /* private window, or cleared storage */ }
    return fallback
  })
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(prefs)) }
    catch { /* not worth failing a render over */ }
  }, [storageKey, prefs])
  return [prefs, setPrefs] as const
}

export default function ViewControls(
  { prefs, onChange, idPrefix }: {
    prefs: { mode: ViewMode; fields: Field[]; size: number }
    onChange: (next: { mode: ViewMode; fields: Field[]; size: number }) => void
    idPrefix: string
  },
) {
  const toggle = (f: Field) => {
    const has = prefs.fields.includes(f)
    onChange({
      ...prefs,
      fields: has ? prefs.fields.filter(x => x !== f) : [...prefs.fields, f],
    })
  }

  return (
    <div className="row" style={{ alignItems: 'flex-start', gap: 18 }}>
      <div>
        <span className="subhead" id={`${idPrefix}-view-h`}>View</span>
        <div className="seg" role="group" aria-labelledby={`${idPrefix}-view-h`}>
          <button type="button" aria-pressed={prefs.mode === 'cards'}
                  onClick={() => onChange({ ...prefs, mode: 'cards' })}>Cards</button>
          <button type="button" aria-pressed={prefs.mode === 'table'}
                  onClick={() => onChange({ ...prefs, mode: 'table' })}>Table</button>
        </div>
      </div>

      {prefs.mode === 'cards' && (
        <>
          <div>
            <span className="subhead" id={`${idPrefix}-fields-h`}>Show under each card</span>
            <div className="fieldpick" role="group" aria-labelledby={`${idPrefix}-fields-h`}>
              {FIELDS.map(([id, label]) => (
                <label key={id} htmlFor={`${idPrefix}-f-${id}`}>
                  <input id={`${idPrefix}-f-${id}`} type="checkbox"
                         checked={prefs.fields.includes(id)}
                         onChange={() => toggle(id)} />
                  {label}
                </label>
              ))}
            </div>
          </div>
          <div>
            <label htmlFor={`${idPrefix}-size`}>Card size</label>
            <input id={`${idPrefix}-size`} type="range" min={130} max={300} step={10}
                   value={prefs.size} style={{ width: 140 }}
                   onChange={e => onChange({ ...prefs, size: Number(e.target.value) })} />
          </div>
        </>
      )}
    </div>
  )
}

const money = (n: number | null | undefined) => n == null ? '—' : `$${n.toFixed(2)}`

/** The metadata line under a card, built from whichever fields are selected. */
export function CardMeta(
  { fields, card }: {
    fields: Field[]
    card: {
      name: string; mana_cost?: string | null; mana_value?: number | null
      color_identity?: string; type_line?: string | null
      price_usd?: number | null; owned?: number
    }
  },
) {
  if (fields.length === 0) return null
  const inline = fields.filter(f => f !== 'name' && f !== 'type')
  return (
    <div className="meta">
      {fields.includes('name') && <span className="cname">{card.name}</span>}
      {fields.includes('type') && <span className="line">{card.type_line}</span>}
      {inline.length > 0 && (
        <span className="line">
          {fields.includes('cost') && <span>{card.mana_cost || '—'}</span>}
          {fields.includes('cmc') && (
            <span title="Mana value">MV {card.mana_value ?? 0}</span>
          )}
          {fields.includes('colors') && <Pips identity={card.color_identity ?? ''} />}
          {fields.includes('price') && <span>{money(card.price_usd)}</span>}
          {fields.includes('owned') && card.owned !== undefined && (
            <span className={card.owned > 0 ? 'pill pill-own' : 'pill pill-none'}>
              {card.owned > 0 ? `✓ ${card.owned}` : '✕ 0'}
            </span>
          )}
        </span>
      )}
    </div>
  )
}