import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from '../App'

// Minimal fake backend: routes -> responses, records every call.
function mockApi(routes) {
  const calls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, opts = {}) => {
    const method = opts.method || 'GET'
    calls.push({ url, method, body: opts.body ? (typeof opts.body === 'string' ? JSON.parse(opts.body) : opts.body) : null })
    const key = Object.keys(routes).find((k) => { const [m, p] = k.split(' '); return m === method && new RegExp('^' + p + '$').test(url.split('?')[0]) })
    if (!key) return new Response(JSON.stringify({ detail: 'not mocked ' + method + ' ' + url }), { status: 404 })
    const r = typeof routes[key] === 'function' ? routes[key](url, opts) : routes[key]
    const status = r && r.__status ? r.__status : 200
    return new Response(JSON.stringify(r && r.__body !== undefined ? r.__body : r), { status })
  })
  return calls
}

const SYSTEM = { queue: { queued: 0, running: 1 }, projects: {}, data_bytes: 1024, disk_free: 1e10, disk_total: 2e10, load: [0.1, 0, 0], cpus: 2,
  limits: { books: 1, images: 2 }, usage: { cost_usd: 0, calls: 3, images: 12, input_tokens: 0, output_tokens: 0 } }
const PROVIDERS = { defaults: { llm: 'builtin-offline-story', image: 'builtin-procedural-image', vision: '' }, types: { llm: ['offline', 'ollama'], image: ['offline', 'comfyui'], vision: ['ollama'] },
  providers: [{ id: 'builtin-offline-story', name: 'Built-in story engine', kind: 'llm', provider_type: 'offline', params: {}, enabled: true, builtin: true, api_key: '' },
    { id: 'builtin-procedural-image', name: 'Built-in illustrator', kind: 'image', provider_type: 'offline', params: {}, enabled: true, builtin: true, api_key: '' }] }
const EST = { pages: 12, llm_calls: 0, images_min: 16, images_max: 42, cost_min_usd: 0, cost_max_usd: 0, minutes_estimate: 1.2, paid: false, warnings: [],
  providers: { llm: 'Built-in story engine', image: 'Built-in illustrator' } }
const page = (n, extra = {}) => ({ id: `p${n}`, number: n, beat: 'rising', text: `Text of page ${n}.`, image_prompt: `brief ${n}`, scene: {}, characters_present: ['hero'],
  image_url: `/api/projects/abc123/files/images/page-${n}.png`, image_status: 'generated', image_locked: false, image_meta: { attempts: [] }, qa: {}, ...extra })
const BOOK = (status = 'completed', extra = {}) => ({ id: 'abc123', title: 'Luna and the Star', author: 'A', language: 'en', age_group: '6-8', status, stage: 'qa', progress: status === 'running' ? 40 : 100,
  archived: false, version: 1, has_pdf: true, created_at: '2026-10-15T10:00:00Z', updated_at: '2026-10-15T10:00:00Z', error: '', cover_url: null, page_count: 3,
  qa_summary: { errors: 0, warnings: 1 }, settings: { page_size: 'A4', orientation: 'portrait', cover_style: 'illustrated', page_numbers: true, back_cover: true, output_profile: 'screen', author: 'A', publisher: '' },
  stage_state: {}, plan: { engine: 'offline:lost_star' }, pages: [page(1), page(2), page(3)],
  characters: [{ id: 'c1', char_id: 'hero', name: 'Luna', role: 'protagonist', spec: { description: 'a girl', appearance: { species: 'human', skin: '#f6d5b8', hair_color: '#2b1d14' }, outfit: { top_color: '#e94f4f', accessory_color: '#3d8bd9' }, distinctive: ['red dress'] }, ref_url: null, ref_approved: true }],
  jobs: [], renders: [1, 2, 3, 4].map((i) => ({ page: i, url: `/r/page-${i}.jpg`, thumb: `/r/thumb-${i}.jpg` })), pdf_url: '/api/projects/abc123/pdf',
  qa_report: { summary: { status, errors: 0, warnings: 1, pdf_valid: true, story_pages: 3, word_count: 50, engines: {} },
    story: { issues: [{ code: 'long_sentences', severity: 'warning', message: 'Average sentence length high', page: 2, stage: 'story' }] }, images: { issues: [] }, layout: { issues: [] },
    pdf: { ok: true, issues: [], info: { pages: 8, size_mb: 1.1, lang: 'en', rendered: 8, fonts: [{ name: 'Andika', embedded: true }], metadata: { '/Title': 'Luna and the Star' } } } }, ...extra })

const go = (hash) => act(() => { window.location.hash = hash; window.dispatchEvent(new HashChangeEvent('hashchange')) })

