import { useState } from 'react'
import { api } from '../api'
import { Field, Select, useAsync } from '../components/common'
import { useT } from '../i18n'

const EMPTY = { id: null, name: '', kind: 'llm', provider_type: 'ollama', base_url: '', api_key: '', model: '', fallback_id: '', enabled: true,
  rpm: 0, max_retries: 3, timeout: 300, input_per_1k: 0, output_per_1k: 0, per_image: 0, per_1k_chars: 0, workflow: '', seconds_per_image: 20, advanced: '{}' }
const HINTS = {
  ollama: { base_url: 'http://localhost:11434', model: 'llama3.1:8b  (vision: llava / qwen2.5vl)' },
  openai_compatible: { base_url: 'https://api.openai.com/v1  ·  http://localhost:1234/v1 (LM Studio)  ·  https://openrouter.ai/api/v1', model: 'gpt-4o-mini · gpt-image-1 · ...' },
  anthropic: { base_url: 'https://api.anthropic.com', model: 'claude-…' },
  comfyui: { base_url: 'http://127.0.0.1:8188', model: 'checkpoint file name, e.g. sd_xl_base_1.0.safetensors' },
  automatic1111: { base_url: 'http://127.0.0.1:7860  ·  stable-diffusion.cpp server (A1111-compatible API)', model: 'optional checkpoint title' },
  fastsdcpu: { base_url: 'http://127.0.0.1:8000  (python src/app.py --api) – CPU only, no GPU needed', model: 'stabilityai/sd-turbo · rupeshs/sdxs-512-0.9 · …' },
  iopaint: { base_url: 'http://127.0.0.1:8080  (iopaint start --model=lama --device=cpu)', model: 'lama (informational)' },
}
const TTS_HINTS = { base_url: 'http://localhost:8880/v1 (Kokoro-FastAPI) · http://localhost:8080/v1 (LocalAI) · https://api.openai.com/v1', model: 'kokoro · tts-1 · …' }

