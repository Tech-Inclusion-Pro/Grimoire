import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type AuthStatus, type Stats } from './api'
import { navigate, useRoute } from './router'
import LoginPage from './components/LoginPage'
import Decks from './components/Decks'
import Collection from './components/Collection'
import Synergy from './components/Synergy'
import Shelf from './components/Shelf'
import { CollectionIcon, DeckIcon, SearchIcon, ShelfIcon } from './components/Icons'

type TabId = 'decks' | 'collection' | 'synergy' | 'shelf'

const TABS: { id: TabId; label: string; icon: () => React.ReactElement; ready: boolean }[] = [
  { id: 'decks', label: 'Decks', icon: DeckIcon, ready: true },
  { id: 'collection', label: 'Collection', icon: CollectionIcon, ready: true },
  { id: 'synergy', label: 'Find synergies', icon: SearchIcon, ready: true },
  { id: 'shelf', label: 'Shelf', icon: ShelfIcon, ready: true },
]

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const route = useRoute()
  const tab: TabId =
    route.name === 'deck' || route.name === 'newDeck' || route.name === 'playtest'
      ? 'decks' : route.name
  const setTab = (id: TabId) => navigate({ name: id } as never)
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])

  const load = useCallback(async () => {
    const status = await api.authStatus()
    setAuth(status)
    if (status.authenticated) setStats(await api.stats())
  }, [])

  useEffect(() => { void load() }, [load])

  if (!auth) return <main className="main"><p className="muted">Loading…</p></main>
  if (auth.enabled && !auth.authenticated) return <LoginPage onSignedIn={load} />

  // WAI-ARIA tabs: arrow keys move between tabs, Home and End jump to the ends.
  function onTabKey(e: React.KeyboardEvent, index: number) {
    const keys: Record<string, number> = {
      ArrowDown: index + 1, ArrowRight: index + 1,
      ArrowUp: index - 1, ArrowLeft: index - 1,
      Home: 0, End: TABS.length - 1,
    }
    const next = keys[e.key]
    if (next === undefined) return
    e.preventDefault()
    const wrapped = (next + TABS.length) % TABS.length
    setTab(TABS[wrapped].id)
    tabRefs.current[wrapped]?.focus()
  }

  return (
    <>
      <a className="skip" href="#main">Skip to main content</a>
      <div className="shell">
        <nav className="rail" aria-label="Grimoire sections">
          <p className="brand">Grimoire</p>
          <p className="brandsub">Your library, your decks, your notes.</p>

          <div role="tablist" aria-label="Grimoire sections" aria-orientation="vertical">
            {TABS.map((t, i) => (
              <button
                key={t.id}
                ref={el => { tabRefs.current[i] = el }}
                role="tab"
                id={`tab-${t.id}`}
                className="tab"
                aria-controls={`panel-${t.id}`}
                aria-selected={tab === t.id}
                tabIndex={tab === t.id ? 0 : -1}
                onClick={() => setTab(t.id)}
                onKeyDown={e => onTabKey(e, i)}
              >
                <t.icon />
                {t.label}
                {!t.ready && <span className="pill pill-none" style={{ marginLeft: 'auto' }}>Soon</span>}
              </button>
            ))}
          </div>

          {stats && (
            <p className="railfoot">
              <span className="live" aria-hidden="true" />
              {stats.copies_owned.toLocaleString()} cards on hand
              {' · '}{stats.distinct_owned.toLocaleString()} distinct
              <br />
              Card data from {stats.bulk_updated_at?.slice(0, 10) ?? 'unknown'}.
              <br />
              {auth.enabled
                ? <>Signed in as {auth.username}. <button className="btn btn-sm btn-quiet"
                    style={{ marginTop: 8 }}
                    onClick={async () => { await api.logout(); location.reload() }}>Log out</button></>
                : <span className="err">No password set — anyone on the tailnet can read this.</span>}
            </p>
          )}
        </nav>

        <main className="main" id="main">
          {TABS.map(t => (
            <section
              key={t.id}
              role="tabpanel"
              id={`panel-${t.id}`}
              aria-labelledby={`tab-${t.id}`}
              tabIndex={0}
              hidden={tab !== t.id}
            >
              {tab === t.id && <Panel id={t.id} onCollectionChanged={load} />}
            </section>
          ))}
        </main>
      </div>
    </>
  )
}

function Panel({ id, onCollectionChanged }: { id: TabId; onCollectionChanged: () => void }) {
  if (id === 'decks') return <Decks />
  if (id === 'collection') return <Collection onChanged={onCollectionChanged} />
  if (id === 'synergy') return <Synergy />
  return <Shelf />
}
