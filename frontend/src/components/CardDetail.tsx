import { useEffect, useRef, useState } from 'react'
import { api, type CardDetail as Detail } from '../api'
import { CardArt, Pips } from './CardArt'

const money = (n: number | null | undefined) => n == null ? '—' : `$${n.toFixed(2)}`

/** Full-height card image with the printed text written out beside it.
 *
 * Side by side on a wide screen, stacked on a narrow one — the image is the
 * point on a tablet, and the text is what makes it readable at all on a phone.
 */
export default function CardDetail(
  { oracleId, cardId, onClose }: {
    oracleId: string; cardId?: string | null; onClose: () => void
  },
) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [face, setFace] = useState(0)
  const [error, setError] = useState('')
  const closeRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setDetail(null); setFace(0); setError('')
    void api.cardDetail(oracleId, cardId)
      .then(setDetail)
      .catch(() => setError('Could not load that card.'))
  }, [oracleId, cardId])

  useEffect(() => { closeRef.current?.focus() }, [detail])

  // Esc closes; Tab is kept inside the dialog while it is open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); return }
      if (e.key !== 'Tab' || !dialogRef.current) return
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
      if (focusable.length === 0) return
      const first = focusable[0], last = focusable[focusable.length - 1]
      if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
    }
    addEventListener('keydown', onKey, true)
    return () => removeEventListener('keydown', onKey, true)
  }, [onClose])

  const faces = detail?.faces ?? []
  const shown = faces.length > 0 ? faces[face] : null
  const name = shown?.name ?? detail?.name ?? ''
  const typeLine = shown?.type_line ?? detail?.type_line
  const text = shown?.oracle_text ?? detail?.oracle_text
  const cost = shown?.mana_cost ?? detail?.mana_cost
  const power = shown?.power ?? detail?.power
  const toughness = shown?.toughness ?? detail?.toughness
  const loyalty = shown?.loyalty ?? detail?.loyalty

  return (
    <div className="cardmodal" role="dialog" aria-modal="true"
         aria-label={detail ? `${detail.name} card details` : 'Card details'}
         onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="cardmodal-inner" ref={dialogRef}>
        {error && <p className="err" style={{ padding: 20 }}>{error}</p>}
        {!detail && !error && <p className="muted" style={{ padding: 20 }}>Loading…</p>}

        {detail && (
          <>
            <div className="cardmodal-art">
              <CardArt cardId={detail.card_id} name={name} typeLine={typeLine}
                       variant="normal" />
              {faces.length > 1 && (
                <button className="btn" style={{ marginTop: 12 }}
                        onClick={() => setFace(f => (f + 1) % faces.length)}>
                  Show other face — {faces[(face + 1) % faces.length].name}
                </button>
              )}
            </div>

            <div className="cardmodal-text">
              <div className="spread">
                <h2 style={{ margin: 0 }}>{name}</h2>
                <button className="btn btn-sm" ref={closeRef} onClick={onClose}>
                  Close <kbd>Esc</kbd>
                </button>
              </div>

              <p className="cardline">
                {cost && <strong>{cost}</strong>}
                <Pips identity={detail.color_identity} />
                {detail.mana_value != null && (
                  <span className="muted">MV {detail.mana_value}</span>
                )}
              </p>

              <p className="cardtype">{typeLine}</p>

              {/* Printed rules text, line for line. */}
              {text
                ? <div className="cardtext">
                    {text.split('\n').map((line, i) => <p key={i}>{line}</p>)}
                  </div>
                : <p className="muted">No rules text.</p>}

              {(power != null || loyalty != null) && (
                <p className="ptline">
                  {power != null && (
                    <>
                      <span className="vh">Power and toughness: </span>
                      <strong>{power} / {toughness}</strong>
                    </>
                  )}
                  {loyalty != null && (
                    <><span className="vh">Loyalty: </span><strong>Loyalty {loyalty}</strong></>
                  )}
                </p>
              )}

              {shown?.flavor_text ?? detail.flavor_text ? (
                <p className="flavor">{shown?.flavor_text ?? detail.flavor_text}</p>
              ) : null}

              <dl className="factgrid">
                <div><dt>Set</dt><dd>{detail.set_name} <span className="muted">
                  {detail.set_code?.toUpperCase()} {detail.collector_num}</span></dd></div>
                <div><dt>Rarity</dt><dd>{detail.rarity}</dd></div>
                <div><dt>Price</dt><dd>{money(detail.price_usd)}
                  {detail.price_usd_foil != null &&
                    <span className="muted"> · foil {money(detail.price_usd_foil)}</span>}</dd></div>
                <div><dt>Copies held</dt><dd>
                  {detail.owned > 0
                    ? <span className="pill pill-own">✓ Own {detail.owned}</span>
                    : <span className="pill pill-none">✕ None</span>}</dd></div>
                {detail.artist && <div><dt>Artist</dt><dd>{detail.artist}</dd></div>}
                {detail.legalities.commander && (
                  <div><dt>Commander</dt><dd>{detail.legalities.commander}</dd></div>
                )}
              </dl>

              {detail.copies.length > 0 && (
                <>
                  <p className="subhead" style={{ marginTop: 16 }}>Where your copies are</p>
                  <ul className="wherelist">
                    {detail.copies.map((c, i) => (
                      <li key={i}>
                        <strong>{c.quantity}×</strong> {c.set_code?.toUpperCase()} {c.collector_num} ·{' '}
                        {c.finish === 'normal' ? 'Normal' : c.finish === 'foil' ? '★ Foil' : '✦ Etched'}
                        {c.condition && ` · ${c.condition}`} · <strong>{c.location}</strong>
                        {c.lent_to && <span className="pill pill-warn"> → lent to {c.lent_to}</span>}
                      </li>
                    ))}
                  </ul>
                </>
              )}

              {detail.decks.length > 0 && (
                <>
                  <p className="subhead" style={{ marginTop: 14 }}>In decks</p>
                  <p style={{ margin: 0, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {detail.decks.map(d => (
                      <span className="badge" key={d.id}>
                        {d.name} ×{d.quantity}{d.wishlist ? ' (wishlist)' : ''}
                      </span>
                    ))}
                  </p>
                </>
              )}

              {detail.tokens.length > 0 && (
                <>
                  <p className="subhead" style={{ marginTop: 14 }}>Creates</p>
                  <p className="muted" style={{ margin: 0 }}>
                    {detail.tokens.map(t => t.name).join(', ')}
                  </p>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
