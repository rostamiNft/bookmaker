import { useState } from 'react'
import { api, withToken } from '../api'
import { fmtBytes } from './common'
import { useT } from '../i18n'

const FORMATS = [['epub', '📱'], ['html', '🌐'], ['images', '🖼']]
const LABEL = { epub: 'epub', html: 'html', images: 'imagesZip' }

export default function Exports({ b, busy, reload }) {
  const { t, notify } = useT()
  const [working, setWorking] = useState('')
  const build = async (fmt) => {
    setWorking(fmt)
    try { await api(`/api/projects/${b.id}/exports`, { method: 'POST', body: { format: fmt } }); notify('✓', 'ok'); await reload() }
    catch (e) { notify(e.message, 'bad') } finally { setWorking('') }
  }
  const ex = b.exports || {}
  const audioPages = b.pages.filter((p) => p.audio_url).length
  return (
    <div className="grid exports">
      {FORMATS.map(([fmt, icon]) => {
        const info = ex[fmt]
        const v = info?.validation
        const stale = info && info.version !== b.version
        return (
          <div className="card" key={fmt} data-testid={`export-${fmt}`}>
            <h3>{icon} {t(LABEL[fmt])}</h3>
            {fmt !== 'images' && <p className="muted small">🔊 {audioPages}/{b.pages.length} {t('narrationAudio')}</p>}
            {info ? (
              <dl className="kv">
                <dt>v</dt><dd>{info.version}{stale ? ' ⚠ (older than the book)' : ''}</dd>
                <dt>size</dt><dd>{fmtBytes(info.bytes)}</dd>
                <dt>time</dt><dd>{new Date(info.built_at).toLocaleString()}</dd>
                {fmt === 'epub' && <><dt>{t('epubcheck')}</dt><dd>{v?.checked ? (v.ok ? <span className="badge ok">✓ {v.checker}</span>
                  : <span className="badge bad">✖ {v.errors} errors</span>) : <span className="muted">{t('notChecked')} {v?.note ? `— ${v.note}` : ''}</span>}
                  {v?.checked && v.warnings > 0 && <small className="warn-text"> ⚠ {v.warnings}</small>}</dd></>}
                {info.pages_without_image?.length > 0 && <><dt>⚠</dt><dd>no image: {info.pages_without_image.join(', ')}</dd></>}
              </dl>
            ) : <p className="muted">—</p>}
            {v?.messages?.length > 0 && <details><summary>messages</summary><ul className="small-list">{v.messages.map((m, i) => <li key={i}>{m.severity} {m.id}: {m.message} {m.where ? `(${m.where})` : ''}</li>)}</ul></details>}
            <div className="row-inline">
              <button className="btn" disabled={busy || !!working} onClick={() => build(fmt)}>{working === fmt ? '…' : `⚙ ${t('build')}`}</button>
              {info && <a className="btn primary" href={withToken(`/api/projects/${b.id}/exports/${fmt}`)}>⬇ {t('download') .replace('PDF', '').trim() || 'Download'}</a>}
            </div>
          </div>
        )
      })}
    </div>
  )
}
