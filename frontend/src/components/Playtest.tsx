import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { CardArt } from './CardArt'
import CardDetail from './CardDetail'
import { navigate } from '../router'

/** Goldfishing. No rules are enforced anywhere — nothing here checks costs,
 *  timing, legality or whether a play makes sense. It moves cards between
 *  zones and counts things, which is what testing an opening hand needs.
 *
 *  Cards are moved by selecting one and choosing a destination, not by
 *  dragging. Dragging alone would fail WCAG 2.5.7, and buttons work on a
 *  touchscreen with one hand holding a drink. */

const ZONES = ['hand', 'battlefield', 'graveyard', 'exile', 'library', 'command'] as const
type Zone = (typeof ZONES)[number]

const ZONE_LABELS: Record<Zone, string> = {
  hand: 'Hand', battlefield: 'Battlefield', graveyard: 'Graveyard',
  exile: 'Exile', library: 'Library', command: 'Command zone',
}

interface Card {
  uid: string; oracle_id: string; card_id: string | null
  name: string; mana_cost: string | null; mana_value: number | null
  type_line: string | null; oracle_text: string | null; color_identity: string
}

interface CardState extends Card {
  tapped: boolean
  counters: number
  // Battlefield position, in pixels from the top-left of the play area.
  // Undefined everywhere else: only the battlefield is a canvas.
  x?: number
  y?: number
}

type Zones = Record<Zone, CardState[]>

