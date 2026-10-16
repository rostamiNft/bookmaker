import { api, withToken } from '../api'
import { fmtBytes, Progress, StatusBadge, useAsync, usePoll } from '../components/common'
import { useT } from '../i18n'

export function BookCard({ b }) {
  const { t } = useT()
  return (
    <a className="book-card" href={`#/book/${b.id}`} data-testid="book-card">
      <div className="cover">{b.cover_url ? <img src={withToken(b.cover_url)} alt="" loading="lazy" /> : <span>📘</span>}</div>
      <div className="meta">
        <b dir="auto">{b.title || '…'}</b>
        <small>{b.language.toUpperCase()} · {b.age_group} · {b.page_count} p</small>
        <StatusBadge status={b.status} />
        {['running', 'queued'].includes(b.status) && <Progress value={b.progress} />}
        {b.has_pdf && <span className="pill">PDF v{b.version}</span>}
        {b.error && <small className="bad-text">{b.error.slice(0, 80)}</small>}
        {!b.error && b.status !== 'running' && b.qa_summary && <small>{t('errors')}: {b.qa_summary.errors} · {t('warnings')}: {b.qa_summary.warnings}</small>}
      </div>
    </a>
  )
}

export default function Dashboard() {
  const { t } = useT()
  const [sys, reloadSys] = useAsync(() => api('/api/system'), [])
  const [books, reloadBooks] = useAsync(() => api('/api/projects'), [])
  const list = books.data || []
  const active = list.some((b) => ['running', 'queued'].includes(b.status))
  usePoll(() => { reloadBooks(); reloadSys() }, 3000, active)
  const s = sys.data
  const running = list.filter((b) => ['running', 'queued', 'paused'].includes(b.status))
  const failed = list.filter((b) => ['failed', 'needs_review'].includes(b.status))
  const done = list.filter((b) => b.status.startsWith('completed'))
  return (
    <div className="page">
      <section className="hero-card">
        <div>
          <h1>{t('tagline')}</h1>
          <p>{t('offlineNote')}</p>
        </div>
        <a className="btn primary big" href="#/new">✨ {t('createQuick')}</a>
      </section>
      <section className="stats">
        <div className="stat"><small>{t('completed')}</small><b>{done.length}</b></div>
        <div className="stat"><small>{t('running')}</small><b>{running.length}</b></div>
        <div className="stat"><small>{t('failed')}</small><b>{failed.length}</b></div>
        <div className="stat"><small>{t('queue')}</small><b>{s ? (s.queue.queued || 0) + (s.queue.running || 0) : '-'}</b><small>{s && `max ${s.limits.books} / ${s.limits.images} img`}</small></div>
        <div className="stat"><small>{t('cost')}</small><b>${s ? s.usage.cost_usd.toFixed(2) : '-'}</b><small>{s && `${s.usage.calls} calls · ${s.usage.images} ${t('images')}`}</small></div>
        <div className="stat"><small>{t('resources')}</small><b>{s ? fmtBytes(s.data_bytes) : '-'}</b>
          <small>{s && `disk free ${fmtBytes(s.disk_free)} · load ${s.load[0].toFixed(2)}/${s.cpus} CPU`}</small></div>
      </section>
      {running.length > 0 && <><h2>{t('running')}</h2><div className="grid">{running.map((b) => <BookCard key={b.id} b={b} />)}</div></>}
      {failed.length > 0 && <><h2>{t('failed')}</h2><div className="grid">{failed.map((b) => <BookCard key={b.id} b={b} />)}</div></>}
      <h2>{t('recent')}</h2>
      {list.length === 0 && !books.loading && <p className="muted">{t('noBooks')}</p>}
      <div className="grid">{done.slice(0, 8).map((b) => <BookCard key={b.id} b={b} />)}</div>
    </div>
  )
}
