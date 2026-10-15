import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import { Field, Select, useAsync } from '../components/common'
import { useT } from '../i18n'
import { navigate } from '../App'

export const DEFAULTS = {
  title: '', topic: '', moral: '', age_group: '6-8', language: 'en', page_count: 12, complexity: 'medium', narration: 'third_person',
  illustration_style: 'watercolor', aspect_ratio: '4:3', page_size: 'A4', orientation: 'portrait', main_characters: 2, hero_name: '',
  text_amount: 'medium', fantasy_level: 2, humor_level: 1, ending: 'happy', cover_style: 'illustrated', author: '', publisher: '',
  page_numbers: true, back_cover: true, text_in_image: false, output_profile: 'screen', seed: null, llm_provider_id: '', image_provider_id: '',
  vision_provider_id: '', max_image_attempts: 3, max_story_attempts: 3, budget_usd: 0,
}

export default function NewBook() {
  const { t, lang, notify } = useT()
  const [s, setS] = useState({ ...DEFAULTS, language: lang })
  const [est, setEst] = useState(null)
  const [estErr, setEstErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [presets] = useAsync(() => api('/api/presets'), [])
  const [prov] = useAsync(() => api('/api/providers'), [])
  const set = (k) => (v) => setS((o) => ({ ...o, [k]: v }))
  const num = (k) => (e) => set(k)(e.target.value === '' ? null : Number(e.target.value))

  useEffect(() => {
    const id = setTimeout(async () => {
      try { setEst(await api('/api/estimate', { method: 'POST', body: s })); setEstErr('') }
      catch (e) { setEst(null); setEstErr(e.message) }
    }, 350)
    return () => clearTimeout(id)
  }, [s])

  const providers = prov.data?.providers || []
  const opts = (kind, allowNone) => [['', allowNone ? `— ${t('none')} —` : `★ default`], ...providers.filter((p) => p.kind === kind && p.enabled).map((p) => [p.id, p.name])]

  async function submit(start) {
    setBusy(true)
    try {
      const b = await api('/api/projects', { method: 'POST', body: { settings: s, start, confirm_cost: confirm } })
      notify(start ? '✓ Generation started' : '✓ Saved', 'ok')
      navigate(`/book/${b.id}`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) notify(t('paidWarn'), 'warn')
      else notify(e.message, 'bad')
    } finally { setBusy(false) }
  }

  return (
    <div className="page">
      <h1>{t('newBook')}</h1>
      {presets.data && (
        <div className="presets">
          <span>{t('presets')}:</span>
          {presets.data.map((p) => <button key={p.id} className="chip" onClick={() => setS((o) => ({ ...o, ...p.settings, language: o.language }))}>{p.name[lang] || p.name.en}</button>)}
        </div>
      )}
      <div className="two-col">
        <form className="card form" onSubmit={(e) => { e.preventDefault(); submit(true) }}>
          <h3>📖 {t('story')}</h3>
          <Field label={t('topic')}><textarea dir="auto" rows={2} value={s.topic} maxLength={600} onChange={(e) => set('topic')(e.target.value)} placeholder="e.g. a brave girl who helps a fallen star get home" /></Field>
          <div className="row">
            <Field label={t('title')} hint={t('titleHint')}><input dir="auto" value={s.title} maxLength={200} onChange={(e) => set('title')(e.target.value)} /></Field>
            <Field label={t('heroName')}><input dir="auto" value={s.hero_name} maxLength={40} onChange={(e) => set('hero_name')(e.target.value)} /></Field>
          </div>
          <Field label={t('moral')}><input dir="auto" value={s.moral} maxLength={300} onChange={(e) => set('moral')(e.target.value)} /></Field>
          <div className="row">
            <Field label={t('language')}><Select value={s.language} onChange={set('language')} options={[['en', 'English'], ['fa', 'فارسی (Persian)']]} /></Field>
            <Field label={t('age')}><Select value={s.age_group} onChange={set('age_group')} options={['3-5', '6-8', '9-12']} /></Field>
            <Field label={t('pages')}><input type="number" min={4} max={40} value={s.page_count} onChange={num('page_count')} /></Field>
          </div>
          <div className="row">
            <Field label={t('textAmount')}><Select value={s.text_amount} onChange={set('text_amount')} options={['short', 'medium', 'long']} /></Field>
            <Field label={t('complexity')}><Select value={s.complexity} onChange={set('complexity')} options={['simple', 'medium', 'rich']} /></Field>
            <Field label={t('narration')}><Select value={s.narration} onChange={set('narration')} options={['third_person', 'first_person']} /></Field>
          </div>
          <div className="row">
            <Field label={`${t('fantasy')}: ${s.fantasy_level}`}><input type="range" min={0} max={3} value={s.fantasy_level} onChange={num('fantasy_level')} /></Field>
            <Field label={`${t('humor')}: ${s.humor_level}`}><input type="range" min={0} max={3} value={s.humor_level} onChange={num('humor_level')} /></Field>
            <Field label={t('mainChars')}><input type="number" min={1} max={4} value={s.main_characters} onChange={num('main_characters')} /></Field>
          </div>
          <Field label={t('ending')}><Select value={s.ending} onChange={set('ending')} options={['happy', 'calm', 'surprise', 'open']} /></Field>

          <h3>🎨 {t('style')} & {t('layout')}</h3>
          <div className="row">
            <Field label={t('style')}><Select value={s.illustration_style} onChange={set('illustration_style')} options={['watercolor', 'flat_vector', 'crayon', 'paper_cutout', 'gouache']} /></Field>
            <Field label={t('aspect')}><Select value={s.aspect_ratio} onChange={set('aspect_ratio')} options={['4:3', '3:2', '1:1', '3:4', '16:9']} /></Field>
            <Field label={t('coverStyle')}><Select value={s.cover_style} onChange={set('cover_style')} options={['illustrated', 'framed', 'minimal']} /></Field>
          </div>
          <div className="row">
            <Field label={t('pageSize')}><Select value={s.page_size} onChange={set('page_size')} options={['A4', 'A5', 'Letter', 'Square8']} /></Field>
            <Field label={t('orientation')}><Select value={s.orientation} onChange={set('orientation')} options={['portrait', 'landscape']} /></Field>
            <Field label={t('profile')}><Select value={s.output_profile} onChange={set('output_profile')} options={[['screen', 'screen (150 dpi)'], ['print', 'print (300 dpi)']]} /></Field>
          </div>
          <div className="row checks">
            <label><input type="checkbox" checked={s.page_numbers} onChange={(e) => set('page_numbers')(e.target.checked)} /> {t('pageNumbers')}</label>
            <label><input type="checkbox" checked={s.back_cover} onChange={(e) => set('back_cover')(e.target.checked)} /> {t('backCover')}</label>
            <label><input type="checkbox" checked={s.text_in_image} onChange={(e) => set('text_in_image')(e.target.checked)} /> {t('textInImage')}</label>
          </div>
          <div className="row">
            <Field label={t('author')}><input dir="auto" value={s.author} maxLength={120} onChange={(e) => set('author')(e.target.value)} /></Field>
            <Field label={t('publisher')}><input dir="auto" value={s.publisher} maxLength={120} onChange={(e) => set('publisher')(e.target.value)} /></Field>
          </div>

          <h3>🤖 {t('providers')}</h3>
          <div className="row">
            <Field label={t('llm')}><Select value={s.llm_provider_id} onChange={set('llm_provider_id')} options={opts('llm')} /></Field>
            <Field label={t('imageModel')}><Select value={s.image_provider_id} onChange={set('image_provider_id')} options={opts('image')} /></Field>
            <Field label={t('vision')}><Select value={s.vision_provider_id} onChange={set('vision_provider_id')} options={opts('vision', true)} /></Field>
          </div>
          <div className="row">
            <Field label="Max image attempts"><input type="number" min={1} max={6} value={s.max_image_attempts} onChange={num('max_image_attempts')} /></Field>
            <Field label="Budget USD (0 = none)"><input type="number" min={0} step="0.5" value={s.budget_usd} onChange={num('budget_usd')} /></Field>
            <Field label={t('seed')}><input type="number" min={0} value={s.seed ?? ''} onChange={num('seed')} /></Field>
          </div>
        </form>

        <aside className="card sticky" data-testid="estimate">
          <h3>🔎 {t('estimate')}</h3>
          {estErr && <p className="bad-text">{estErr}</p>}
          {est && (
            <>
              <dl className="kv">
                <dt>{t('llm')}</dt><dd>{est.providers.llm}</dd>
                <dt>{t('imageModel')}</dt><dd>{est.providers.image}</dd>
                <dt>{t('pages')}</dt><dd>{est.pages}</dd>
                <dt>LLM calls</dt><dd>{est.llm_calls}</dd>
                <dt>{t('images')}</dt><dd>{est.images_min}–{est.images_max}</dd>
                <dt>≈ time</dt><dd>{est.minutes_estimate} {t('minutes')}</dd>
                <dt>{t('cost')}</dt><dd>{est.paid ? `$${est.cost_min_usd} – $${est.cost_max_usd}` : '$0 (local/offline)'}</dd>
              </dl>
              {est.warnings.map((w, i) => <p key={i} className="warn-text">⚠ {w}</p>)}
              {est.paid && <label className="confirm"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> {t('confirm')}</label>}
            </>
          )}
          <div className="summary-preview" dir="auto">
            <b>{s.title || '…'}</b>
            <small>{s.language.toUpperCase()} · {s.age_group} · {s.page_count} p · {s.illustration_style} · {s.page_size} {s.orientation}</small>
          </div>
          <button className="btn primary big" disabled={busy || !!estErr || (est && est.warnings.length > 0) || (est?.paid && !confirm)} onClick={() => submit(true)} data-testid="start">
            {busy ? '…' : `✨ ${t('start')}`}
          </button>
          <button className="btn" disabled={busy} onClick={() => submit(false)}>{t('saveDraft')}</button>
        </aside>
      </div>
    </div>
  )
}