function shuffle<T>(items: T[]): T[] {
  const out = [...items]
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

const dressed = (c: Card): CardState => ({ ...c, tapped: false, counters: 0 })

const CARD_W = 104
const CARD_H = 145

/** Where a card lands when it enters the battlefield. Cascades so a turn's
 *  worth of permanents does not pile up on one spot, and wraps into rows
 *  rather than running off the right edge. */
function nextSpot(existing: CardState[], width: number): { x: number; y: number } {
  const perRow = Math.max(1, Math.floor((width - 16) / (CARD_W + 12)))
  const n = existing.length
  return {
    x: 12 + (n % perRow) * (CARD_W + 12),
    y: 12 + Math.floor(n / perRow) * (CARD_H + 14),
  }
}

export default function Playtest({ id }: { id: number }) {
  const [deckName, setDeckName] = useState('')
  const [zones, setZones] = useState<Zones | null>(null)
  const [life, setLife] = useState(40)
  const [startingLife, setStartingLife] = useState(40)
  const [turn, setTurn] = useState(1)
  const [mulligans, setMulligans] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const [source, setSource] = useState<Card[]>([])
  const [commanders, setCommanders] = useState<Card[]>([])
  const [log, setLog] = useState<string[]>([])
  const [zoom, setZoom] = useState<{ o: string; c: string | null } | null>(null)
  const fieldWidth = useRef(900)

  // One drag system for every zone. `moved` is what separates a tap from a
  // drag; without it every attempt to pick a card up would also tap it.
  const drag = useRef<{ uid: string; from: Zone; moved: boolean } | null>(null)
  const [ghost, setGhost] = useState<{ card: CardState; x: number; y: number } | null>(null)
  const [overZone, setOverZone] = useState<Zone | null>(null)

  const say = useCallback((line: string) => {
    setLog(l => [line, ...l].slice(0, 40))
  }, [])

  const deal = useCallback((library: Card[], cmd: Card[], bottom = 0) => {
    const shuffled = shuffle(library)
    const hand = shuffled.slice(0, 7)
    const rest = shuffled.slice(7)
    setZones({
      hand: hand.map(dressed),
      battlefield: [], graveyard: [], exile: [],
      library: rest.map(dressed),
      command: cmd.map(dressed),
    })
    setSelected(null)
    if (bottom > 0) {
      say(`Mulligan to ${7 - bottom}: draw seven, then put ${bottom} on the bottom.`)
    }
  }, [say])

  useEffect(() => {
    void (async () => {
      const data = await api.playtest(id)
      setDeckName(data.deck.name)
      setStartingLife(data.starting_life)
      setLife(data.starting_life)
      setSource(data.library)
      setCommanders(data.command_zone)
      deal(data.library, data.command_zone)
      say(`Opening hand for ${data.deck.name}.`)
    })()
  }, [id, deal, say])

  const move = useCallback((uid: string, to: Zone, toBottom = false,
                           at?: { x: number; y: number }) => {
    setZones(prev => {
      if (!prev) return prev
      let moving: CardState | undefined
      const next = { ...prev }
      for (const zone of ZONES) {
        const found = next[zone].find(c => c.uid === uid)
        if (found) {
          moving = found
          next[zone] = next[zone].filter(c => c.uid !== uid)
          break
        }
      }
      if (!moving) return prev
      let landed: CardState
      if (to === 'battlefield') {
        landed = { ...moving, ...(at ?? nextSpot(next.battlefield, fieldWidth.current)) }
      } else {
        // Leaving the battlefield resets what only meant anything there.
        landed = { ...moving, tapped: false, counters: 0, x: undefined, y: undefined }
      }
      next[to] = toBottom ? [...next[to], landed] : [landed, ...next[to]]
      return next
    })
    setSelected(null)
  }, [])

  const moveTo = useCallback((uid: string, to: Zone, x?: number, y?: number) => {
    move(uid, to, false, x != null && y != null ? { x, y } : undefined)
  }, [move])

  const placeOnField = useCallback((uid: string, x: number, y: number) => {
    setZones(p => p && {
      ...p, battlefield: p.battlefield.map(c => c.uid === uid ? { ...c, x, y } : c),
    })
  }, [])

  const draw = useCallback((n = 1) => {
    setZones(prev => {
      if (!prev) return prev
      const drawn = prev.library.slice(0, n)
      if (drawn.length === 0) { say('Library is empty.'); return prev }
      say(drawn.length === 1 ? `Drew ${drawn[0].name}.` : `Drew ${drawn.length} cards.`)
      return { ...prev, hand: [...drawn, ...prev.hand], library: prev.library.slice(n) }
    })
  }, [say])

  const untapAll = useCallback(() => {
    setZones(prev => prev && {
      ...prev, battlefield: prev.battlefield.map(c => ({ ...c, tapped: false })),
    })
    say('Untapped everything.')
  }, [say])

  const mulligan = useCallback(() => {
    const next = mulligans + 1
    setMulligans(next)
    deal(source, commanders, next)
  }, [mulligans, source, commanders, deal])

  // Keyboard shortcuts, so a goldfish session does not need the mouse.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const key = e.key.toLowerCase()
      if (key === 'd') { e.preventDefault(); draw(1) }
      else if (key === 'u') { e.preventDefault(); untapAll() }
      else if (key === 'm') { e.preventDefault(); mulligan() }
      else if (key === 'n') {
        e.preventDefault()
        setTurn(t => t + 1); untapAll(); draw(1)
      }
      else if (key === 'escape') setSelected(null)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [draw, untapAll, mulligan])

  /** Handlers for one card. `onTapLike` is what a press that never moved
   *  means in this zone: tapping on the battlefield, reading it anywhere else. */
  function dragProps(card: CardState, from: Zone, onTapLike: () => void) {
    return {
      onPointerDown: (e: React.PointerEvent) => {
        if (e.button !== 0) return
        ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
        drag.current = { uid: card.uid, from, moved: false }
        if (from === 'battlefield') setSelected(card.uid)
      },
      onPointerMove: (e: React.PointerEvent) => {
        const d = drag.current
        if (!d || d.uid !== card.uid) return
        if (!d.moved) {
          // A few pixels of slop, so a shaky thumb still reads as a tap.
          if (Math.abs(e.movementX) + Math.abs(e.movementY) < 3 && !ghost) return
          d.moved = true
        }
        setGhost({ card, x: e.clientX, y: e.clientY })
        const under = document.elementFromPoint(e.clientX, e.clientY)
        const target = under?.closest('[data-zone]')?.getAttribute('data-zone')
        setOverZone((target as Zone) ?? null)
      },
      onPointerUp: (e: React.PointerEvent) => {
        const d = drag.current
        drag.current = null
        try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId) } catch { /* gone */ }
        const wasDragging = d?.moved
        setGhost(null)
        setOverZone(null)
        if (!wasDragging) { onTapLike(); return }

        const under = document.elementFromPoint(e.clientX, e.clientY)
        const holder = under?.closest('[data-zone]') as HTMLElement | null
        const to = holder?.getAttribute('data-zone') as Zone | undefined
        if (!to) return

        if (to === 'battlefield') {
          const box = holder!.getBoundingClientRect()
          const x = clamp(e.clientX - box.left - CARD_W / 2, box.width - CARD_W)
          const y = clamp(e.clientY - box.top - CARD_H / 2, box.height - CARD_H)
          if (from === 'battlefield') placeOnField(card.uid, x, y)
          else moveTo(card.uid, 'battlefield', x, y)
        } else if (to !== from) {
          moveTo(card.uid, to)
        }
      },
    }
  }

  if (!zones) return <p className="muted">Shuffling…</p>

  const chosen = ZONES.flatMap(z => zones[z]).find(c => c.uid === selected) ?? null

  return (
    <>
      <button className="btn btn-quiet" style={{ marginBottom: 14 }}
              onClick={() => navigate({ name: 'deck', id })}>
        ← Back to {deckName}
      </button>

      <div className="spread">
        <div>
          <h1>Playtesting {deckName}</h1>
          <p className="lede">
            Goldfishing only — nothing here enforces rules, checks costs or
            stops an illegal play. Move a card by selecting it and choosing
            where it goes.
          </p>
        </div>
      </div>

      <div className="tile">
        <div className="row" style={{ alignItems: 'center', gap: 22 }}>
          <div>
            <span className="subhead">Life</span>
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <button className="btn btn-sm" onClick={() => setLife(l => l - 1)}
                      aria-label="Lose one life">−</button>
              <strong style={{ fontSize: 22, minWidth: 42, textAlign: 'center' }}
                      aria-live="polite">{life}</strong>
              <button className="btn btn-sm" onClick={() => setLife(l => l + 1)}
                      aria-label="Gain one life">+</button>
            </div>
          </div>
          <div>
            <span className="subhead">Turn</span>
            <strong style={{ fontSize: 22 }}>{turn}</strong>
          </div>
          <div>
            <span className="subhead">Mulligans</span>
            <strong style={{ fontSize: 22 }}>{mulligans}</strong>
          </div>
          <div>
            <span className="subhead">Library</span>
            <strong style={{ fontSize: 22 }}>{zones.library.length}</strong>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn" onClick={() => draw(1)}>Draw <kbd>D</kbd></button>
            <button className="btn" onClick={untapAll}>Untap all <kbd>U</kbd></button>
            <button className="btn" onClick={() => { setTurn(t => t + 1); untapAll(); draw(1) }}>
              Next turn <kbd>N</kbd>
            </button>
            <button className="btn" onClick={mulligan}>Mulligan <kbd>M</kbd></button>
            <button className="btn btn-quiet" onClick={() => {
              setMulligans(0); setTurn(1); setLife(startingLife)
              deal(source, commanders); say('Reset.')
            }}>Restart</button>
          </div>
        </div>
      </div>

      {chosen && !ghost && !zones.battlefield.some(c => c.uid === chosen.uid) && (
        <div className="tile" role="status">
          <div className="row" style={{ alignItems: 'center' }}>
            <strong style={{ flex: '1 1 200px' }}>{chosen.name} selected</strong>
            <span className="muted">Send to:</span>
            {ZONES.map(z => (
              <button key={z} className="btn btn-sm" onClick={() => move(chosen.uid, z)}>
                {ZONE_LABELS[z]}
              </button>
            ))}
            <button className="btn btn-sm" onClick={() => move(chosen.uid, 'library', true)}>
              Bottom of library
            </button>
            <button className="btn btn-sm btn-quiet" onClick={() => setSelected(null)}>
              Cancel <kbd>Esc</kbd>
            </button>
          </div>
        </div>
      )}

      <Battlefield
        cards={zones.battlefield}
        selected={selected}
        onSelect={setSelected}
        onWidth={w => { fieldWidth.current = w }}
        dragProps={dragProps}
        dragging={ghost !== null}
        dropping={overZone === 'battlefield'}
        onPlace={placeOnField}
        onTap={uid => setZones(p => p && {
          ...p, battlefield: p.battlefield.map(c =>
            c.uid === uid ? { ...c, tapped: !c.tapped } : c),
        })}
        onCounter={(uid, delta) => setZones(p => p && {
          ...p, battlefield: p.battlefield.map(c =>
            c.uid === uid ? { ...c, counters: Math.max(0, c.counters + delta) } : c),
        })}
        onZoom={c => setZoom({ o: c.oracle_id, c: c.card_id })}
        onMove={move}
        onTidy={() => setZones(p => p && {
          ...p, battlefield: p.battlefield.map((c, i) => ({
            ...c, ...nextSpot(p.battlefield.slice(0, i), fieldWidth.current),
          })),
        })}
      />

      {(['hand', 'command', 'graveyard', 'exile', 'library'] as Zone[]).map(zone => (
        <ZonePanel key={zone} zone={zone} cards={zones[zone]}
                   selected={selected} onSelect={setSelected}
                   dragProps={dragProps}
                   dropping={overZone === zone}
                   anyDrag={ghost !== null}
                   onZoom={c => setZoom({ o: c.oracle_id, c: c.card_id })} />
      ))}

      {/* The card following the pointer. pointer-events:none so the hit test
          underneath finds the zone rather than the ghost itself. */}
      {ghost && (
        <div className="dragghost" aria-hidden="true"
             style={{ left: ghost.x, top: ghost.y }}>
          <CardArt cardId={ghost.card.card_id} name={ghost.card.name} variant="small" />
        </div>
      )}

      {zoom && (
        <CardDetail oracleId={zoom.o} cardId={zoom.c} onClose={() => setZoom(null)} />
      )}

      <div className="tile">
        <h3>Log</h3>
        {log.length === 0
          ? <p className="muted">Nothing yet.</p>
          : <ul className="muted" style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {log.map((line, i) => <li key={i}>{line}</li>)}
            </ul>}
      </div>
    </>
  )
}

