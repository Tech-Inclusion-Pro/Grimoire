/** Typed access to the Grimoire API.
 *
 * Errors carry the HTTP status because the difference matters: a 400 from the
 * search endpoint is the parser explaining what is wrong with the query, and
 * its message is written to be shown to the user verbatim.
 */

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
  /** True when the server is telling the user how to fix their input. */
  get isUserFixable() { return this.status === 400 }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch { /* not JSON */ }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  const type = res.headers.get('content-type') ?? ''
  return (type.includes('json') ? await res.json() : await res.text()) as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// ---------- shapes ----------

export interface AuthStatus { enabled: boolean; authenticated: boolean; username: string | null }

export interface Stats {
  printings: number; distinct_cards: number; copies_owned: number
  distinct_owned: number; collection_value: number; purchase_total: number
  lent_out: number; decks: number; locations: number
  bulk_updated_at: string | null; bulk_imported_at: string | null
}

export interface CardRow {
  oracle_id: string; card_id: string | null; name: string; type_line: string | null
  mana_cost: string | null; mana_value: number | null; color_identity: string
  printings: number; owned: number; foils: number
  image_normal: string | null; price_usd: number | null; value: number | null
  locations: string | null
}

export interface SearchResult {
  total: number; limit: number; offset: number
  terms: string[]; results: CardRow[]
}

export interface Printing {
  id: string; name: string; set_code: string; set_name: string
  collector_num: string; rarity: string; price_usd: number | null
  price_usd_foil: number | null; image_normal: string | null
  released_at: string; owned: number
}

export interface DeckSummary {
  id: number; name: string; format: string | null
  banner_card_id: string | null; colors: string
  commander_oracle_id: string | null; commander: string | null
  bracket_claimed: number | null; created_at: string; updated_at: string
  cards: number; wishlist: number
}

export interface DeckCard {
  oracle_id: string; card_id: string | null; image_art_crop: string | null
  quantity: number; category: string | null; tag: string | null
  wishlist: number; print_pref: string | null
  name: string; mana_cost: string | null; mana_value: number | null
  type_line: string | null; oracle_text: string | null
  color_identity: string; colors: string; layout: string
  image_normal: string | null; price_usd: number | null; owned: number
}

export interface Conflict {
  oracle_id: string; name: string; needed: number; owned: number; used_elsewhere: number
}

export interface DeckStats {
  cards: number; distinct: number; buildable_now: number; buildable_pct: number
  wishlist: number; missing: number
  curve: Record<string, number>; colors: Record<string, number>
  categories: Record<string, number>; value: number; conflicts: Conflict[]
}

export interface DeckDetail {
  deck: DeckSummary; cards: DeckCard[]; stats: DeckStats
  tokens: { name: string; type_line: string | null }[]
}

export interface BulkResult {
  resolved: { quantity: number; name: string; matched_name: string; owned: number; category: string }[]
  unresolved: { quantity: number; name: string }[]
  added?: number
  stats?: DeckStats
}

export interface JournalEntry {
  id: number; at: string; body: string; result: 'win' | 'loss' | null
}

export interface Record_ { wins: number; losses: number; unrecorded: number }

export interface JournalData {
  entries: JournalEntry[]; record: Record_; min_entries: number
}

export interface ModelOption {
  id: string; label: string; where: string
  consequence: string; caveat: string | null; available: boolean
}

export interface Overview {
  sections: Record<string, string>; model: string; at: string; entries_used: number
}

export interface VaultResult {
  vault?: { path: string; entries: number; overview_written: boolean }
  vault_error?: string
}

export interface EmbedStatus {
  embedded: number; owned: number; stale: number
  built_at: string | null; model: string; ready: boolean
}