export default function Providers({ admin = true }) {
  const { t, notify } = useT()
  const [st, reload] = useAsync(() => api('/api/providers'), [])
  const [usage] = useAsync(() => api('/api/usage'), [])
  const [form, setForm] = useState(null)
  const data = st.data
  const run = async (fn, ok = '✓') => { try { const r = await fn(); notify(ok, 'ok'); await reload(); return r } catch (e) { notify(e.message, 'bad') } }
  const set = (k) => (v) => setForm((f) => ({ ...f, [k]: v }))
  const edit = (p) => setForm({ ...EMPTY, ...p, api_key: '', rpm: p.params.rpm || 0, max_retries: p.params.max_retries ?? 3, timeout: p.params.timeout || 300,
    input_per_1k: p.params.pricing?.input_per_1k || 0, output_per_1k: p.params.pricing?.output_per_1k || 0, per_image: p.params.pricing?.per_image || 0, per_1k_chars: p.params.pricing?.per_1k_chars || 0,
    seconds_per_image: p.params.seconds_per_image || 20, workflow: '',
    advanced: JSON.stringify(Object.fromEntries(Object.entries(p.params || {}).filter(([k]) => !['rpm', 'max_retries', 'timeout', 'pricing', 'seconds_per_image'].includes(k))), null, 1) })
  const save = () => run(async () => {
    let advanced = {}
    try { advanced = JSON.parse(form.advanced || '{}') } catch { throw new Error('Advanced params must be valid JSON') }
    const body = { name: form.name, kind: form.kind, provider_type: form.provider_type, base_url: form.base_url, model: form.model, enabled: form.enabled,
      fallback_id: form.fallback_id, api_key: form.api_key ? form.api_key : (form.id ? null : ''),
      params: { ...advanced, rpm: Number(form.rpm), max_retries: Number(form.max_retries), timeout: Number(form.timeout), seconds_per_image: Number(form.seconds_per_image),
        pricing: { input_per_1k: Number(form.input_per_1k), output_per_1k: Number(form.output_per_1k), per_image: Number(form.per_image), per_1k_chars: Number(form.per_1k_chars) } },
      workflow: form.workflow ? form.workflow : null }
    await api(form.id ? `/api/providers/${form.id}` : '/api/providers', { method: form.id ? 'PUT' : 'POST', body })
    setForm(null)
  }, '✓ saved')
  const types = data?.types || {}
  const defaults = data?.defaults || {}
  const byKind = (k) => (data?.providers || []).filter((p) => p.kind === k)
  return (
    <div className="page">
      <h1>{t('providers')}</h1>
      <p className="muted">{t('offlineNote')}</p>
      {data && (
        <div className="card">
          <h3>{t('defaults')}</h3>
          <div className="row">
            {[['llm', t('llm'), ['llm']], ['image', t('imageModel'), ['image']], ['vision', t('vision'), ['vision']], ['tts', t('ttsDefault'), ['tts']],
              ['inpaint', t('inpaintModel'), ['inpaint', 'image']]].map(([key, label, kinds]) => (
              <Field key={key} label={label}>
                <Select value={defaults[key] || ''} disabled={!admin} onChange={(v) => run(() => api('/api/providers-defaults', { method: 'PUT', body: { ...defaults, [key]: v } }))}
                  options={[...(key === 'vision' || key === 'tts' ? [['', `— ${t('none')} —`]] : []), ...(key === 'inpaint' ? [['', t('engineClassical')]] : []),
                    ...(data.providers || []).filter((p) => kinds.includes(p.kind) && !(key === 'inpaint' && p.provider_type === 'fastsdcpu')).map((p) => [p.id, `${p.name}${kinds.length > 1 ? ` (${p.kind})` : ''}`])]} />
              </Field>
            ))}
          </div>
        </div>
      )}
      {admin && <div className="row-inline"><button className="btn primary" onClick={() => setForm({ ...EMPTY })}>➕ {t('addProvider')}</button></div>}
      <table className="table">
        <thead><tr><th>{t('name')}</th><th>{t('kind')}</th><th>{t('type')}</th><th>{t('model')}</th><th>{t('apiKey')}</th><th>{t('status')}</th><th></th></tr></thead>
        <tbody>
          {(data?.providers || []).map((p) => (
            <tr key={p.id}>
              <td>{p.name}{p.builtin && <span className="pill">built-in</span>}{p.fallback_id && <small className="muted"> → fallback</small>}</td>
              <td>{p.kind}</td><td>{p.provider_type}</td><td>{p.model}</td><td><code>{p.api_key || '-'}</code></td>
              <td>{p.last_check?.checked_at ? (p.last_check.ok ? <span className="badge ok">ok · {p.last_check.latency_ms}ms</span>
                : <span className="badge bad" title={p.last_check.error}>error</span>) : <span className="muted">—</span>}
                {p.last_check?.error && <div className="bad-text small">{p.last_check.error}</div>}
                {p.last_check?.note && <div className="muted small">{p.last_check.note}</div>}</td>
              <td className="actions">{admin && <>
                <button className="btn small" onClick={() => run(() => api(`/api/providers/${p.id}/check`, { method: 'POST' }), 'checked')}>{t('check')}</button>
                {!p.builtin && <button className="btn small" onClick={() => edit(p)}>{t('edit')}</button>}
                {!p.builtin && <button className="btn small danger" onClick={() => window.confirm(`${t('delete')} ${p.name}?`) && run(() => api(`/api/providers/${p.id}`, { method: 'DELETE' }))}>{t('delete')}</button>}
              </>}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {form && (
        <div className="modal" role="dialog">
          <div className="card form">
            <h3>{form.id ? t('edit') : t('addProvider')}</h3>
            <div className="row">
              <Field label={t('name')}><input value={form.name} onChange={(e) => set('name')(e.target.value)} /></Field>
              <Field label={t('kind')}><Select value={form.kind} onChange={(v) => setForm((f) => ({ ...f, kind: v, provider_type: types[v]?.find((x) => x !== 'offline') || 'ollama' }))} options={Object.keys(types)} /></Field>
              <Field label={t('type')}><Select value={form.provider_type} onChange={set('provider_type')} options={(types[form.kind] || []).filter((x) => x !== 'offline')} /></Field>
            </div>
            <Field label={t('baseUrl')} hint={(form.kind === 'tts' ? TTS_HINTS : HINTS[form.provider_type])?.base_url}><input dir="ltr" value={form.base_url} onChange={(e) => set('base_url')(e.target.value)} /></Field>
            <Field label={t('model')} hint={(form.kind === 'tts' ? TTS_HINTS : HINTS[form.provider_type])?.model}><input dir="ltr" value={form.model} onChange={(e) => set('model')(e.target.value)} /></Field>
            <Field label={t('apiKey')} hint={`${form.id ? t('keepKey') + ' · ' : ''}env:VARIABLE_NAME reads the key from the server environment`}>
              <input dir="ltr" type="password" autoComplete="off" value={form.api_key} onChange={(e) => set('api_key')(e.target.value)} /></Field>
            <div className="row">
              <Field label={t('rpm')}><input type="number" min={0} value={form.rpm} onChange={(e) => set('rpm')(e.target.value)} /></Field>
              <Field label="Max retries"><input type="number" min={0} max={8} value={form.max_retries} onChange={(e) => set('max_retries')(e.target.value)} /></Field>
              <Field label="Timeout (s)"><input type="number" min={5} value={form.timeout} onChange={(e) => set('timeout')(e.target.value)} /></Field>
            </div>
            <div className="row">
              <Field label={`${t('pricing')} / 1k input`}><input type="number" step="0.0001" min={0} value={form.input_per_1k} onChange={(e) => set('input_per_1k')(e.target.value)} /></Field>
              <Field label="/ 1k output"><input type="number" step="0.0001" min={0} value={form.output_per_1k} onChange={(e) => set('output_per_1k')(e.target.value)} /></Field>
              {form.kind === 'tts'
                ? <Field label="/ 1k characters"><input type="number" step="0.001" min={0} value={form.per_1k_chars} onChange={(e) => set('per_1k_chars')(e.target.value)} /></Field>
                : <Field label="/ image"><input type="number" step="0.001" min={0} value={form.per_image} onChange={(e) => set('per_image')(e.target.value)} /></Field>}
            </div>
            <Field label={t('fallback')}><Select value={form.fallback_id} onChange={set('fallback_id')} options={[['', `— ${t('none')} —`], ...byKind(form.kind).filter((p) => p.id !== form.id).map((p) => [p.id, p.name])]} /></Field>
            {form.provider_type === 'comfyui' && <Field label={t('workflow')} hint="Placeholders: {{prompt}} {{negative}} {{width}} {{height}} {{seed}} {{checkpoint}} {{ref_image_1}}…  (empty = built-in txt2img workflow / keep existing)">
              <textarea dir="ltr" rows={5} value={form.workflow} onChange={(e) => set('workflow')(e.target.value)} /></Field>}
            <Field label="Advanced params (JSON)" hint='e.g. {"json_mode": false} or {"controlnet_unit": {...}} or {"options": {"num_ctx": 8192}}'>
              <textarea dir="ltr" rows={3} value={form.advanced} onChange={(e) => set('advanced')(e.target.value)} /></Field>
            <div className="row-inline">
              <button className="btn primary" onClick={save}>💾 {t('save')}</button>
              <button className="btn" onClick={() => setForm(null)}>{t('cancel')}</button>
            </div>
          </div>
        </div>
      )}

      <h2>{t('usage')}</h2>
      <table className="table small">
        <thead><tr><th>time</th><th>provider</th><th>purpose</th><th>tokens in/out</th><th>images</th><th>ms</th><th>cost</th><th></th></tr></thead>
        <tbody>{(usage.data || []).slice(0, 60).map((u, i) => (
          <tr key={i} className={u.success ? '' : 'error'}><td>{new Date(u.at).toLocaleTimeString()}</td><td>{u.provider}</td><td>{u.purpose}</td>
            <td>{u.input_tokens}/{u.output_tokens}</td><td>{u.images}</td><td>{u.duration_ms}</td><td>${u.cost_usd.toFixed(4)}</td><td className="small">{u.error}</td></tr>
        ))}</tbody>
      </table>
    </div>
  )
}
