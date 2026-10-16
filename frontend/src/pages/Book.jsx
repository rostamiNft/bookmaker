import { useEffect, useState } from 'react'
import { ApiError, api, withToken } from '../api'
import { Field, Issues, Progress, Select, StatusBadge, useAsync, usePoll } from '../components/common'
import { useT } from '../i18n'
import Preview from '../components/Preview'
import MaskPainter from '../components/MaskPainter'
import Revisions from '../components/Revisions'
import Exports from '../components/Exports'

const ACTIVE = ['queued', 'running']

export default function Book({ id, tab }) {
  const { t, notify } = useT()
  const [st, reload] = useAsync(() => api(`/api/projects/${id}`), [id])
  const b = st.data
  usePoll(reload, 2000, !!b && ACTIVE.includes(b.status))
  const run = async (fn, ok = '✓') => { try { await fn(); notify(ok, 'ok'); await reload() } catch (e) { notify(e.message, 'bad') } }
  if (st.error) return <div className="page"><p className="bad-text">{st.error.message}</p></div>
  if (!b) return <div className="page"><p className="muted">…</p></div>
  const busy = ACTIVE.includes(b.status)
  const rtl = b.language === 'fa'
  const tabs = [['pages', t('pagesTab')], ['characters', t('charactersTab')], ['preview', t('previewTab')], ['qa', t('qaTab')],
    ['exports', t('exportsTab')], ['history', t('historyTab')], ['logs', t('logsTab')], ['settings', t('settingsTab')]]
  return (
    <div className="page">
      <section className="book-head">
        <div className="cover-sm">{b.cover_url ? <img src={withToken(b.cover_url)} alt="" /> : '📘'}</div>
        <div className="grow">
          <h1 dir="auto">{b.title || '…'}</h1>
          <p className="muted">{b.language.toUpperCase()} · {b.age_group} · {b.pages.length} p · {b.plan?.engine || ''} · v{b.version}</p>
          <div className="row-inline"><StatusBadge status={b.status} /> <span className="muted">{t('stage')}: {b.stage || '-'}</span></div>
          {busy && <Progress value={b.progress} />}
          {b.error && <p className="bad-text">{b.error}</p>}
          {b.status === 'awaiting_review' && <div className="review-banner" data-testid="review-banner"><p>📝 {t('awaitingReview')}</p>
            <button className="btn primary" onClick={() => run(() => api(`/api/projects/${id}/approve-outline`, { method: 'POST' }))} data-testid="approve-outline">✅ {t('approveOutline')}</button></div>}
          {b.qa_report?.summary && <p className="muted">{t('errors')}: {b.qa_report.summary.errors} · {t('warnings')}: {b.qa_report.summary.warnings} · PDF {b.qa_report.summary.pdf_valid ? '✓ valid' : '✖'}</p>}
        </div>
        <div className="head-actions">
          {b.pdf_url && <a className="btn primary" href={withToken(b.pdf_url)} data-testid="download">⬇ {t('download')}</a>}
          {busy ? <button className="btn" onClick={() => run(() => api(`/api/projects/${id}/pause`, { method: 'POST' }))}>⏸ {t('pause')}</button>
            : <button className="btn" onClick={() => run(() => api(`/api/projects/${id}/start`, { method: 'POST' }))}>▶ {t('resume')}</button>}
          <button className="btn" disabled={busy} onClick={() => run(() => api(`/api/projects/${id}/rebuild`, { method: 'POST' }))}>🔁 {t('rebuild')}</button>
          <button className="btn" disabled={busy || !b.pages.length} onClick={() => run(async () => {
            try { await api(`/api/projects/${id}/narrate`, { method: 'POST', body: {} }) } catch (e) {
              if (!(e instanceof ApiError && e.status === 402)) throw e
              let msg = e.message
              try { msg = JSON.parse(e.detail).message } catch { /* plain text */ }
              if (!window.confirm(msg)) return
              await api(`/api/projects/${id}/narrate`, { method: 'POST', body: { confirm_cost: true } })
            }
          })} data-testid="narrate">🔊 {t('narrate')}</button>
          <a className="btn" href={withToken(`/api/projects/${id}/export`)}>🗄 {t('export')}</a>
        </div>
      </section>
      <nav className="tabs">{tabs.map(([k, l]) => <a key={k} href={`#/book/${id}/${k}`} className={tab === k ? 'active' : ''}>{l}</a>)}</nav>
      {tab === 'pages' && <PagesTab b={b} run={run} busy={busy} rtl={rtl} />}
      {tab === 'characters' && <CharactersTab b={b} run={run} busy={busy} />}
      {tab === 'preview' && <Preview b={b} />}
      {tab === 'qa' && <QATab b={b} />}
      {tab === 'exports' && <Exports b={b} busy={busy} reload={reload} />}
      {tab === 'history' && <HistoryTab id={id} />}
      {tab === 'logs' && <LogsTab id={id} busy={busy} />}
      {tab === 'settings' && <SettingsTab b={b} run={run} busy={busy} />}
    </div>
  )
}

