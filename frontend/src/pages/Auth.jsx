import { useState } from 'react'
import { api, setToken } from '../api'
import { Field, Select, useAsync } from '../components/common'
import { useT } from '../i18n'

// Sign-in screen for BOOKMAKER_AUTH_MODE=accounts (first-run setup included) and =token.
export function Login({ status, onDone }) {
  const { t, notify } = useT()
  const [u, setU] = useState('')
  const [p, setP] = useState('')
  const [tok, setTok] = useState('')
  const [busy, setBusy] = useState(false)
  const setup = status?.needs_setup
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      if (status?.mode === 'token') {
        setToken(tok)
        await api('/api/projects')
      } else {
        const r = await api(setup ? '/api/auth/setup' : '/api/auth/login', { method: 'POST', body: { username: u, password: p } })
        setToken(r.token)
      }
      onDone()
    } catch (err) {
      if (status?.mode === 'token') setToken('')
      notify(err.message, 'bad')
    } finally { setBusy(false) }
  }
  return (
    <div className="page login">
      <form className="card form narrow" onSubmit={submit} data-testid="login-form">
        <h2>🔐 {setup ? t('setupTitle') : t('login')}</h2>
        {setup && <p className="muted">{t('setupHint')}</p>}
        {status?.mode === 'token' ? (
          <>
            <p className="muted">{t('tokenLogin')}</p>
            <Field label={t('token')}><input dir="ltr" type="password" value={tok} onChange={(e) => setTok(e.target.value)} autoFocus /></Field>
          </>
        ) : (
          <>
            <Field label={t('username')}><input dir="ltr" autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} autoFocus data-testid="login-user" /></Field>
            <Field label={t('password')} hint={setup ? '≥ 8, letters + digits/symbols' : ''}>
              <input dir="ltr" type="password" autoComplete={setup ? 'new-password' : 'current-password'} value={p} onChange={(e) => setP(e.target.value)} data-testid="login-pass" /></Field>
          </>
        )}
        <button className="btn primary" disabled={busy} data-testid="login-submit">{busy ? '…' : (setup ? t('save') : t('login'))}</button>
      </form>
    </div>
  )
}

export function Users({ me }) {
  const { t, notify } = useT()
  const [st, reload] = useAsync(() => (me?.role === 'admin' ? api('/api/auth/users') : Promise.resolve([])), [me?.role])
  const [form, setForm] = useState({ username: '', password: '', display_name: '', role: 'user' })
  const [pw, setPw] = useState({ current_password: '', new_password: '' })
  const run = async (fn, ok = '✓') => { try { await fn(); notify(ok, 'ok'); await reload() } catch (e) { notify(e.message, 'bad') } }
  return (
    <div className="page">
      <h1>{t('users')}</h1>
      {me?.role === 'admin' && (
        <>
          <table className="table">
            <thead><tr><th>{t('username')}</th><th>{t('role')}</th><th>{t('active')}</th><th>{t('books')}</th><th>{t('lastLogin')}</th><th /></tr></thead>
            <tbody>
              {(st.data || []).map((u) => (
                <tr key={u.id}>
                  <td>{u.username} <small className="muted">{u.display_name !== u.username ? u.display_name : ''}</small></td>
                  <td><Select value={u.role} onChange={(v) => run(() => api(`/api/auth/users/${u.id}`, { method: 'PATCH', body: { role: v } }))} options={['user', 'admin']} /></td>
                  <td>{u.active ? '✓' : '✖'}</td><td>{u.books}</td><td>{u.last_login ? new Date(u.last_login).toLocaleString() : '-'}</td>
                  <td className="actions">
                    <button className="btn small" onClick={() => run(() => api(`/api/auth/users/${u.id}`, { method: 'PATCH', body: { active: !u.active } }))}>{u.active ? t('disable') : t('enable')}</button>
                    <button className="btn small" onClick={() => { const v = window.prompt(t('newPassword')); if (v) run(() => api(`/api/auth/users/${u.id}`, { method: 'PATCH', body: { password: v } })) }}>{t('resetPassword')}</button>
                    {u.id !== me.id && <button className="btn small danger" onClick={() => window.confirm(`${t('delete')} ${u.username}?`) && run(() => api(`/api/auth/users/${u.id}`, { method: 'DELETE' }))}>{t('delete')}</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <form className="card form narrow" onSubmit={(e) => { e.preventDefault(); run(async () => { await api('/api/auth/users', { method: 'POST', body: form }); setForm({ username: '', password: '', display_name: '', role: 'user' }) }) }}>
            <h3>➕ {t('newUser')}</h3>
            <div className="row">
              <Field label={t('username')}><input dir="ltr" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></Field>
              <Field label={t('password')}><input dir="ltr" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
              <Field label={t('role')}><Select value={form.role} onChange={(v) => setForm({ ...form, role: v })} options={['user', 'admin']} /></Field>
            </div>
            <button className="btn primary">{t('save')}</button>
          </form>
        </>
      )}
      <form className="card form narrow" onSubmit={(e) => { e.preventDefault(); run(async () => { const r = await api('/api/auth/password', { method: 'POST', body: pw }); setToken(r.token); setPw({ current_password: '', new_password: '' }) }) }}>
        <h3>🔑 {t('changePassword')} — {me?.username}</h3>
        <div className="row">
          <Field label={t('currentPassword')}><input dir="ltr" type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
          <Field label={t('newPassword')}><input dir="ltr" type="password" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
        </div>
        <button className="btn">{t('save')}</button>
      </form>
    </div>
  )
}
