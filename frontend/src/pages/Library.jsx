import { useState } from 'react'
import { api, withToken } from '../api'
import { StatusBadge, useAsync } from '../components/common'
import { useT } from '../i18n'

export default function Library() {
  const { t, notify } = useT()
  const [archived, setArchived] = useState(false)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [books, reload] = useAsync(() => api(`/api/projects?archived=${archived}&q=${encodeURIComponent(q)}&status=${status}`), [archived, q, status])
  const act = async (fn, okMsg) => { try { await fn(); notify(okMsg, 'ok'); reload() } catch (e) { notify(e.message, 'bad') } }
  return (
    <div className="page">
      <h1>{t('library')}</h1>
      <div className="toolbar">
        <input placeholder={t('search')} value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">{t('all')}</option>
          <option value="completed,completed_with_warnings">{t('completed')}</option>
          <option value="running,queued,paused">{t('running')}</option>
          <option value="failed,needs_review">{t('failed')}</option>
        </select>
        <label><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> {t('archived')}</label>
      </div>
      <table className="table">
        <thead><tr><th></th><th>{t('title')}</th><th>{t('language')}</th><th>{t('age')}</th><th>{t('status')}</th><th>Created</th><th></th></tr></thead>
        <tbody>
          {(books.data || []).map((b) => (
            <tr key={b.id}>
              <td className="thumb">{b.cover_url && <img src={withToken(b.cover_url)} alt="" />}</td>
              <td dir="auto"><a href={`#/book/${b.id}`}>{b.title || b.id.slice(0, 8)}</a><br /><small className="muted">{b.id}</small></td>
              <td>{b.language}</td><td>{b.age_group}</td><td><StatusBadge status={b.status} /></td>
              <td>{new Date(b.created_at).toLocaleString()}</td>
              <td className="actions">
                {b.has_pdf && <a className="btn small" href={withToken(`/api/projects/${b.id}/pdf`)}>⬇ PDF</a>}
                <button className="btn small" onClick={() => act(() => api(`/api/projects/${b.id}`, { method: 'PATCH', body: { archived: !archived } }), '✓')}>{archived ? t('unarchive') : t('archive')}</button>
                <button className="btn small danger" onClick={() => {
                  const v = window.prompt(`${t('confirmDelete')}\n${b.id}`)
                  if (v === b.id) act(() => api(`/api/projects/${b.id}?confirm=${b.id}`, { method: 'DELETE' }), '✓ moved to trash')
                }}>{t('delete')}</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {books.data && books.data.length === 0 && <p className="muted">{t('noBooks')}</p>}
    </div>
  )
}