function PagesTab({ b, run, busy, rtl }) {
  const { t } = useT()
  const [sel, setSel] = useState(b.pages[0]?.id)
  const page = b.pages.find((p) => p.id === sel) || b.pages[0]
  const [text, setText] = useState(page?.text || '')
  const [prompt, setPrompt] = useState(page?.image_prompt || '')
  const [instr, setInstr] = useState('')
  const [newText, setNewText] = useState('')
  const [painting, setPainting] = useState(false)
  const [prov] = useAsync(() => api('/api/providers').catch(() => ({ providers: [] })), [])
  useEffect(() => { setText(page?.text || ''); setPrompt(page?.image_prompt || '') }, [page?.id, page?.text, page?.image_prompt])
  if (!page) return <p className="muted">No pages yet.</p>
  const base = `/api/projects/${b.id}/pages`
  const move = (dir) => {
    const ids = b.pages.map((p) => p.id); const i = ids.indexOf(page.id); const j = i + dir
    if (j < 0 || j >= ids.length) return
    ;[ids[i], ids[j]] = [ids[j], ids[i]]
    run(() => api(`${base}/reorder`, { method: 'POST', body: { order: ids } }))
  }
  const cover = b.stage_state?.cover
  return (
    <div className="pages-tab">
      <ol className="page-list">
        {cover?.file && <li className="cover-item"><img src={withToken(`/api/projects/${b.id}/files/${cover.file}`)} alt="" /><span>{t('cover')}</span>
          <button className="btn small" disabled={busy} onClick={() => run(() => api(`/api/projects/${b.id}/cover/regenerate`, { method: 'POST' }))}>↻</button></li>}
        {b.pages.map((p) => {
          const errs = (p.qa?.image?.issues || []).filter((i) => i.severity === 'error').length
          return (
            <li key={p.id} className={p.id === page.id ? 'active' : ''} onClick={() => setSel(p.id)} data-testid="page-item">
              {p.image_url ? <img src={withToken(p.image_url)} alt="" loading="lazy" /> : <div className="ph">{p.image_status}</div>}
              <span><b>{p.number}</b> · {p.beat}{p.image_locked ? ' 🔒' : ''}{errs ? ` ✖${errs}` : ''}</span>
            </li>
          )
        })}
      </ol>
      <div className="page-editor card">
        <div className="editor-grid">
          <div>
            {page.image_url ? <img className="page-img" src={withToken(page.image_url)} alt="" /> : <div className="ph big">{page.image_status}</div>}
            <div className="row-inline wrap">
              <button className="btn" disabled={busy} onClick={() => run(() => api(`${base}/${page.id}/regenerate-image`, { method: 'POST', body: { prompt: prompt !== page.image_prompt ? prompt : '' } }))}>🎨 {t('regenImage')}</button>
              {page.image_locked
                ? <button className="btn" onClick={() => run(() => api(`${base}/${page.id}`, { method: 'PATCH', body: { image_locked: false } }))}>🔓 {t('unlock')}</button>
                : <button className="btn" disabled={!page.image_url} onClick={() => run(() => api(`${base}/${page.id}`, { method: 'PATCH', body: { image_status: 'approved' } }))}>✅ {t('approve')}</button>}
            </div>
            {page.image_url && <button className="btn" disabled={busy} onClick={() => setPainting(true)} data-testid="open-repair">🩹 {t('repair')}</button>}
            {page.inpaints > 0 && <small className="muted"> · {page.inpaints}× 🩹</small>}
            {page.audio_url && <audio className="page-audio" controls preload="none" src={withToken(page.audio_url)} data-testid="page-audio" />}
            {painting && <MaskPainter src={withToken(page.image_url)} bookId={b.id} page={page} providers={prov.data?.providers}
              onClose={() => setPainting(false)} onDone={() => { setPainting(false); run(async () => {}) }} />}
            <Issues issues={page.qa?.image?.issues} />
            {page.image_meta?.attempts && <details><summary>Image attempts ({page.image_meta.attempts.length}) · {page.image_meta.provider}</summary>
              <ul className="small-list">{page.image_meta.attempts.map((a, i) => <li key={i}>#{a.attempt} variant {a.variant} seed {a.seed ?? '-'} — {a.ok ? '✓' : '✖'} {(a.issues || []).join(', ')} {a.error || ''}</li>)}</ul>
              <pre className="prompt">{page.image_meta.prompt}</pre></details>}
          </div>
          <div>
            <Field label={`${t('text')} — p.${page.number}`}><textarea dir={rtl ? 'rtl' : 'ltr'} rows={7} value={text} onChange={(e) => setText(e.target.value)} data-testid="page-text" /></Field>
            <Field label={t('imagePrompt')}><textarea dir="ltr" rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></Field>
            <div className="row-inline wrap">
              <button className="btn primary" disabled={busy || (text === page.text && prompt === page.image_prompt)}
                onClick={() => run(() => api(`${base}/${page.id}`, { method: 'PATCH', body: { text, image_prompt: prompt } }))} data-testid="save-page">💾 {t('save')}</button>
              <button className="btn" disabled={busy} onClick={() => move(-1)}>↑ {t('moveUp')}</button>
              <button className="btn" disabled={busy} onClick={() => move(1)}>↓ {t('moveDown')}</button>
              <button className="btn danger" disabled={busy} onClick={() => window.confirm(t('removePage') + '?') && run(() => api(`${base}/${page.id}`, { method: 'DELETE' }))}>🗑 {t('removePage')}</button>
            </div>
            <Field label={t('instruction')}><input dir="auto" value={instr} onChange={(e) => setInstr(e.target.value)} /></Field>
            <button className="btn" disabled={busy} onClick={() => run(() => api(`${base}/${page.id}/regenerate-text`, { method: 'POST', body: { instruction: instr } }))}>✍ {t('regenText')}</button>
            <hr />
            <Field label={`${t('addPage')} ${page.number}`}><textarea dir={rtl ? 'rtl' : 'ltr'} rows={2} value={newText} onChange={(e) => setNewText(e.target.value)} /></Field>
            <button className="btn" disabled={busy || !newText.trim()} onClick={() => run(async () => { await api(base, { method: 'POST', body: { after: page.number, text: newText } }); setNewText('') })}>➕ {t('addPage')}</button>
            <p className="muted small">Edits are applied to the PDF with “{t('rebuild')}”. New pages copy the scene of this page; pending illustrations are generated on rebuild.</p>
            <Revisions bookId={b.id} page={page} run={run} busy={busy} />
          </div>
        </div>
      </div>
    </div>
  )
}

function CharactersTab({ b, run, busy }) {
  const { t } = useT()
  return (
    <>
      {b.cast_url && <div className="card cast"><h3>👥 {t('castSheet')}</h3>
        <img src={withToken(b.cast_url)} alt="" data-testid="cast-sheet" />
        {b.cast_ai_url && <img src={withToken(b.cast_ai_url)} alt="" />}</div>}
      <div className="grid chars">
        {b.characters.map((c) => <CharacterCard key={c.id} b={b} c={c} run={run} busy={busy} t={t} />)}
      </div>
    </>
  )
}

function CharacterCard({ b, c, run, busy, t }) {
  const [name, setName] = useState(c.name)
  const [desc, setDesc] = useState(c.spec.description || '')
  const app = c.spec.appearance || {}
  const out = c.spec.outfit || {}
  const [colors, setColors] = useState({ skin: app.skin, hair_color: app.hair_color, top_color: out.top_color, accessory_color: out.accessory_color })
  const human = ['human', 'human_old'].includes(app.species)
  const [scale, setScale] = useState(app.scale ?? 1)
  const upload = async (e) => {
    const f = e.target.files[0]; if (!f) return
    const form = new FormData(); form.append('file', f)
    run(() => api(`/api/projects/${b.id}/characters/${c.id}/reference`, { method: 'POST', form }))
  }
  const save = () => run(() => api(`/api/projects/${b.id}/characters/${c.id}`, {
    method: 'PATCH', body: {
      name: name !== c.name ? name : null,
      spec: { description: desc, appearance: { skin: colors.skin, hair_color: colors.hair_color, scale }, outfit: { top_color: colors.top_color, accessory_color: colors.accessory_color } },
    },
  }), '✓ saved — use “Rebuild PDF” to redraw affected pages')
  return (
    <div className="card char">
      {c.ref_url && <img src={withToken(c.ref_url)} alt="" className="ref" />}
      <small className="muted">{t('reference')} {c.ref_approved ? '✓' : '(not approved)'} · id: {c.char_id} · {c.role} · {app.species}</small>
      <Field label={t('name')}><input dir="auto" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label={t('description')}><textarea rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
      <div className="colors">
        <span>{t('colors')}:</span>
        {Object.entries(colors).filter(([k]) => human || ['skin', 'accessory_color'].includes(k)).map(([k, v]) => (
          <label key={k} title={k}><input type="color" value={v || '#888888'} onChange={(e) => setColors((o) => ({ ...o, [k]: e.target.value }))} /><small>{k.replace('_color', '')}</small></label>
        ))}
      </div>
      <Field label={`${t('scale')}: ×${Number(scale).toFixed(2)}${b.relative_heights?.[c.char_id] ? ` · ${t('relHeight')} ${b.relative_heights[c.char_id].toFixed(2)}` : ''}`}>
        <input type="range" min={0.5} max={1.8} step={0.05} value={scale} onChange={(e) => setScale(Number(e.target.value))} data-testid="char-scale" /></Field>
      {c.spec.distinctive?.length > 0 && <p className="small muted">✦ {c.spec.distinctive.join(' · ')}</p>}
      <div className="row-inline wrap">
        <button className="btn primary" disabled={busy} onClick={save}>💾 {t('save')}</button>
        <label className="btn">📤 {t('uploadRef')}<input type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={upload} /></label>
      </div>
    </div>
  )
}

function QATab({ b }) {
  const { t } = useT()
  const q = b.qa_report || {}
  const s = q.summary
  const sec = (title, issues) => (
    <div className="card"><h3>{title} <small className="muted">({(issues || []).filter((i) => i.severity === 'error').length} ✖ / {(issues || []).filter((i) => i.severity === 'warning').length} ⚠)</small></h3><Issues issues={issues} /></div>
  )
  return (
    <div className="qa">
      {s && <div className="card"><h3>{t('status')}: <span className={`badge ${s.status.startsWith('completed') ? (s.warnings ? 'warn' : 'ok') : 'bad'}`}>{s.status}</span></h3>
        <p>{t('errors')}: {s.errors} · {t('warnings')}: {s.warnings} · PDF: {s.pdf_valid ? 'valid' : 'invalid'} · {s.story_pages} pages · {s.word_count} words</p>
        <p className="muted small">Story: {s.engines?.story} · Images: {s.engines?.images}</p></div>}
      {sec(t('story'), q.story?.issues)}
      {sec(t('imagesSec'), q.images?.issues)}
      {sec(t('layout'), q.layout?.issues)}
      {sec(t('pdf'), q.pdf?.issues)}
      {q.pdf?.info && <div className="card"><h3>PDF</h3><dl className="kv">
        <dt>pages</dt><dd>{q.pdf.info.pages}</dd><dt>size</dt><dd>{q.pdf.info.size_mb} MB</dd><dt>lang</dt><dd>{q.pdf.info.lang}</dd>
        <dt>rendered</dt><dd>{q.pdf.info.rendered}</dd><dt>fonts</dt><dd>{(q.pdf.info.fonts || []).map((f) => `${f.name}${f.embedded ? ' (embedded)' : ' ✖'}`).join(', ')}</dd>
        <dt>title</dt><dd dir="auto">{q.pdf.info.metadata?.['/Title']}</dd></dl></div>}
    </div>
  )
}

function HistoryTab({ id }) {
  const [h] = useAsync(() => api(`/api/projects/${id}/history`), [id])
  return (
    <ul className="timeline">
      {(h.data || []).map((e) => (
        <li key={e.id}><b>{e.action}</b> <span className="muted">{new Date(e.at).toLocaleString()} · {e.actor}</span>
          {e.detail && Object.keys(e.detail).length > 0 && <details><summary>details</summary><pre dir="auto">{JSON.stringify(e.detail, null, 1)}</pre></details>}</li>
      ))}
    </ul>
  )
}

function LogsTab({ id, busy }) {
  const [l, reload] = useAsync(() => api(`/api/projects/${id}/logs`), [id])
  usePoll(reload, 2500, busy)
  return (
    <table className="table logs"><tbody>
      {(l.data || []).map((r) => <tr key={r.id} className={r.status}><td>{new Date(r.at).toLocaleTimeString()}</td><td>{r.stage}</td><td>{r.status}</td><td dir="auto">{r.message}</td></tr>)}
    </tbody></table>
  )
}

function SettingsTab({ b, run, busy }) {
  const { t } = useT()
  const [s, setS] = useState({ page_size: b.settings.page_size, orientation: b.settings.orientation, cover_style: b.settings.cover_style,
    page_numbers: b.settings.page_numbers, back_cover: b.settings.back_cover, output_profile: b.settings.output_profile, author: b.settings.author, publisher: b.settings.publisher,
    layout_style: b.settings.layout_style || 'classic', bleed_mm: b.settings.bleed_mm || 0, crop_marks: !!b.settings.crop_marks, color_mode: b.settings.color_mode || 'rgb',
    narration_audio: !!b.settings.narration_audio, tts_provider_id: b.settings.tts_provider_id || '', narration_voice: b.settings.narration_voice || '',
    narration_speed: b.settings.narration_speed || 1, cast_reference: b.settings.cast_reference ?? true })
  const [prov] = useAsync(() => api('/api/providers').catch(() => ({ providers: [] })), [])
  const tts = (prov.data?.providers || []).filter((p) => p.kind === 'tts' && p.enabled)
  const [title, setTitle] = useState(b.title)
  const set = (k) => (v) => setS((o) => ({ ...o, [k]: v }))
  return (
    <div className="card form narrow">
      <p className="muted">{t('settingsLocked')}</p>
      <Field label={t('title')}><input dir="auto" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      <div className="row">
        <Field label={t('pageSize')}><Select value={s.page_size} onChange={set('page_size')} options={['A4', 'A5', 'Letter', 'Square8']} /></Field>
        <Field label={t('orientation')}><Select value={s.orientation} onChange={set('orientation')} options={['portrait', 'landscape']} /></Field>
        <Field label={t('coverStyle')}><Select value={s.cover_style} onChange={set('cover_style')} options={['illustrated', 'framed', 'minimal']} /></Field>
      </div>
      <div className="row">
        <Field label={t('author')}><input dir="auto" value={s.author} onChange={(e) => set('author')(e.target.value)} /></Field>
        <Field label={t('publisher')}><input dir="auto" value={s.publisher} onChange={(e) => set('publisher')(e.target.value)} /></Field>
        <Field label={t('profile')}><Select value={s.output_profile} onChange={set('output_profile')} options={['screen', 'print']} /></Field>
      </div>
      <div className="row checks">
        <label><input type="checkbox" checked={s.page_numbers} onChange={(e) => set('page_numbers')(e.target.checked)} /> {t('pageNumbers')}</label>
        <label><input type="checkbox" checked={s.back_cover} onChange={(e) => set('back_cover')(e.target.checked)} /> {t('backCover')}</label>
      </div>
      <h3>🖨 {t('printOptions')}</h3>
      <div className="row">
        <Field label={t('layoutStyle')}><Select value={s.layout_style} onChange={set('layout_style')} options={[['classic', 'classic (framed picture + text)'], ['full_bleed', 'full-bleed picture + text panel']]} /></Field>
        <Field label={t('bleed')}><input type="number" min={0} max={6} step={0.5} value={s.bleed_mm} onChange={(e) => set('bleed_mm')(Number(e.target.value))} /></Field>
        <Field label={t('colorMode')} hint={s.color_mode === 'cmyk' ? t('cmykNote') : ''}><Select value={s.color_mode} onChange={set('color_mode')} options={['rgb', 'cmyk']} /></Field>
      </div>
      <div className="row checks">
        <label><input type="checkbox" checked={s.crop_marks} onChange={(e) => set('crop_marks')(e.target.checked)} /> {t('cropMarks')}</label>
        <label><input type="checkbox" checked={s.cast_reference} onChange={(e) => set('cast_reference')(e.target.checked)} /> {t('castReference')}</label>
      </div>
      <h3>🔊 {t('narrationAudio')}</h3>
      <div className="row">
        <label className="checks"><input type="checkbox" checked={s.narration_audio} onChange={(e) => set('narration_audio')(e.target.checked)} /> {t('narrationAudio')}</label>
        <Field label={t('ttsModel')}><Select value={s.tts_provider_id} onChange={set('tts_provider_id')} options={[['', '★ default'], ...tts.map((p) => [p.id, p.name])]} /></Field>
        <Field label={t('voice')}><input dir="ltr" value={s.narration_voice} maxLength={60} onChange={(e) => set('narration_voice')(e.target.value)} /></Field>
        <Field label={`${t('speed')}: ${s.narration_speed}`}><input type="range" min={0.6} max={1.5} step={0.05} value={s.narration_speed} onChange={(e) => set('narration_speed')(Number(e.target.value))} /></Field>
      </div>
      <button className="btn primary" disabled={busy} onClick={() => run(async () => {
        await api(`/api/projects/${b.id}`, { method: 'PATCH', body: { title, settings: s } })
        await api(`/api/projects/${b.id}/rebuild`, { method: 'POST' })
      })}>💾 {t('save')} + {t('rebuild')}</button>
      <pre className="settings-dump">{JSON.stringify(b.settings, null, 1)}</pre>
    </div>
  )
}
