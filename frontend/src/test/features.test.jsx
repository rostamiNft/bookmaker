import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../App'

function mockApi(routes) {
  const calls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, opts = {}) => {
    const method = opts.method || 'GET'
    let body = null
    if (opts.body) body = typeof opts.body === 'string' ? JSON.parse(opts.body) : opts.body
    calls.push({ url, method, body, headers: opts.headers || {} })
    const key = Object.keys(routes).find((k) => { const [m, p] = k.split(' '); return m === method && new RegExp('^' + p + '$').test(url.split('?')[0]) })
    if (!key) return new Response(JSON.stringify({ detail: 'not mocked ' + method + ' ' + url }), { status: 404 })
    const r = typeof routes[key] === 'function' ? routes[key](url, opts) : routes[key]
    return new Response(JSON.stringify(r && r.__body !== undefined ? r.__body : r), { status: r && r.__status ? r.__status : 200 })
  })
  return calls
}
const go = (hash) => act(() => { window.location.hash = hash; window.dispatchEvent(new HashChangeEvent('hashchange')) })
const page = (n, extra = {}) => ({ id: `p${n}`, number: n, beat: 'rising', text: `Text of page ${n}.`, image_prompt: `brief ${n}`, scene: {}, characters_present: ['hero'],
  image_url: `/api/projects/abc123/files/images/page-${n}.png`, image_status: 'generated', image_locked: false, image_meta: {}, qa: {}, audio_url: null, inpaints: 0, ...extra })
const BOOK = (status, extra = {}) => ({ id: 'abc123', title: 'Luna', language: 'en', age_group: '6-8', status, stage: 'plan', progress: 20, version: 2, pages: [page(1), page(2)],
  characters: [{ id: 'c1', char_id: 'hero', name: 'Luna', role: 'protagonist', spec: { appearance: { species: 'human', scale: 1 }, outfit: {} }, ref_url: '/x.png', ref_approved: true }],
  settings: { page_size: 'A4', orientation: 'portrait', cover_style: 'illustrated', page_numbers: true, back_cover: true, output_profile: 'screen' },
  stage_state: {}, plan: {}, renders: [], qa_report: {}, exports: {}, relative_heights: { hero: 0.8 }, cast_url: '/api/projects/abc123/files/refs/cast.png', ...extra })

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); window.location.hash = '#/' })