function ZonePanel(
  { zone, cards, selected, onSelect, onZoom, dragProps, dropping, anyDrag }: {
    zone: Zone; cards: CardState[]; selected: string | null
    onSelect: (uid: string | null) => void
    onZoom: (card: CardState) => void
    dragProps: (card: CardState, from: Zone, onTapLike: () => void) => Record<string, unknown>
    dropping: boolean
    anyDrag: boolean
  },
) {
  if (zone !== 'hand' && cards.length === 0 && !anyDrag) return null
  return (
    <section className={`tile dropzone${dropping ? ' dropping' : ''}`}
             data-zone={zone} aria-labelledby={`zone-${zone}`}>
      <div className="spread">
        <h3 id={`zone-${zone}`} style={{ margin: 0 }}>{ZONE_LABELS[zone]}</h3>
        <span className="muted">{cards.length} card{cards.length === 1 ? '' : 's'}</span>
      </div>
      <div>
      {cards.length === 0 && (
        <p className="muted" style={{ margin: '10px 0' }}>
          {anyDrag ? `Drop here to send it to the ${ZONE_LABELS[zone].toLowerCase()}.` : 'Empty.'}
        </p>
      )}
      <div className="cardgrid" style={{ marginTop: 12, ['--card-w' as string]: '132px' }}>
        {cards.map(card => (
          <div className="cardtile" key={card.uid}>
            <div
              className="open draggable"
              role="button"
              tabIndex={0}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onZoom(card) } }}
              style={{
                outline: selected === card.uid ? '3px solid var(--tint-mag)' : undefined,
              }}
              {...dragProps(card, zone, () => onZoom(card))}>
              <CardArt cardId={card.card_id} name={card.name}
                       typeLine={card.type_line} variant="small" />
              <span className="vh">Open {card.name}</span>
            </div>
            <div className="steppers">
              <button className="btn btn-sm"
                      aria-pressed={selected === card.uid}
                      onClick={() => onSelect(selected === card.uid ? null : card.uid)}>
                {selected === card.uid ? 'Cancel' : 'Move'}
                <span className="vh"> {card.name}</span>
              </button>
            </div>
          </div>
        ))}
      </div>
      </div>
    </section>
  )
}

