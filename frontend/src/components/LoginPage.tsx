import { useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'

export default function LoginPage({ onSignedIn }: { onSignedIn: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.login(username, password)
      setPassword('')
      onSignedIn()
    } catch (err) {
      setPassword('')
      setError(err instanceof ApiError && err.status === 429
        ? 'Too many attempts. Wait a few minutes.'
        : 'That username and password did not match.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="main" style={{ maxWidth: 440, margin: '10vh auto' }}>
      <div className="tile">
        <h1>Grimoire</h1>
        <p className="lede">Sign in to reach the collection.</p>
        <form onSubmit={submit}>
          <div style={{ marginBottom: 14 }}>
            <label htmlFor="username">Username</label>
            <input id="username" value={username} required autoComplete="username"
              autoCapitalize="none" spellCheck={false} style={{ width: '100%' }}
              onChange={e => setUsername(e.target.value)} />
          </div>
          <div style={{ marginBottom: 16 }}>
            <label htmlFor="password">Password</label>
            <input id="password" type="password" value={password} required
              autoComplete="current-password" style={{ width: '100%' }}
              onChange={e => setPassword(e.target.value)} />
          </div>
          <p role="alert" className="err" style={{ minHeight: '1.4em', margin: '0 0 12px' }}>
            {error}
          </p>
          <button className="btn btn-primary" style={{ width: '100%' }} disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </main>
  )
}