describe('Bookmaker UI', () => {
  it('dashboard shows books and resource stats', async () => {
    mockApi({ 'GET /api/system': SYSTEM, 'GET /api/projects': [BOOK('completed'), { ...BOOK('running'), id: 'def456', title: 'Running book' }] })
    render(<App />)
    expect(await screen.findByText('Running book')).toBeInTheDocument()
    expect(screen.getAllByTestId('book-card')).toHaveLength(2)
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
  })

  it('creates a book from the form and navigates to it', async () => {
    const calls = mockApi({ 'GET /api/presets': [{ id: 'x', name: { en: 'Bedtime story' }, settings: { age_group: '3-5', page_count: 10 } }], 'GET /api/providers': PROVIDERS,
      'POST /api/estimate': EST, 'POST /api/projects': { id: 'abc123' }, 'GET /api/projects/abc123': BOOK('queued') })
    render(<App />)
    await go('#/new')
    fireEvent.click(await screen.findByText('Bedtime story'))
    fireEvent.change(screen.getByPlaceholderText(/brave girl/), { target: { value: 'A turtle who learns to swim' } })
    await waitFor(() => expect(screen.getByTestId('estimate')).toHaveTextContent('16–42'))
    await waitFor(() => expect(screen.getByTestId('start')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('start'))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/projects')).toBe(true))
    const body = calls.find((c) => c.method === 'POST' && c.url === '/api/projects').body
    expect(body.settings.topic).toBe('A turtle who learns to swim')
    expect(body.settings.age_group).toBe('3-5')
    expect(body.settings.page_count).toBe(10)
    await waitFor(() => expect(window.location.hash).toBe('#/book/abc123'))
  })

  it('blocks generation when the estimate reports a problem', async () => {
    mockApi({ 'GET /api/presets': [], 'GET /api/providers': PROVIDERS, 'POST /api/estimate': { ...EST, warnings: ['The offline story engine supports 6-18 pages'] } })
    render(<App />)
    await go('#/new')
    expect(await screen.findByText(/supports 6-18 pages/)).toBeInTheDocument()
    expect(screen.getByTestId('start')).toBeDisabled()
  })

  it('edits page text and requests image regeneration', async () => {
    const calls = mockApi({ 'GET /api/projects/abc123': BOOK(), 'PATCH /api/projects/abc123/pages/p1': page(1), 'POST /api/projects/abc123/pages/p1/regenerate-image': { job_id: 'j' } })
    render(<App />)
    await go('#/book/abc123/pages')
    const ta = await screen.findByTestId('page-text')
    fireEvent.change(ta, { target: { value: 'New text by the user.' } })
    fireEvent.click(screen.getByTestId('save-page'))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body.text).toBe('New text by the user.'))
    fireEvent.click(screen.getByText(/Regenerate image/))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/regenerate-image'))).toBe(true))
  })

  it('shows progress for a running book and the QA report with issues', async () => {
    mockApi({ 'GET /api/projects/abc123': BOOK('running') })
    render(<App />)
    await go('#/book/abc123/qa')
    expect(await screen.findByRole('progressbar')).toBeInTheDocument()
    expect(screen.getByText(/long_sentences/)).toBeInTheDocument()
    expect(screen.getByText(/Andika \(embedded\)/)).toBeInTheDocument()
  })

  it('previews the rendered PDF pages as a book with navigation', async () => {
    mockApi({ 'GET /api/projects/abc123': BOOK() })
    render(<App />)
    await go('#/book/abc123/preview')
    expect(await screen.findByAltText('page 1')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('next'))
    await waitFor(() => expect(screen.getByAltText('page 2')).toBeInTheDocument())
    expect(screen.getByAltText('page 3')).toBeInTheDocument()  // two-page spread
    fireEvent.click(screen.getByText('Single page'))
    expect(screen.getAllByAltText(/^page \d+$/)).toHaveLength(1)
  })

  it('offers the PDF download link', async () => {
    mockApi({ 'GET /api/projects/abc123': BOOK() })
    render(<App />)
    await go('#/book/abc123')
    const a = await screen.findByTestId('download')
    expect(a).toHaveAttribute('href', '/api/projects/abc123/pdf')
  })

  it('manages providers: add and test connection', async () => {
    const calls = mockApi({ 'GET /api/providers': PROVIDERS, 'GET /api/usage': [], 'POST /api/providers': { id: 'n' },
      'POST /api/providers/builtin-offline-story/check': { ok: true } })
    render(<App />)
    await go('#/providers')
    fireEvent.click((await screen.findAllByText('Test connection'))[0])
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/check'))).toBe(true))
    fireEvent.click(screen.getByText(/Add provider/))
    const inputs = screen.getByRole('dialog').querySelectorAll('input')
    fireEvent.change(inputs[0], { target: { value: 'My Ollama' } })
    fireEvent.change(inputs[1], { target: { value: 'http://localhost:11434' } })
    fireEvent.change(inputs[2], { target: { value: 'llama3.1:8b' } })
    fireEvent.click(within(screen.getByRole('dialog')).getByText(/Save/))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.url === '/api/providers')?.body.base_url).toBe('http://localhost:11434'))
  })

  it('switches the interface to Persian with RTL direction', async () => {
    mockApi({ 'GET /api/system': SYSTEM, 'GET /api/projects': [] })
    render(<App />)
    fireEvent.click(screen.getByLabelText('language'))
    await waitFor(() => expect(document.documentElement.dir).toBe('rtl'))
    expect(screen.getAllByText('کتاب جدید').length).toBeGreaterThan(0)
  })

  it('shows API errors to the user', async () => {
    mockApi({ 'GET /api/projects/abc123': { __status: 404, __body: { detail: 'Book not found' } } })
    render(<App />)
    await go('#/book/abc123')
    expect(await screen.findByText('Book not found')).toBeInTheDocument()
  })
})
