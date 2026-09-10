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

// How far a pointer must travel before a press becomes a drag rather than a
// tap. Big enough to survive a shaky thumb, small enough not to feel sticky.
const DRAG_THRESHOLD = 6

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
  const shell = useRef<HTMLDivElement>(null)
  const [fullscreen, setFullscreen] = useState(false)

  // One drag system for every zone. `moved` is what separates a tap from a
  // drag; without it every attempt to pick a card up would also tap it.
  const drag = useRef<
    { uid: string; from: Zone; moved: boolean; startX: number; startY: number } | null
  >(null)
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

  // Fullscreen the play area, so a goldfish session on a tablet is not framed
  // by the browser and the nav rail.
  const toggleFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await shell.current?.requestFullscreen()
    } catch {
      // Safari on iPhone has no Fullscreen API for arbitrary elements; the
      // button simply does nothing there rather than throwing.
    }
  }, [])

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement !== null)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  const onField = useCallback(
    (uid: string) => zones?.battlefield.some(c => c.uid === uid) ?? false, [zones])

  const tapCard = useCallback((uid: string) => setZones(p => p && {
    ...p, battlefield: p.battlefield.map(c => c.uid === uid ? { ...c, tapped: !c.tapped } : c),
  }), [])

  const counterCard = useCallback((uid: string, delta: number) => setZones(p => p && {
    ...p, battlefield: p.battlefield.map(c =>
      c.uid === uid ? { ...c, counters: Math.max(0, c.counters + delta) } : c),
  }), [])

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
      else if (key === 'f') { e.preventDefault(); void toggleFullscreen() }
      else if (key === 'escape') setSelected(null)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [draw, untapAll, mulligan, toggleFullscreen])

  /** Handlers for one card. `onTapLike` is what a press that never moved
   *  means in this zone: tapping on the battlefield, reading it anywhere else. */
  function dragProps(card: CardState, from: Zone, onTapLike: () => void) {
    return {
      onPointerDown: (e: React.PointerEvent) => {
        if (e.button !== 0) return
        ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
        drag.current = {
          uid: card.uid, from, moved: false,
          startX: e.clientX, startY: e.clientY,
        }
        if (from === 'battlefield') setSelected(card.uid)
      },
      onPointerMove: (e: React.PointerEvent) => {
        const d = drag.current
        if (!d || d.uid !== card.uid) return
        if (!d.moved) {
          // Distance from where the press started, NOT e.movementX. movementX
          // is the delta since the previous event: a real mouse moved slowly
          // reports 1-2px per event and never crosses any threshold, and on
          // touch it is 0 in most browsers. Measuring per-event meant dragging
          // worked only for synthetic events that set movementX by hand.
          const travelled = Math.hypot(e.clientX - d.startX, e.clientY - d.startY)
          if (travelled < DRAG_THRESHOLD) return
          d.moved = true
        }
        setGhost({ card, x: e.clientX, y: e.clientY })
        const under = document.elementFromPoint(e.clientX, e.clientY)
        const target = under?.closest('[data-zone]')?.getAttribute('data-zone')
        setOverZone((target as Zone) ?? null)
      },
      // The browser fires this when it takes the gesture over — a scroll
      // recognised inside the hand strip, a system edge swipe, a second
      // finger. Without it the ghost sticks and the card never lands.
      onPointerCancel: () => {
        drag.current = null
        setGhost(null)
        setOverZone(null)
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
          // Position is always measured against the canvas, even when the drop
          // landed on the panel around it; clamp() then pulls it inside.
          const canvas = (holder!.matches('[data-field]')
            ? holder!
            : holder!.querySelector('[data-field]')) as HTMLElement | null
          const box = (canvas ?? holder!).getBoundingClientRect()
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
    <div ref={shell} className={fullscreen ? 'playshell fullscreen' : 'playshell'}>
      <button className="btn btn-quiet" style={{ marginBottom: 14 }}
              onClick={() => navigate({ name: 'deck', id })} hidden={fullscreen}>
        ← Back to {deckName}
      </button>

      <div className="spread">
        <div>
          <h1>Playtesting {deckName}</h1>
          <p className="lede">
            Goldfishing only — nothing here enforces rules, checks costs or
            stops an illegal play. Drag cards between zones; click one on the
            battlefield to tap it. Every card also has buttons, and the arrow
            keys nudge whatever is selected.
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
            <button className="btn" onClick={() => void toggleFullscreen()}
                    aria-pressed={fullscreen}>
              {fullscreen ? 'Exit full screen' : 'Full screen'} <kbd>F</kbd>
            </button>
          </div>
        </div>
      </div>

      {/* One playmat: battlefield, hand and the piles all in view at once, so
          a card only ever travels a short distance and nothing has to be
          scrolled to reach a drop target. */}
      <div className="playmat">
        <Battlefield
          cards={zones.battlefield}
          selected={selected}
          onWidth={w => { fieldWidth.current = w }}
          dragProps={dragProps}
          dropping={overZone === 'battlefield'}
          onPlace={placeOnField}
          onTap={tapCard}
          onTidy={() => setZones(p => p && {
            ...p, battlefield: p.battlefield.map((c, i) => ({
              ...c, ...nextSpot(p.battlefield.slice(0, i), fieldWidth.current),
            })),
          })}
        />

        <Hand cards={zones.hand} selected={selected} dragProps={dragProps}
              dropping={overZone === 'hand'} onSelect={setSelected} />

        <div className="mat-piles">
          <Pile zone="library" cards={zones.library} facedown
                dropping={overZone === 'library'} dragProps={dragProps}
                hint="Click to draw" onActivate={() => draw(1)}
                onSelect={setSelected} selected={selected} />
          {(['graveyard', 'exile', 'command'] as Zone[]).map(zone => (
            <Pile key={zone} zone={zone} cards={zones[zone]}
                  dropping={overZone === zone} dragProps={dragProps}
                  onSelect={setSelected} selected={selected} />
          ))}
        </div>
      </div>

      {/* Pinned to the bottom of the screen, not placed in the flow. In the
          flow it rendered above the playmat and was hundreds of pixels off
          the top of the viewport whenever the hand was in view, so clicking a
          card looked like it did nothing. */}
      {chosen && !ghost && (
        <div className="actionbar" role="status">
          <div className="actionbar-inner">
            <strong className="actionbar-name">{chosen.name}</strong>
            {onField(chosen.uid) && (
              <>
                <button className="btn btn-sm" onClick={() => tapCard(chosen.uid)}>
                  {chosen.tapped ? 'Untap' : 'Tap'}
                </button>
                <button className="btn btn-sm" onClick={() => counterCard(chosen.uid, 1)}
                        aria-label={`Add a counter to ${chosen.name}`}>+1</button>
                <button className="btn btn-sm" onClick={() => counterCard(chosen.uid, -1)}
                        aria-label={`Remove a counter from ${chosen.name}`}>−1</button>
              </>
            )}
            <span className="muted">Send to:</span>
            {ZONES.filter(z => !(onField(chosen.uid) && z === 'battlefield')).map(z => (
              <button key={z} className="btn btn-sm" onClick={() => move(chosen.uid, z)}>
                {ZONE_LABELS[z]}
              </button>
            ))}
            <button className="btn btn-sm"
                    onClick={() => setZoom({ o: chosen.oracle_id, c: chosen.card_id })}>
              Read card
            </button>
            <button className="btn btn-sm btn-quiet" onClick={() => setSelected(null)}>
              Cancel <kbd>Esc</kbd>
            </button>
          </div>
        </div>
      )}

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
    </div>
  )
}

/** The hand: a horizontal strip along the bottom of the playmat, scrolling
 *  sideways when it gets long rather than growing the page. */
function Hand(
  { cards, selected, dragProps, dropping, onSelect }: {
    cards: CardState[]
    selected: string | null
    dragProps: (card: CardState, from: Zone, onTapLike: () => void) => Record<string, unknown>
    dropping: boolean
    onSelect: (uid: string) => void
  },
) {
  return (
    <section className={`mat-hand${dropping ? ' dropping' : ''}`}
             data-zone="hand" aria-labelledby="zone-hand">
      <div className="mat-label">
        <h3 id="zone-hand">Hand</h3>
        <span>{cards.length}</span>
      </div>
      <div className="handrow">
        {cards.length === 0 && (
          <p className="muted" style={{ margin: 'auto 8px' }}>
            {dropping ? 'Drop to return it to hand.' : 'Empty.'}
          </p>
        )}
        {cards.map(card => (
          <div key={card.uid}
               className="handcard draggable"
               role="button"
               tabIndex={0}
               aria-label={`${card.name} — choose where to send it`}
               onKeyDown={e => {
                 if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(card.uid) }
               }}
               style={{ outline: selected === card.uid ? '3px solid var(--tint-mag)' : undefined }}
               {...dragProps(card, 'hand', () => onSelect(card.uid))}>
            <CardArt cardId={card.card_id} name={card.name}
                     typeLine={card.type_line} variant="small" />
          </div>
        ))}
      </div>
    </section>
  )
}

