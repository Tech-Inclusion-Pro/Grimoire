import { useState } from 'react'

/** A form that writes the query, the way Scryfall's advanced search does.
 *
 * It fills the search box rather than searching behind your back, so the
 * syntax stays visible and you pick it up by using this a few times. Every
 * field maps to something the parser already understands — there is no second
 * query language here.
 */

const COLORS = [
  ['W', 'White'], ['U', 'Blue'], ['B', 'Black'],
  ['R', 'Red'], ['G', 'Green'], ['C', 'Colourless'],
] as const

const RARITIES = ['common', 'uncommon', 'rare', 'mythic'] as const

const IS_FLAGS = [
  ['owned', 'I own it'],
  ['foil', 'I own a foil'],
  ['etched', 'I own an etched foil'],
  ['deck', 'In one of my decks'],
  ['wishlist', 'On a wishlist'],
  ['lent', 'Lent out'],
  ['unfiled', 'Not filed anywhere'],
  ['legendary', 'Legendary'],
  ['commander', 'Can be a commander'],
  ['vanilla', 'No rules text'],
  ['dfc', 'Double-faced'],
  ['permanent', 'Permanent'],
  ['spell', 'Instant or sorcery'],
] as const

const OPS = ['=', '>=', '<=', '>', '<', '!='] as const

type Num = { op: string; value: string }
const emptyNum = (): Num => ({ op: '>=', value: '' })

