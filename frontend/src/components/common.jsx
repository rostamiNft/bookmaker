import { useEffect, useRef, useState } from 'react'
import { STATUS_COLORS, useT } from '../i18n'

export function StatusBadge({ status }) {
  return <span className={`badge ${STATUS_COLORS[status] || 'muted'}`} data-testid="status">{status?.replaceAll('_', ' ')}</span>
}

export function Progress({ value }) {
  return <div className="progress" role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${Math.max(2, value || 0)}%` }} /></div>
}

export function Issues({ issues, empty }) {
  const { t } = useT()
  if (!issues || issues.length === 0) return <p className="ok-text">✓ {empty || t('noIssues')}</p>
  return (
    <ul className="issues">
      {issues.map((i, k) => (
        <li key={k} className={i.severity}>
          <b>{i.severity === 'error' ? '✖' : '⚠'} {i.code}</b>{i.page != null && i.page !== '' ? ` · p.${i.page}` : ''} — {i.message}
        </li>
      ))}
    </ul>
  )
}

export function Field({ label, children, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  )
}

export function Select({ value, onChange, options, ...rest }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} {...rest}>
      {options.map((o) => (Array.isArray(o) ? <option key={o[0]} value={o[0]}>{o[1]}</option> : <option key={o} value={o}>{o}</option>))}
    </select>
  )
}

export function Toast({ msg, onClose }) {
  useEffect(() => { if (msg) { const id = setTimeout(onClose, 6000); return () => clearTimeout(id) } }, [msg, onClose])
  if (!msg) return null
  return <div className={`toast ${msg.type || ''}`} role="alert" onClick={onClose}>{msg.text}</div>
}

export function usePoll(fn, ms, active = true) {
  const saved = useRef(fn)
  saved.current = fn
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => saved.current(), ms)
    return () => clearInterval(id)
  }, [ms, active])
}

export function useAsync(fn, deps) {
  const [state, set] = useState({ loading: true, data: null, error: null })
  const reload = async () => {
    try { set((s) => ({ ...s, loading: true })); const data = await fn(); set({ loading: false, data, error: null }) }
    catch (e) { set({ loading: false, data: null, error: e }) }
  }
  useEffect(() => { reload() }, deps) // eslint-disable-line
  return [state, reload, set]
}

export function fmtBytes(n) {
  if (!n && n !== 0) return '-'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(1)} ${u[i]}`
}
