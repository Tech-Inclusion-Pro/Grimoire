import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type JournalData, type ModelOption, type Overview,
         type VaultResult } from '../api'

export default function Journal({ deckId, deckName }: { deckId: number; deckName: string }) {
  const [data, setData] = useState<JournalData | null>(null)
  const [body, setBody] = useState('')
  const [result, setResult] = useState('')
  const [busy, setBusy] = useState(false)
  const [vault, setVault] = useState<VaultResult | null>(null)

  const load = useCallback(() => { void api.journal(deckId).then(setData) }, [deckId])
  useEffect(load, [load])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!body.trim()) return
    setBusy(true)
    try {
      // Saved on submit, never on keystroke: rapid small writes into an
      // iCloud-synced folder produce conflict copies.
      setVault(await api.addEntry(deckId, body, result || null))
      setBody('')
      setResult('')
      load()
    } finally { setBusy(false) }
  }

  if (!data) return <div className="tile"><p className="muted">Loading…</p></div>

  const { entries, record } = data

  return (
    <>
      <div className="tile">
        <div className="spread">
          <h2 style={{ margin: 0 }}>Journal</h2>
          <p style={{ margin: 0, display: 'flex', gap: 8 }}>
            <span className="pill pill-own">✓ {record.wins} won</span>
            <span className="pill pill-warn">✕ {record.losses} lost</span>
            {record.unrecorded > 0 && (
              <span className="pill pill-none">— {record.unrecorded} unrecorded</span>
            )}
          </p>
        </div>
        <p className="muted" style={{ marginTop: 8 }}>
          Written to <code>MTG/Decks/{deckName}.md</code> in your vault on save.
          Anything you write in that file yourself is never overwritten.
        </p>

        <form onSubmit={submit} style={{ marginTop: 14 }}>
          <label htmlFor="entry">What happened? What did it feel like to play?</label>
          <textarea id="entry" rows={4} value={body} onChange={e => setBody(e.target.value)}
            placeholder="Marwyn got huge because nobody wanted to spend removal on a three-drop." />
          <div className="row" style={{ marginTop: 12 }}>
            <div>
              <label htmlFor="result">Result</label>
              <select id="result" value={result} onChange={e => setResult(e.target.value)}>
                <option value="">Not recorded</option>
                <option value="win">Win</option>
                <option value="loss">Loss</option>
              </select>
            </div>
            <button className="btn btn-primary" disabled={busy || !body.trim()}>
              Save entry
            </button>
          </div>
        </form>

        {vault && <VaultNote result={vault} />}
      </div>

      <OverviewPanel deckId={deckId} entryCount={entries.length}
                     minEntries={data.min_entries} onWritten={load} />

      <div className="tile">
        <h3>Entries</h3>
        {entries.length === 0 && (
          <p className="muted">Nothing written yet. The overview reads these, not the decklist.</p>
        )}
        {[...entries].reverse().map(e => (
          <article key={e.id} style={{ borderTop: '1px solid rgba(255,255,255,.07)',
                                       padding: '12px 0' }}>
            <div className="spread">
              <p className="muted" style={{ margin: 0 }}>
                {e.at.slice(0, 16).replace('T', ' ')}
                {e.result === 'win' && <> · <span className="ok">Win</span></>}
                {e.result === 'loss' && <> · <span className="err">Loss</span></>}
              </p>
              <button className="btn btn-sm btn-quiet" onClick={async () => {
                if (!confirm('Delete this entry? It will be removed from the vault note too.')) return
                setVault(await api.deleteEntry(e.id))
                load()
              }}>Delete<span className="vh"> entry from {e.at.slice(0, 10)}</span></button>
            </div>
            <p style={{ margin: '6px 0 0', whiteSpace: 'pre-wrap' }}>{e.body}</p>
          </article>
        ))}
      </div>
    </>
  )
}

function VaultNote({ result }: { result: VaultResult }) {
  if (result.vault_error) {
    return (
      <p className="err" role="alert" style={{ marginTop: 12 }}>
        Saved here, but not written to the vault: {result.vault_error}
      </p>
    )
  }
  if (!result.vault) return null
  return (
    <p className="muted" role="status" style={{ marginTop: 12 }}>
      ✓ Written to the vault — {result.vault.entries} entr
      {result.vault.entries === 1 ? 'y' : 'ies'}.
    </p>
  )
}

function OverviewPanel(
  { deckId, entryCount, minEntries, onWritten }:
  { deckId: number; entryCount: number; minEntries: number; onWritten: () => void },
) {
  const [models, setModels] = useState<ModelOption[]>([])
  const [chosen, setChosen] = useState('')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void api.models().then(r => {
      setModels(r.models)
      setChosen(r.models.find(m => m.id.startsWith('qwen2.5:3b'))?.id
                ?? r.models[0]?.id ?? '')
    })
  }, [])

  const model = models.find(m => m.id === chosen)
  const enough = entryCount >= minEntries

  return (
    <div className="tile">
      <h3>Overview</h3>
      <p className="muted">
        Generated from your journal entries, never from the decklist — an
        overview that read the card list would produce generic Commander advice
        you did not need. Every claim should trace to something you wrote.
      </p>

      {!enough ? (
        <p className="muted" style={{ marginTop: 12 }}>
          {entryCount} of {minEntries} entries so far. It stays quiet until there
          is a pattern to describe.
        </p>
      ) : (
        <>
          <div className="row" style={{ marginTop: 14 }}>
            <div>
              <label htmlFor="model">Generate with</label>
              <select id="model" value={chosen} onChange={e => setChosen(e.target.value)}>
                {models.map(m => (
                  <option key={m.id} value={m.id} disabled={!m.available}>{m.label}</option>
                ))}
              </select>
            </div>
            <button className="btn btn-primary" disabled={busy || !model?.available}
              onClick={async () => {
                setBusy(true); setError('')
                try {
                  const r = await api.generateOverview(deckId, chosen)
                  setOverview(r.overview)
                  onWritten()
                } catch (e) {
                  setError(e instanceof ApiError ? e.message : String(e))
                } finally { setBusy(false) }
              }}>
              {busy ? 'Writing…' : 'Generate overview'}
            </button>
          </div>

          {/* The data-flow consequence sits next to the button, not in a
              settings page: the journal is the most personal data here. */}
          {model && (
            <p className="muted" style={{ marginTop: 10 }}>
              <strong style={{ color: 'var(--ink)' }}>{model.where}.</strong>{' '}
              {model.consequence}
              {model.caveat && <> <span className="pill pill-warn">! {model.caveat}</span></>}
            </p>
          )}
        </>
      )}

      {error && <p className="err" role="alert" style={{ marginTop: 12 }}>{error}</p>}

      {overview && (
        <div style={{ marginTop: 16 }} role="status">
          <p className="muted">
            Written by {overview.model} from {overview.entries_used} entries, and
            saved into the vault note.
          </p>
          {Object.entries(overview.sections).map(([title, text]) => text.trim() && (
            <section key={title} style={{ marginTop: 12 }}>
              <h4 style={{ margin: '0 0 4px', fontSize: 14 }}>{title}</h4>
              <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{text}</p>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
