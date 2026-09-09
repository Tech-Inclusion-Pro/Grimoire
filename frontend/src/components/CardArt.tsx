import { useState } from 'react'

/** A card image, served from Grimoire's own cache.
 *
 * Never cropped, distorted, recoloured or watermarked — the Scryfall licence
 * forbids all four, which is also why the grid gives each card its full frame
 * rather than tightening it for density. `art_crop` is Scryfall's own variant,
 * which is a different thing from us cropping their art.
 */
export function CardArt(
  { cardId, name, typeLine, variant = 'normal', className, style }: {
    cardId: string | null
    name: string
    typeLine?: string | null
    variant?: 'small' | 'normal' | 'art_crop'
    className?: string
    style?: React.CSSProperties
  },
) {
  const [failed, setFailed] = useState(false)

  if (!cardId || failed) {
    return (
      <div className={`art-missing ${className ?? ''}`} style={style}>
        <span>{name}</span>
      </div>
    )
  }
  return (
    <img
      className={className}
      style={style}
      src={`/api/images/${cardId}/${variant}`}
      // Alt text built from name and type line, per the accessibility floor.
      alt={typeLine ? `${name} — ${typeLine}` : name}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  )
}

const COLOR_WORDS: Record<string, string> = {
  W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colourless',
}

/** Mana pips. Each carries its letter, because colour must never be the only
 *  thing distinguishing one from another. */
export function Pips({ identity }: { identity: string }) {
  const letters = identity ? [...identity] : ['C']
  const words = letters.map(c => COLOR_WORDS[c] ?? c).join(', ')
  return (
    <span className="pips" role="img" aria-label={`Colours: ${words}`}>
      {letters.map(c => (
        <span key={c} className={`pip pip-${c}`} aria-hidden="true">{c}</span>
      ))}
    </span>
  )
}