function quote(v: string): string {
  const t = v.trim()
  return /[\s"]/.test(t) ? `"${t.replace(/"/g, '')}"` : t
}

export default function AdvancedSearch({ onBuild }: { onBuild: (q: string) => void }) {
  const [name, setName] = useState('')
  const [exact, setExact] = useState(false)
  const [text, setText] = useState('')
  const [type, setType] = useState('')
  const [colors, setColors] = useState<string[]>([])
  const [colorMode, setColorMode] = useState(':')
  const [identity, setIdentity] = useState<string[]>([])
  const [mv, setMv] = useState<Num>(emptyNum)
  const [power, setPower] = useState<Num>(emptyNum)
  const [tough, setTough] = useState<Num>(emptyNum)
  const [usd, setUsd] = useState<Num>(emptyNum)
  const [value, setValue] = useState<Num>(emptyNum)
  const [year, setYear] = useState<Num>(emptyNum)
  const [rarities, setRarities] = useState<string[]>([])
  const [set, setSet] = useState('')
  const [location, setLocation] = useState('')
  const [flags, setFlags] = useState<Record<string, 'yes' | 'no'>>({})

  function toggle(list: string[], set_: (v: string[]) => void, item: string) {
    set_(list.includes(item) ? list.filter(x => x !== item) : [...list, item])
  }

  function build(): string {
    const parts: string[] = []

    if (name.trim()) parts.push(exact ? `!${quote(name)}` : quote(name))
    if (text.trim()) parts.push(`o:${quote(text)}`)
    // Each word its own term: a type line reads "Creature — Elf Druid", so
    // t:"creature elf" is a substring that never occurs and matches nothing.
    for (const word of type.trim().split(/\s+/).filter(Boolean)) {
      parts.push(`t:${quote(word)}`)
    }

    if (colors.length) {
      const letters = colors.includes('C') && colors.length === 1
        ? 'c' : colors.filter(c => c !== 'C').join('').toLowerCase()
      if (letters) parts.push(`c${colorMode}${letters}`)
    }
    if (identity.length) {
      const letters = identity.includes('C') && identity.length === 1
        ? 'c' : identity.filter(c => c !== 'C').join('').toLowerCase()
      if (letters) parts.push(`id<=${letters}`)
    }

    const num = (field: string, n: Num) => {
      if (n.value.trim()) parts.push(`${field}${n.op}${n.value.trim()}`)
    }
    num('mv', mv); num('pow', power); num('tou', tough)
    num('usd', usd); num('value', value); num('year', year)

    if (rarities.length === 1) parts.push(`r:${rarities[0]}`)
    else if (rarities.length > 1) parts.push(`(${rarities.map(r => `r:${r}`).join(' or ')})`)

    if (set.trim()) parts.push(`s:${set.trim().toLowerCase()}`)
    if (location.trim()) parts.push(`loc:${quote(location)}`)

    for (const [flag, state] of Object.entries(flags)) {
      parts.push(state === 'yes' ? `is:${flag}` : `-is:${flag}`)
    }
    return parts.join(' ')
  }

  const preview = build()

  function cycleFlag(flag: string) {
    setFlags(prev => {
      const next = { ...prev }
      if (!next[flag]) next[flag] = 'yes'
      else if (next[flag] === 'yes') next[flag] = 'no'
      else delete next[flag]
      return next
    })
  }

  function reset() {
    setName(''); setExact(false); setText(''); setType('')
    setColors([]); setIdentity([]); setColorMode(':')
    setMv(emptyNum()); setPower(emptyNum()); setTough(emptyNum())
    setUsd(emptyNum()); setValue(emptyNum()); setYear(emptyNum())
    setRarities([]); setSet(''); setLocation(''); setFlags({})
  }

  return (
    <div className="advgrid">
      <Field label="Card name" id="adv-name">
        <input id="adv-name" value={name} onChange={e => setName(e.target.value)}
               placeholder="Llanowar Elves" />
        <label className="inline">
          <input type="checkbox" checked={exact} onChange={e => setExact(e.target.checked)} />
          Exact name
        </label>
      </Field>

      <Field label="Rules text" id="adv-text" hint="Searches both faces of a double-faced card">
        <input id="adv-text" value={text} onChange={e => setText(e.target.value)}
               placeholder="target creature" />
      </Field>

      <Field label="Type line" id="adv-type"
             hint="Several words all have to appear: creature elf finds Elf Druids">
        <input id="adv-type" value={type} onChange={e => setType(e.target.value)}
               placeholder="creature elf" />
      </Field>

      <fieldset className="advfield">
        <legend>Colours</legend>
        <div className="chips">
          {COLORS.map(([c, label]) => (
            <label key={c} className={`chip-check${colors.includes(c) ? ' on' : ''}`}>
              <input type="checkbox" checked={colors.includes(c)}
                     onChange={() => toggle(colors, setColors, c)} />
              <span className={`pip pip-${c}`} aria-hidden="true">{c}</span>
              {label}
            </label>
          ))}
        </div>
        <label className="inline" style={{ marginTop: 8 }}>
          <span className="muted">Match</span>
          <select value={colorMode} onChange={e => setColorMode(e.target.value)}>
            <option value=":">including these</option>
            <option value="=">exactly these</option>
            <option value="<=">at most these</option>
          </select>
        </label>
      </fieldset>

      <fieldset className="advfield">
        <legend>Commander colour identity</legend>
        <p className="muted" style={{ margin: '0 0 6px', fontSize: 12 }}>
          Cards legal in a deck of this identity.
        </p>
        <div className="chips">
          {COLORS.map(([c, label]) => (
            <label key={c} className={`chip-check${identity.includes(c) ? ' on' : ''}`}>
              <input type="checkbox" checked={identity.includes(c)}
                     onChange={() => toggle(identity, setIdentity, c)} />
              <span className={`pip pip-${c}`} aria-hidden="true">{c}</span>
              {label}
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="advfield">
        <legend>Numbers</legend>
        <NumRow label="Mana value" id="adv-mv" value={mv} onChange={setMv} />
        <NumRow label="Power" id="adv-pow" value={power} onChange={setPower} />
        <NumRow label="Toughness" id="adv-tou" value={tough} onChange={setTough} />
        <NumRow label="Price of any printing" id="adv-usd" value={usd} onChange={setUsd} prefix="$" />
        <NumRow label="Worth of a copy I hold" id="adv-val" value={value} onChange={setValue} prefix="$" />
        <NumRow label="Released in year" id="adv-year" value={year} onChange={setYear} />
      </fieldset>

      <fieldset className="advfield">
        <legend>Rarity</legend>
        <div className="chips">
          {RARITIES.map(r => (
            <label key={r} className={`chip-check${rarities.includes(r) ? ' on' : ''}`}>
              <input type="checkbox" checked={rarities.includes(r)}
                     onChange={() => toggle(rarities, setRarities, r)} />
              {r}
            </label>
          ))}
        </div>
      </fieldset>

      <Field label="Set code" id="adv-set" hint="Three or four letters, e.g. dom">
        <input id="adv-set" value={set} onChange={e => setSet(e.target.value)}
               placeholder="dom" autoCapitalize="none" />
      </Field>

      <Field label="Where it is filed" id="adv-loc">
        <input id="adv-loc" value={location} onChange={e => setLocation(e.target.value)}
               placeholder="Binder A" />
      </Field>

      <fieldset className="advfield advwide">
        <legend>Conditions</legend>
        <p className="muted" style={{ margin: '0 0 8px', fontSize: 12 }}>
          Click once to require, twice to exclude, three times to clear.
        </p>
        <div className="chips">
          {IS_FLAGS.map(([flag, label]) => {
            const state = flags[flag]
            return (
              <button key={flag} type="button"
                      className={`chip-check${state ? ' on' : ''}${state === 'no' ? ' negated' : ''}`}
                      aria-pressed={state === 'yes'}
                      onClick={() => cycleFlag(flag)}>
                {state === 'no'
                  ? `✕ not ${label.charAt(0).toLowerCase()}${label.slice(1)}`
                  : state === 'yes' ? `✓ ${label}` : label}
              </button>
            )
          })}
        </div>
      </fieldset>

      <div className="advwide">
        <p className="subhead">Query this builds</p>
        <code className="advpreview">{preview || 'Nothing selected yet.'}</code>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn btn-primary" type="button" disabled={!preview}
                  onClick={() => onBuild(preview)}>
            Search with this
          </button>
          <button className="btn" type="button" onClick={reset}>Clear</button>
        </div>
      </div>
    </div>
  )
}

function Field(
  { label, id, hint, children }: {
    label: string; id: string; hint?: string; children: React.ReactNode
  },
) {
  return (
    <div className="advfield">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>{hint}</p>}
    </div>
  )
}

function NumRow(
  { label, id, value, onChange, prefix }: {
    label: string; id: string; value: { op: string; value: string }
    onChange: (v: { op: string; value: string }) => void; prefix?: string
  },
) {
  return (
    <div className="numrow">
      <label htmlFor={id}>{label}</label>
      <select aria-label={`${label} comparison`} value={value.op}
              onChange={e => onChange({ ...value, op: e.target.value })}>
        {OPS.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
      <input id={id} type="number" inputMode="decimal" value={value.value}
             placeholder={prefix ? '0.00' : '0'}
             onChange={e => onChange({ ...value, value: e.target.value })} />
    </div>
  )
}
