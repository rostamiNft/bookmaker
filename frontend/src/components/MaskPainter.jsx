import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Field, Select } from './common'
import { useT } from '../i18n'

// Paint a repair mask over a page illustration. The mask is drawn on a canvas with the
// picture's natural resolution (so it lines up exactly), shown semi-transparent on top
// of the image, and uploaded as an RGBA PNG (painted = opaque) to /inpaint.
export default function MaskPainter({ src, bookId, page, providers, onDone, onClose }) {
  const { t, notify } = useT()
  const canvas = useRef(null)
  const img = useRef(null)
  const drawing = useRef(false)
  const last = useRef(null)
  const [size, setSize] = useState(null)
  const [brush, setBrush] = useState(28)
  const [erase, setErase] = useState(false)
  const [engine, setEngine] = useState('classical')
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [painted, setPainted] = useState(false)

  useEffect(() => {
    const el = img.current
    const ready = () => setSize({ w: el.naturalWidth, h: el.naturalHeight })
    if (el && el.complete && el.naturalWidth) ready()
    else el && el.addEventListener('load', ready, { once: true })
  }, [src])

  const pos = (e) => {
    const c = canvas.current
    const r = c.getBoundingClientRect()
    const p = e.touches ? e.touches[0] : e
    return { x: ((p.clientX - r.left) / r.width) * c.width, y: ((p.clientY - r.top) / r.height) * c.height, scale: c.width / r.width }
  }
  const stroke = (a, b) => {
    const ctx = canvas.current.getContext('2d')
    ctx.globalCompositeOperation = erase ? 'destination-out' : 'source-over'
    ctx.strokeStyle = 'rgba(255, 40, 90, 1)'
    ctx.fillStyle = 'rgba(255, 40, 90, 1)'
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.lineWidth = brush * a.scale
    ctx.beginPath()
    ctx.moveTo(a.x, a.y)
    ctx.lineTo(b.x, b.y)
    ctx.stroke()
    if (!erase) setPainted(true)
  }
  const down = (e) => { e.preventDefault(); drawing.current = true; const p = pos(e); last.current = p; stroke(p, p) }
  const move = (e) => { if (!drawing.current) return; e.preventDefault(); const p = pos(e); stroke(last.current, p); last.current = p }
  const up = () => { drawing.current = false }
  const clear = () => { const c = canvas.current; c.getContext('2d').clearRect(0, 0, c.width, c.height); setPainted(false) }

  const submit = async () => {
    setBusy(true)
    try {
      const blob = await new Promise((res) => canvas.current.toBlob(res, 'image/png'))
      const form = new FormData()
      form.append('mask', blob, 'mask.png')
      form.append('engine', engine)
      form.append('prompt', prompt)
      const r = await api(`/api/projects/${bookId}/pages/${page.id}/inpaint`, { method: 'POST', form })
      notify(`✓ ${(r.mask_coverage * 100).toFixed(1)}%`, 'ok')
      onDone && onDone()
    } catch (e) { notify(e.message, 'bad') } finally { setBusy(false) }
  }
  const engines = [['classical', t('engineClassical')], ['default', t('engineDefault')],
    ...(providers || []).filter((p) => p.enabled && (p.kind === 'inpaint' || (p.kind === 'image' && p.provider_type !== 'fastsdcpu' && p.provider_type !== 'offline')))
      .map((p) => [p.id, `${p.name} (${p.provider_type})`])]
  return (
    <div className="modal" role="dialog" aria-label={t('repair')}>
      <div className="card form wide mask-painter">
        <h3>🩹 {t('repair')} — p.{page.number}</h3>
        <p className="muted small">{t('repairHint')}</p>
        <div className="mask-stage">
          <img ref={img} src={src} alt="" draggable={false} />
          {size && <canvas ref={canvas} width={size.w} height={size.h} data-testid="mask-canvas"
            onMouseDown={down} onMouseMove={move} onMouseUp={up} onMouseLeave={up}
            onTouchStart={down} onTouchMove={move} onTouchEnd={up} />}
        </div>
        <div className="row-inline wrap">
          <label>{t('brush')} <input type="range" min={6} max={90} value={brush} onChange={(e) => setBrush(Number(e.target.value))} /></label>
          <label><input type="checkbox" checked={erase} onChange={(e) => setErase(e.target.checked)} /> {t('eraser')}</label>
          <button className="btn small" onClick={clear}>{t('clear')}</button>
        </div>
        <div className="row">
          <Field label={t('engine')}><Select value={engine} onChange={setEngine} options={engines} /></Field>
          <Field label={t('repairPrompt')}><input dir="auto" value={prompt} maxLength={600} disabled={engine === 'classical'} onChange={(e) => setPrompt(e.target.value)} /></Field>
        </div>
        <div className="row-inline">
          <button className="btn primary" disabled={busy || !painted} onClick={submit} data-testid="run-repair">{busy ? '…' : `🩹 ${t('runRepair')}`}</button>
          <button className="btn" onClick={onClose}>{t('close')}</button>
        </div>
      </div>
    </div>
  )
}