describe('new features', () => {
  it('accounts mode: shows the login form and stores the session token', async () => {
    let loggedIn = false
    const calls = mockApi({
      'GET /api/auth/status': { mode: 'accounts', needs_setup: false },
      'GET /api/auth/me': () => (loggedIn ? { id: 'u1', username: 'mina', role: 'user' } : { __status: 401, __body: { detail: 'Authentication required' } }),
      'POST /api/auth/login': () => { loggedIn = true; return { token: 'tok-123', user: { username: 'mina' } } },
      'GET /api/system': { queue: { queued: 0, running: 0 }, projects: {}, data_bytes: 1, disk_free: 1e10, disk_total: 2e10, load: [0, 0, 0], cpus: 2, limits: { books: 1, images: 1 }, usage: { cost_usd: 0, calls: 0, images: 0 } }, 'GET /api/projects': [],
    })
    render(<App />)
    fireEvent.change(await screen.findByTestId('login-user'), { target: { value: 'mina' } })
    fireEvent.change(screen.getByTestId('login-pass'), { target: { value: 'Secret-123' } })
    fireEvent.click(screen.getByTestId('login-submit'))
    expect(await screen.findByTestId('logout')).toHaveTextContent('mina')
    expect(localStorage.getItem('bm_token')).toBe('tok-123')
    const login = calls.find((c) => c.url === '/api/auth/login')
    expect(login.body).toEqual({ username: 'mina', password: 'Secret-123' })
    await waitFor(() => expect(calls.some((c) => c.url === '/api/auth/me' && c.headers.Authorization === 'Bearer tok-123')).toBe(true))
  })

  it('first run: setup form creates the administrator', async () => {
    const calls = mockApi({ 'GET /api/auth/status': { mode: 'accounts', needs_setup: true }, 'GET /api/auth/me': { __status: 401, __body: { detail: 'x' } },
      'POST /api/auth/setup': { token: 't0', user: {} } })
    render(<App />)
    expect(await screen.findByText(/Create the administrator account/)).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('login-user'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByTestId('login-pass'), { target: { value: 'Adm1n-pass' } })
    fireEvent.click(screen.getByTestId('login-submit'))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/auth/setup' && c.method === 'POST')).toBe(true))
  })

  it('outline review: banner approves the story', async () => {
    const calls = mockApi({ 'GET /api/projects/abc123': BOOK('awaiting_review'), 'POST /api/projects/abc123/approve-outline': { job_id: 'j1' },
      'GET /api/providers': { providers: [] }, 'GET /api/projects/abc123/pages/p1/revisions': [] })
    render(<App />)
    await go('#/book/abc123')
    expect(await screen.findByTestId('review-banner')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('approve-outline'))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/projects/abc123/approve-outline')).toBe(true))
  })

  it('revisions: restores an earlier text', async () => {
    const calls = mockApi({ 'GET /api/projects/abc123': BOOK('completed'), 'GET /api/providers': { providers: [] },
      'GET /api/projects/abc123/pages/p1/revisions': [{ id: 7, number: 1, reason: 'before_edit', actor: 'user', text: 'Old text.', image_url: null, at: '2026-10-15T10:00:00Z' }],
      'POST /api/projects/abc123/pages/p1/revisions/7/restore': { ok: true } })
    render(<App />)
    await go('#/book/abc123/pages')
    fireEvent.click(await screen.findByText(/Revisions.*\(1\)/))
    fireEvent.click(screen.getByText('Restore text'))
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/revisions/7/restore'))?.body).toEqual({ restore_text: true, restore_image: false, rebuild: true }))
  })

  it('narration player and repair dialog are available on a page', async () => {
    mockApi({ 'GET /api/projects/abc123': BOOK('completed', { pages: [page(1, { audio_url: '/api/projects/abc123/files/audio/page-01.mp3' }), page(2)] }),
      'GET /api/providers': { providers: [{ id: 'io', name: 'IOPaint', kind: 'inpaint', provider_type: 'iopaint', enabled: true, params: {} }] },
      'GET /api/projects/abc123/pages/p1/revisions': [] })
    render(<App />)
    await go('#/book/abc123/pages')
    expect(await screen.findByTestId('page-audio')).toHaveAttribute('src', '/api/projects/abc123/files/audio/page-01.mp3')
    fireEvent.click(screen.getByTestId('open-repair'))
    expect(await screen.findByText(/Only the painted area changes/)).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /IOPaint \(iopaint\)/ })).toBeInTheDocument()
    expect(screen.getByTestId('run-repair')).toBeDisabled() // nothing painted yet
  })

  it('exports tab builds an EPUB and shows the EPUBCheck result', async () => {
    let built = false
    const calls = mockApi({
      'GET /api/projects/abc123': () => BOOK('completed', built ? { exports: { epub: { version: 2, bytes: 123456, built_at: '2026-10-15T10:00:00Z', validation: { checked: true, ok: true, errors: 0, warnings: 0, checker: '5.3.0' } } } } : {}),
      'POST /api/projects/abc123/exports': () => { built = true; return { ok: true } },
    })
    render(<App />)
    await go('#/book/abc123/exports')
    const card = await screen.findByTestId('export-epub')
    fireEvent.click(card.querySelector('button'))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.url === '/api/projects/abc123/exports')?.body).toEqual({ format: 'epub' }))
    expect(await screen.findByText(/✓ 5.3.0/)).toBeInTheDocument()
  })

  it('character size slider sends appearance.scale', async () => {
    const calls = mockApi({ 'GET /api/projects/abc123': BOOK('completed'), 'PATCH /api/projects/abc123/characters/c1': { ok: true } })
    render(<App />)
    await go('#/book/abc123/characters')
    expect(await screen.findByTestId('cast-sheet')).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('char-scale'), { target: { value: '1.4' } })
    fireEvent.click(screen.getByText(/Save/))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body.spec.appearance.scale).toBe(1.4))
  })

  it('preview: plays the narration of the shown page and reads along', async () => {
    const play = vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
    vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    const renders = [1, 2, 3, 4, 5].map((n) => ({ page: n, url: `/r/page-${n}.jpg`, thumb: `/r/thumb-${n}.jpg` }))
    const layout = { pages: [{ index: 1, kind: 'cover' }, { index: 2, kind: 'title' }, { index: 3, kind: 'copyright' }, { index: 4, kind: 'story', number: 1 }, { index: 5, kind: 'story', number: 2 }] }
    mockApi({ 'GET /api/projects/abc123': BOOK('completed', { renders, qa_report: { layout },
      pages: [page(1, { audio_url: '/api/projects/abc123/files/audio/page-01.mp3' }), page(2, { audio_url: '/api/projects/abc123/files/audio/page-02.mp3' })] }) })
    go('#/book/abc123/preview')
    render(<App />)
    fireEvent.click(await screen.findByTestId('read-along'))
    const audio = screen.getByTestId('preview-audio')
    await waitFor(() => expect(audio.getAttribute('src')).toContain('page-01.mp3'))
    expect(play).toHaveBeenCalled()
    expect(await screen.findByTestId('read-stop')).toBeInTheDocument()
    fireEvent.ended(audio)
    await waitFor(() => expect(audio.getAttribute('src')).toContain('page-02.mp3'))
    fireEvent.ended(audio)
    expect(await screen.findByTestId('read-along')).toBeInTheDocument()
  })
})
