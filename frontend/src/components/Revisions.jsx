import { api, withToken } from '../api'
import { useAsync } from './common'
import { useT } from '../i18n'

// Per-page undo: every edit, regeneration, rewrite, repair and restore snapshots the page first.
export default function Revisions({ bookId, page, run, busy }) {
  const { t } = useT()
  const [st, reload] = useAsync(() => api(`/api/projects/${bookId}/pages/${page.id}/revisions`), [bookId, page.id, page.text, page.image_url])
  const list = st.data || []
  const restore = (r, text, image) => run(async () => {
    await api(`/api/projects/${bookId}/pages/${page.id}/revisions/${r.id}/restore`, { method: 'POST', body: { restore_text: text, restore_image: image, rebuild: true } })
    await reload()
  })
  return (
    <details className="revisions" data-testid="revisions">
      <summary>↶ {t('revisions')} ({list.length})</summary>
      {list.length === 0 && <p className="muted small">{t('noRevisions')}</p>}
      <ul className="rev-list">
        {list.map((r) => (
          <li key={r.id}>
            {r.image_url && <img src={withToken(r.image_url)} alt="" loading="lazy" />}
            <div className="grow">
              <small className="muted">{new Date(r.at).toLocaleString()} · {r.reason.replaceAll('_', ' ')} · {r.actor}</small>
              <p className="small" dir="auto">{r.text?.slice(0, 160)}{r.text?.length > 160 ? '…' : ''}</p>
              <div className="row-inline wrap">
                <button className="btn small" disabled={busy || !r.text || r.text === page.text} onClick={() => restore(r, true, false)}>{t('restoreText')}</button>
                <button className="btn small" disabled={busy || !r.image_url || r.image_url === page.image_url} onClick={() => restore(r, false, true)}>{t('restoreImage')}</button>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </details>
  )
}