/** The battlefield as a free-form canvas, the way Archidekt plays it: drag a
 *  permanent anywhere and it stays there, click it to tap.
 *
 *  Dragging alone would fail WCAG 2.5.7, so every card also has buttons, and
 *  the arrow keys nudge whichever card is selected. Pointer events rather than
 *  HTML5 drag-and-drop, because HTML5 drag does not work on touch. */
function Battlefield(
  { cards, selected, onSelect, onPlace, onTap, onCounter, onZoom, onMove, onTidy,
    onWidth, dragProps, dragging, dropping }: {
    cards: CardState[]
    selected: string | null
    onSelect: (uid: string | null) => void
    onPlace: (uid: string, x: number, y: number) => void
    onTap: (uid: string) => void
    onCounter: (uid: string, delta: number) => void
    onZoom: (card: CardState) => void
    onMove: (uid: string, to: Zone) => void
    onTidy: () => void
    onWidth: (w: number) => void
    dragProps: (card: CardState, from: Zone, onTapLike: () => void) => Record<string, unknown>
    dragging: boolean
    dropping: boolean
  },
) {
  const field = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!field.current) return
    const report = () => field.current && onWidth(field.current.clientWidth)
    report()
    const ro = new ResizeObserver(report)
    ro.observe(field.current)
    return () => ro.disconnect()
  }, [onWidth])


  // Arrow keys nudge the selected card: the pointer-free path to the same
  // outcome as dragging.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!selected) return
      const el = e.target as HTMLElement
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return
      const step = e.shiftKey ? 40 : 10
      const deltas: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0], ArrowRight: [step, 0],
        ArrowUp: [0, -step], ArrowDown: [0, step],
      }
      const d = deltas[e.key]
      if (!d) return
      const card = cards.find(c => c.uid === selected)
      if (!card) return
      e.preventDefault()
      const box = field.current?.getBoundingClientRect()
      onPlace(card.uid,
        clamp((card.x ?? 0) + d[0], (box?.width ?? 900) - CARD_W),
        clamp((card.y ?? 0) + d[1], (box?.height ?? 500) - CARD_H))
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [selected, cards, onPlace])

  const chosen = cards.find(c => c.uid === selected)

  return (
    <section className="tile" aria-labelledby="zone-battlefield">
      <div className="spread">
        <h3 id="zone-battlefield" style={{ margin: 0 }}>Battlefield</h3>
        <div className="row" style={{ gap: 8 }}>
          <span className="muted">{cards.length} permanent{cards.length === 1 ? '' : 's'}</span>
          <button className="btn btn-sm" onClick={onTidy} disabled={cards.length === 0}>
            Tidy up
          </button>
        </div>
      </div>
      <p className="muted" style={{ margin: '6px 0 10px' }}>
        Drag cards anywhere. Click one to tap it. Arrow keys nudge whatever is
        selected — hold Shift to move further.
      </p>

      <div className={`field${dropping ? ' dropping' : ''}`} ref={field} data-zone="battlefield">
        {cards.length === 0 && (
          <p className="muted" style={{ padding: 16 }}>
            Nothing in play. Select a card in hand and send it to the battlefield.
          </p>
        )}
        {cards.map(card => (
          <div
            key={card.uid}
            className={`fieldcard${card.tapped ? ' tapped' : ''}` +
                       `${selected === card.uid ? ' chosen' : ''}`}
            style={{ left: card.x ?? 12, top: card.y ?? 12 }}
            {...dragProps(card, 'battlefield', () => onTap(card.uid))}
          >
            <CardArt cardId={card.card_id} name={card.name}
                     typeLine={card.type_line} variant="small" />
            {card.counters > 0 && <span className="fieldcounter">+{card.counters}</span>}
            <span className="vh">
              {card.name}{card.tapped ? ', tapped' : ', untapped'}
              {card.counters ? `, ${card.counters} counters` : ''}
            </span>
          </div>
        ))}
      </div>

      {chosen && !dragging && (
        <div style={{ marginTop: 12 }} role="group"
             aria-label={`Actions for ${chosen.name}`}>
          <div className="row">
            <strong style={{ flex: '1 1 160px' }}>{chosen.name}</strong>
            <button className="btn btn-sm" onClick={() => onTap(chosen.uid)}>
              {chosen.tapped ? 'Untap' : 'Tap'}
            </button>
            <button className="btn btn-sm" onClick={() => onCounter(chosen.uid, 1)}
                    aria-label={`Add a counter to ${chosen.name}`}>+1 counter</button>
            <button className="btn btn-sm" onClick={() => onCounter(chosen.uid, -1)}
                    aria-label={`Remove a counter from ${chosen.name}`}>−1 counter</button>
            <button className="btn btn-sm" onClick={() => onZoom(chosen)}>Read card</button>
            <button className="btn btn-sm btn-quiet" onClick={() => onSelect(null)}>
              Deselect <kbd>Esc</kbd>
            </button>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <span className="muted">Send to:</span>
            {(['hand', 'graveyard', 'exile', 'library', 'command'] as Zone[]).map(z => (
              <button key={z} className="btn btn-sm" onClick={() => onMove(chosen.uid, z)}>
                {ZONE_LABELS[z]}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

function clamp(value: number, max: number): number {
  return Math.max(0, Math.min(value, Math.max(0, max)))
}
