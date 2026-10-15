import { useEffect, useState } from 'react'
import { withToken } from '../api'
import { useT } from '../i18n'

// Book-like viewer built from the PDF's own rendered pages (what you see is the real PDF).
export default function Preview({ b }) {
  const { t } = useT()
  const pages = b.renders || []
  const [mode, setMode] = useState('double')
  const [idx, setIdx] = useState(0)
  const [zoom, setZoom] = useState(1)
  const rtl = b.language === 'fa'
  const step = mode === 'double' ? 2 : 1
  useEffect(() => {
    const on = (e) => {
      if (e.key === 'ArrowRight') setIdx((i) => Math.max(0, Math.min(pages.length - 1, i + (rtl ? -step : step))))
      if (e.key === 'ArrowLeft') setIdx((i) => Math.max(0, Math.min(pages.length - 1, i + (rtl ? step : -step))))
    }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [pages.length, step, rtl])
  if (!pages.length) return <p className="muted">No rendered pages yet — the preview appears after the PDF is built.</p>
  // spread: cover alone, then pairs (2-3, 4-5 ...)
  let spread = [idx]
  if (mode === 'double' && idx > 0) {
    const start = idx % 2 === 1 ? idx : idx - 1
    spread = [start, start + 1].filter((i) => i < pages.length)
  }
  if (rtl) spread = [...spread].reverse()
  const pdfIdx = (i) => i + 1
  const warnFor = (i) => {
    const issues = [...(b.qa_report?.pdf?.issues || []), ...(b.qa_report?.layout?.issues || [])]
    return issues.filter((x) => x.page === pdfIdx(i))
  }
  const go = (d) => setIdx((i) => Math.max(0, Math.min(pages.length - 1, i + d)))
  return (
    <div className="preview">
      <div className="toolbar">
        <button className={`btn small ${mode === 'single' ? 'primary' : ''}`} onClick={() => setMode('single')}>{t('single')}</button>
        <button className={`btn small ${mode === 'double' ? 'primary' : ''}`} onClick={() => setMode('double')}>{t('double')}</button>
        <label>{t('zoom')} <input type="range" min={0.6} max={2} step={0.1} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} /></label>
        <label>{t('goTo')} <input type="number" min={1} max={pages.length} value={idx + 1} onChange={(e) => setIdx(Math.max(0, Math.min(pages.length - 1, Number(e.target.value) - 1)))} /> / {pages.length}</label>
        {b.pdf_url && <a className="btn small" target="_blank" rel="noreferrer" href={withToken(b.pdf_url + '?inline=true')}>PDF ↗</a>}
      </div>
      <div className="stage">
        <button className="nav prev" onClick={() => go(rtl ? step : -step)} aria-label="previous">‹</button>
        <div className={`spread ${mode}`} style={{ '--zoom': zoom }}>
          {spread.map((i) => (
            <figure key={i} className="sheet">
              <img src={withToken(pages[i].url)} alt={`page ${i + 1}`} />
              <figcaption>{i + 1}{warnFor(i).length > 0 && <span className="warn-text"> ⚠ {warnFor(i).map((w) => w.code).join(', ')}</span>}</figcaption>
            </figure>
          ))}
        </div>
        <button className="nav next" onClick={() => go(rtl ? -step : step)} aria-label="next">›</button>
      </div>
      <div className="thumbs">
        {pages.map((p, i) => <img key={i} src={withToken(p.thumb)} alt="" className={spread.includes(i) ? 'active' : ''} onClick={() => setIdx(i)} />)}
      </div>
    </div>
  )
}