export interface SynergyHit {
  oracle_id: string; card_id: string | null; name: string; mana_cost: string | null
  mana_value: number | null; type_line: string | null; color_identity: string
  owned: number; price_usd: number | null
  score: number; match_pct: number; strength: 'strong' | 'fair' | 'weak'
  roles: string[]; reason: string; weakness: string | null
  committed_to: string[]
}

export interface SynergyResult {
  goal: string; goal_roles: string[]; searched: number
  results: SynergyHit[]; model: string
}

export interface ShelfItem {
  oracle_id: string; card_id: string | null; added_at: string; note: string | null
  name: string; mana_cost: string | null; mana_value: number | null
  type_line: string | null; color_identity: string
  price_usd: number | null; owned: number
}

export interface Package { id: number; name: string; cards: number }

export interface PlaytestCard {
  uid: string; oracle_id: string; card_id: string | null
  name: string; mana_cost: string | null; mana_value: number | null
  type_line: string | null; oracle_text: string | null; color_identity: string
}

export interface PlaytestDeck {
  deck: { id: number; name: string; format: string | null }
  library: PlaytestCard[]; command_zone: PlaytestCard[]; starting_life: number
}

export interface CardFace {
  face_index: number; name: string | null; mana_cost: string | null
  type_line: string | null; oracle_text: string | null
  power: string | null; toughness: string | null; loyalty: string | null
  flavor_text: string | null
}

export interface CardCopy {
  quantity: number; finish: string; condition: string | null; language: string | null
  lent_to: string | null
  location: string; set_code: string; set_name: string; collector_num: string
}

export interface CardDetail {
  oracle_id: string; card_id: string; name: string
  mana_cost: string | null; mana_value: number | null
  type_line: string | null; oracle_text: string | null
  power: string | null; toughness: string | null; loyalty: string | null
  color_identity: string; rarity: string | null
  set_code: string; set_name: string; collector_num: string
  layout: string; released_at: string
  flavor_text: string | null; artist: string | null; keywords: string[]
  legalities: Record<string, string>
  price_usd: number | null; price_usd_foil: number | null; price_usd_etched: number | null
  faces: CardFace[]; copies: CardCopy[]; owned: number
  decks: { id: number; name: string; quantity: number; wishlist: number }[]
  tokens: { name: string; type_line: string | null }[]
}

export interface SavedSearch { id: number; name: string; query: string; created_at: string | null }
export interface Location { id: number; name: string; copies: number }
export interface HistoryEntry { id: number; at: string; summary: string | null }

// ---------- endpoints ----------

