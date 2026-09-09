import type { DeckStats } from '../api'

/** Mana curve as inline SVG.
 *
 * Carries role="img" and a description of the actual data, because a chart
 * that only exists visually fails for anyone using a screen reader — and the
 * accessibility floor applies to screens only one person will ever see.
 */
export default function ManaCurve({ curve }: { curve: DeckStats['curve'] }) {
  const keys = ['0', '1', '2', '3', '4', '5', '6', '7+']
  const values = keys.map(k => curve[k] ?? 0)
  const max = Math.max(1, ...values)
  const total = values.reduce((a, b) => a + b, 0)

  if (total === 0) return <p className="muted">No non-land cards yet.</p>

  const description =
    'Mana curve, excluding lands: ' +
    keys.map((k, i) => `${values[i]} card${values[i] === 1 ? '' : 's'} at ${k}`).join(', ') + '.'

  const w = 220, h = 90, gap = 5
  const barW = (w - gap * (keys.length - 1)) / keys.length

  return (
    <>
      <svg viewBox={`0 0 ${w} ${h + 16}`} width="100%" height="110"
           role="img" aria-label={description}>
        {values.map((v, i) => {
          const barH = Math.round((v / max) * h)
          return (
            <g key={keys[i]}>
              <rect x={i * (barW + gap)} y={h - barH} width={barW} height={barH}
                    rx="3" fill="var(--magenta)" opacity={v ? 0.9 : 0.18} />
              <text x={i * (barW + gap) + barW / 2} y={h + 12} textAnchor="middle"
                    fontSize="9" fill="var(--ink-low)">{keys[i]}</text>
            </g>
          )
        })}
      </svg>
      {/* The same numbers as text, so the chart is never the only source. */}
      <p className="muted" style={{ marginTop: 4 }}>
        {keys.map((k, i) => values[i] ? `${k}: ${values[i]}` : null)
             .filter(Boolean).join('  ·  ')}
      </p>
    </>
  )
}
