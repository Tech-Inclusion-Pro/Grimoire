/* Rail icons: 1.6px outline strokes, one unified colour across every item.
   Decorative only — every tab has a visible text label beside it. */
const common = {
  width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none',
  stroke: 'var(--icon)', strokeWidth: 1.6,
  strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true, focusable: false,
}

export const DeckIcon = () => (
  <svg {...common}><rect x="4" y="3" width="12" height="16" rx="2" />
    <path d="M8 21h10a2 2 0 0 0 2-2V8" /></svg>
)
export const CollectionIcon = () => (
  <svg {...common}><rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M3 10h18M9 4v16" /></svg>
)
export const ShelfIcon = () => (
  <svg {...common}><path d="M4 6h16M4 12h16M4 18h16" />
    <circle cx="8" cy="6" r="1.4" /><circle cx="14" cy="12" r="1.4" />
    <circle cx="10" cy="18" r="1.4" /></svg>
)
export const SearchIcon = () => (
  <svg {...common}><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.5 4.5" /></svg>
)