export const api = {
  authStatus: () => request<AuthStatus>('/api/auth/status'),
  login: (username: string, password: string) =>
    request<{ status: string }>('/api/auth/login', json({ username, password })),
  logout: () => request<{ status: string }>('/api/auth/logout', { method: 'POST' }),

  stats: () => request<Stats>('/api/stats'),

  search: (q: string, owned: boolean, limit = 100, offset = 0) =>
    request<SearchResult>(
      `/api/cards?q=${encodeURIComponent(q)}&owned=${owned}&limit=${limit}&offset=${offset}`),
  printings: (oracleId: string) => request<Printing[]>(`/api/cards/${oracleId}/printings`),
  cardDetail: (oracleId: string, cardId?: string | null) =>
    request<CardDetail>(`/api/cards/${oracleId}/detail` +
      (cardId ? `?card_id=${encodeURIComponent(cardId)}` : '')),

  locations: () => request<Location[]>('/api/locations'),
  addCopy: (body: { card_id: string; quantity: number; finish: string; location_id: number | null }) =>
    request<unknown>('/api/copies', json(body)),

  savedSearches: () => request<SavedSearch[]>('/api/searches'),
  saveSearch: (name: string, query: string) =>
    request<SavedSearch>('/api/searches', json({ name, query })),
  deleteSearch: (id: number) => request<void>(`/api/searches/${id}`, { method: 'DELETE' }),

  decks: () => request<DeckSummary[]>('/api/decks'),
  createDeck: (body: { name: string; format: string; bracket_claimed?: number | null }) =>
    request<{ id: number; name: string }>('/api/decks', json(body)),
  deck: (id: number, q = '') =>
    request<DeckDetail>(`/api/decks/${id}?q=${encodeURIComponent(q)}`),
  deleteDeck: (id: number) => request<void>(`/api/decks/${id}`, { method: 'DELETE' }),
  editDeck: (id: number, body: {
    name?: string; format?: string
    commander_oracle_id?: string | null; bracket_claimed?: number | null
    clear_commander?: boolean
  }) => request<DeckSummary>(`/api/decks/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }),
  setCard: (id: number, body: {
    oracle_id: string; quantity: number; category?: string | null
    tag?: string | null; wishlist?: boolean | null; print_pref?: string | null
  }) => request<DeckStats>(`/api/decks/${id}/cards`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  bulkAdd: (id: number, text: string, apply: boolean) =>
    request<BulkResult>(`/api/decks/${id}/bulk`, json({ text, apply })),
  playtest: (id: number) => request<PlaytestDeck>(`/api/decks/${id}/playtest`),
  history: (id: number) => request<HistoryEntry[]>(`/api/decks/${id}/history`),
  restore: (id: number, historyId: number) =>
    request<{ restored: number; stats: DeckStats }>(
      `/api/decks/${id}/history/${historyId}/restore`, { method: 'POST' }),
  exportUrl: (id: number, fmt: string) => `/api/decks/${id}/export?fmt=${fmt}`,

  journal: (id: number) => request<JournalData>(`/api/decks/${id}/journal`),
  addEntry: (id: number, body: string, result: string | null) =>
    request<{ id: number; record: Record_ } & VaultResult>(
      `/api/decks/${id}/journal`, json({ body, result })),
  deleteEntry: (entryId: number) =>
    request<{ deleted: number } & VaultResult>(`/api/journal/${entryId}`, { method: 'DELETE' }),
  models: () => request<{ models: ModelOption[] }>('/api/journal/models'),
  generateOverview: (id: number, model: string) =>
    request<{ overview: Overview } & VaultResult>(
      `/api/decks/${id}/journal/overview`, json({ model })),
  vaultSync: (id: number) =>
    request<VaultResult>(`/api/decks/${id}/vault-sync`, { method: 'POST' }),

  embedStatus: () => request<EmbedStatus>('/api/embeddings'),
  buildEmbeddings: () =>
    request<{ owned: number; embedded: number; skipped: number }>(
      '/api/embeddings/build', { method: 'POST' }),
  synergy: (body: {
    goal: string; colors?: string | null; format?: string | null
    exclude_deck?: number | null; hide_committed?: boolean; limit?: number
  }) => request<SynergyResult>('/api/synergy', json(body)),

  shelf: () => request<{ items: ShelfItem[]; packages: Package[] }>('/api/shelf'),
  shelve: (oracle_id: string, note?: string | null) =>
    request<{ added: boolean }>('/api/shelf', json({ oracle_id, note })),
  shelfNote: (oracle_id: string, note: string | null) =>
    request<unknown>(`/api/shelf/${oracle_id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    }),
  unshelve: (oracle_id: string) =>
    request<void>(`/api/shelf/${oracle_id}`, { method: 'DELETE' }),
  shelfSend: (deck_id: number, oracle_ids: string[] | null, keep_on_shelf = false) =>
    request<{ added: number }>('/api/shelf/send', json({ deck_id, oracle_ids, keep_on_shelf })),
  createPackage: (name: string, oracle_ids: string[]) =>
    request<Package>('/api/packages', json({ name, oracle_ids })),
  packageToShelf: (id: number) =>
    request<{ added: number }>(`/api/packages/${id}/to-shelf`, { method: 'POST' }),
  deletePackage: (id: number) =>
    request<void>(`/api/packages/${id}`, { method: 'DELETE' }),
}