/** Library, graveyard, exile and command as stacked piles rather than grids.
 *  Sixteen library cards laid out individually pushed everything else off the
 *  screen; a pile with a count is how the zone reads on a table. */
function Pile(
  { zone, cards, dropping, dragProps, onSelect, selected, facedown, hint, onActivate }: {
    zone: Zone
    cards: CardState[]
    dropping: boolean
    dragProps: (card: CardState, from: Zone, onTapLike: () => void) => Record<string, unknown>
    onSelect: (uid: string) => void
    selected: string | null
    facedown?: boolean
    hint?: string
    onActivate?: () => void
  },
) {
  const [open, setOpen] = useState(false)
  const top = cards[0]

  return (
    <section className={`pile${dropping ? ' dropping' : ''}`}
             data-zone={zone} aria-labelledby={`zone-${zone}`}>
      <div className="mat-label">
        <h3 id={`zone-${zone}`}>{ZONE_LABELS[zone]}</h3>
        <span>{cards.length}</span>
      </div>

      <div className="pilebody">
        {!top && (
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            {dropping ? 'Drop here' : 'Empty'}
          </p>
        )}
        {top && facedown && (
          <button className="cardback" onClick={onActivate}
                  aria-label={`${cards.length} cards in the library. ${hint ?? ''}`}>
            <span>{cards.length}</span>
          </button>
        )}
        {top && !facedown && (
          <div className="piletop draggable" role="button" tabIndex={0}
               style={{ outline: selected === top.uid ? '3px solid var(--tint-mag)' : undefined }}
               aria-label={`${top.name}, top of ${ZONE_LABELS[zone].toLowerCase()} — choose where to send it`}
               onKeyDown={e => {
                 if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(top.uid) }
               }}
               {...dragProps(top, zone, () => onSelect(top.uid))}>
            <CardArt cardId={top.card_id} name={top.name}
                     typeLine={top.type_line} variant="small" />
          </div>
        )}
      </div>

      {hint && <p className="pilehint">{hint}</p>}
      {cards.length > 1 && !facedown && (
        <button className="btn btn-sm btn-quiet pileall" onClick={() => setOpen(o => !o)}>
          {open ? 'Hide' : `All ${cards.length}`}
        </button>
      )}

      {open && (
        <div className="pilelist">
          {cards.map(card => (
            <div key={card.uid} className="handcard draggable" role="button" tabIndex={0}
                 style={{ outline: selected === card.uid ? '3px solid var(--tint-mag)' : undefined }}
                 aria-label={`${card.name} — choose where to send it`}
                 onKeyDown={e => {
                   if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(card.uid) }
                 }}
                 {...dragProps(card, zone, () => onSelect(card.uid))}>
              <CardArt cardId={card.card_id} name={card.name}
                       typeLine={card.type_line} variant="small" />
            </div>
          ))}
        </div>
      )}
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
  { cards, selected, onPlace, onTap, onTidy, onWidth, dragProps, dropping }: {
    cards: CardState[]
    selected: string | null
    onPlace: (uid: string, x: number, y: number) => void
    onTap: (uid: string) => void
    onTidy: () => void
    onWidth: (w: number) => void
    dragProps: (card: CardState, from: Zone, onTapLike: () => void) => Record<string, unknown>
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


  return (
    <section className="mat-battlefield" data-zone="battlefield"
             aria-labelledby="zone-battlefield">
      <div className="mat-label">
        <h3 id="zone-battlefield">Battlefield</h3>
        <span>{cards.length}</span>
        <button className="btn btn-sm btn-quiet" onClick={onTidy}
                disabled={cards.length === 0} style={{ marginLeft: 'auto' }}>
          Tidy up
        </button>
      </div>

      <div className={`field${dropping ? ' dropping' : ''}`} ref={field} data-field>
        {cards.length === 0 && (
          <p className="muted" style={{ padding: 16 }}>
            Drag a card here from your hand.
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

    </section>
  )
}

function clamp(value: number, max: number): number {
  return Math.max(0, Math.min(value, Math.max(0, max)))
}
