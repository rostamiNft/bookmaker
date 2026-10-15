// Browser end-to-end test (real Chromium, real backend, no mocks).
// Not part of `npm test`: needs a fresh server in accounts mode and a Chromium binary.
//   rm -rf /tmp/e2e-data && BOOKMAKER_DATA_DIR=/tmp/e2e-data BOOKMAKER_AUTH_MODE=accounts \
//     ../.venv/bin/uvicorn app.main:app --port 8010          (from backend/; needs requirements-media.txt for EPUBCheck + narration)
//   npm i --no-save puppeteer-core @sparticuz/chromium      (or point CHROMIUM at any Chrome/Chromium)
//   E2E_BASE=http://127.0.0.1:8010/ CHROMIUM=/path/to/chrome node e2e/browser-e2e.mjs
// Results/screenshots from the documented run: docs/test-results/browser-e2e/
import chromium from '@sparticuz/chromium'
import puppeteer from 'puppeteer-core'
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8010/'
const OUT = process.env.E2E_OUT || '/tmp/'
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)
const b = await puppeteer.launch({ executablePath: process.env.CHROMIUM || await chromium.executablePath(), args: chromium.args, headless: true, defaultViewport: { width: 1280, height: 900 } })
const p = await b.newPage()
const errors = []
p.on('pageerror', (e) => errors.push(String(e)))
p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
const api = (path, opts = {}) => p.evaluate(async (path, opts) => {
  const r = await fetch(path, { ...opts, headers: { Authorization: 'Bearer ' + localStorage.getItem('bm_token'), 'Content-Type': 'application/json' } })
  return { status: r.status, body: await r.json().catch(() => null) }
}, path, opts)
const waitText = (sel, re, timeout = 60000) => p.waitForFunction((sel, re) => [...document.querySelectorAll(sel)].some((e) => new RegExp(re).test(e.textContent)), { timeout, polling: 500 }, sel, re)
const results = []
const ok = (name, cond, extra = '') => { results.push({ name, ok: !!cond, extra }); log(cond ? 'PASS' : 'FAIL', name, extra) }

await p.goto(BASE, { waitUntil: 'networkidle0' })
await p.waitForSelector('[data-testid=login-form]')
ok('first-run setup screen', await p.$eval('[data-testid=login-form]', (e) => e.textContent.includes('administrator')))
await p.type('[data-testid=login-user]', 'admin')
await p.type('[data-testid=login-pass]', 'Adm1n-pass')
await p.click('[data-testid=login-submit]')
await p.waitForSelector('[data-testid=logout]')
ok('admin logged in', await p.$eval('[data-testid=logout]', (e) => e.textContent.includes('admin')))

// create a Persian book with outline review + narration
await p.evaluate(() => { location.hash = '#/new' })
await p.waitForSelector('[data-testid=start]')
await p.evaluate(() => {
  const sel = [...document.querySelectorAll('select')].find((s) => [...s.options].some((o) => o.value === 'fa'))
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set
  setter.call(sel, 'fa'); sel.dispatchEvent(new Event('change', { bubbles: true }))
})
await p.evaluate(() => {
  const el = document.querySelector('input[type=number][min="4"]')
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  setter.call(el, '8'); el.dispatchEvent(new Event('input', { bubbles: true }))
})
await p.type('textarea', 'یک خرگوش کوچک که از تاریکی می‌ترسد')
await p.click('[data-testid=opt-review]')
await p.click('[data-testid=opt-narration]')
await p.waitForFunction(() => !document.querySelector('[data-testid=start]').disabled, { timeout: 20000 }).catch(async (e) => { await p.screenshot({ path: OUT + 'fail.png', fullPage: true }); console.log(await p.$eval('[data-testid=estimate]', (x) => x.textContent)); throw e })
await p.click('[data-testid=start]')
await p.waitForFunction(() => location.hash.startsWith('#/book/'), { timeout: 20000 })
const bookId = await p.evaluate(() => location.hash.split('/')[2])
log('book', bookId)
await p.waitForSelector('[data-testid=review-banner]', { timeout: 90000 })
await p.screenshot({ path: OUT + 'e2e-1-review.png' })
let d = (await api(`/api/projects/${bookId}`)).body
ok('outline gate stops before illustrations', d.status === 'awaiting_review' && d.pages.every((x) => !x.image_url), `status=${d.status} pages=${d.pages.length}`)
await p.click('[data-testid=approve-outline]')
await p.waitForFunction(() => /completed/.test(document.querySelector('[data-testid=status]')?.textContent || ''), { timeout: 240000, polling: 1000 })
d = (await api(`/api/projects/${bookId}`)).body
ok('book completed after approval', d.status.startsWith('completed') && d.qa_report.summary.errors === 0, `status=${d.status} errors=${d.qa_report.summary.errors} warnings=${d.qa_report.summary.warnings}`)
ok('narration audio on every page', d.pages.every((x) => x.audio_url), d.pages.map((x) => x.audio_meta?.duration_s).join(','))
await p.reload({ waitUntil: 'networkidle0' })
await p.waitForSelector('[data-testid=page-audio]')
const audioOk = await p.evaluate(async () => {
  const a = document.querySelector('[data-testid=page-audio]')
  a.muted = true
  try { await a.play() } catch (e) { return 'play failed: ' + e }
  await new Promise((r) => setTimeout(r, 1200))
  const t = a.currentTime; a.pause(); return t > 0.3 ? 'ok ' + t.toFixed(2) + 's of ' + a.duration.toFixed(1) : 'not playing ' + t
})
ok('browser plays the narration MP3', audioOk.startsWith('ok'), audioOk)

// region repair with the mask painter
await p.click('[data-testid=open-repair]')
await p.waitForSelector('[data-testid=mask-canvas]')
const box = await (await p.$('[data-testid=mask-canvas]')).boundingBox()
await p.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2)
await p.mouse.down()
for (let i = 0; i <= 10; i++) await p.mouse.move(box.x + box.width * (0.2 + i * 0.015), box.y + box.height * (0.2 + i * 0.01))
await p.mouse.up()
await p.screenshot({ path: OUT + 'e2e-2-mask.png' })
await p.click('[data-testid=run-repair]')
await p.waitForFunction(() => !document.querySelector('[data-testid=mask-canvas]'), { timeout: 20000 })
await p.waitForFunction(async (id) => {
  const r = await fetch(`/api/projects/${id}`, { headers: { Authorization: 'Bearer ' + localStorage.getItem('bm_token') } })
  const j = await r.json(); return j.pages[0].inpaints === 1 && !['queued', 'running'].includes(j.status)
}, { timeout: 120000, polling: 1000 }, bookId)
const hist = (await api(`/api/projects/${bookId}/history`)).body
const rep = hist.find((h) => h.action === 'image_repaired')
ok('mask repair applied only inside the mask', rep && rep.detail.changed_outside_mask === 0, JSON.stringify(rep?.detail))
const revs = (await api(`/api/projects/${bookId}/pages/${d.pages[0].id}/revisions`)).body
ok('old illustration kept as a revision', revs.some((r) => r.reason === 'before_inpaint'), `revisions=${revs.length}`)

// exports
await p.evaluate((id) => { location.hash = `#/book/${id}/exports` }, bookId)
await p.waitForSelector('[data-testid=export-epub] button')
await p.waitForFunction(() => !document.querySelector('[data-testid=export-epub] button').disabled, { timeout: 30000 })
await p.click('[data-testid=export-epub] button')
await waitText('[data-testid=export-epub]', '✓ 5\\.', 180000)
await p.click('[data-testid=export-html] button')
await waitText('[data-testid=export-html]', 'KB|MB', 60000)
await p.screenshot({ path: OUT + 'e2e-3-exports.png' })
ok('EPUB built and passes EPUBCheck in the UI', true)
// open the HTML reader in the browser and check it renders RTL with audio
const tok = await p.evaluate(() => localStorage.getItem('bm_token'))
const r2 = await b.newPage()
const html = await p.evaluate(async (u) => (await fetch(u)).text(), `/api/projects/${bookId}/exports/html?token=${tok}`)
await r2.setContent(html, { waitUntil: 'load' })
const reader = await r2.evaluate(() => ({ dir: document.documentElement.dir, sheets: document.querySelectorAll('.sheet').length, audio: document.querySelectorAll('audio').length }))
{
  await r2.click('#next'); await r2.click('#next'); await r2.click('#next')
}
await new Promise((r) => setTimeout(r, 1200))
await r2.screenshot({ path: OUT + 'e2e-4-reader.png' })
ok('HTML reader: RTL, all pages, embedded audio', reader.dir === 'rtl' && reader.sheets >= 10 && reader.audio === 8, JSON.stringify(reader))

// characters: cast sheet + scale
await p.evaluate((id) => { location.hash = `#/book/${id}/characters` }, bookId)
await p.waitForSelector('[data-testid=cast-sheet]')
await p.screenshot({ path: OUT + 'e2e-5-cast.png' })
ok('cast line-up visible', true)

// preview: read-along plays the narration of the story pages in order
await p.evaluate((id) => { location.hash = `#/book/${id}/preview` }, bookId)
await p.waitForSelector('[data-testid=read-along]')
await p.click('[data-testid=read-along]')
await p.waitForFunction(() => { const a = document.querySelector('[data-testid=preview-audio]'); return a && !a.paused && a.currentTime > 0.3 }, { timeout: 20000 })
const ra1 = await p.$eval('[data-testid=preview-audio]', (a) => ({ src: a.src.split('?')[0].split('/').pop(), t: a.currentTime }))
await p.screenshot({ path: OUT + 'e2e-7-readalong.png' })
// jump to the end of the clip: the next story page must start automatically
await p.$eval('[data-testid=preview-audio]', (a) => { a.currentTime = Math.max(0, a.duration - 0.2) })
await p.waitForFunction((first) => { const a = document.querySelector('[data-testid=preview-audio]'); return a && !a.src.includes(first) && !a.paused }, { timeout: 20000 }, ra1.src)
const ra2 = await p.$eval('[data-testid=preview-audio]', (a) => a.src.split('?')[0].split('/').pop())
await p.click('[data-testid=read-stop]')
ok('preview read-along plays page after page', ra1.src.startsWith('page-01') && ra2.startsWith('page-02'), `${ra1.src} -> ${ra2}`)

// a second user cannot see the admin's book
await p.evaluate(() => { location.hash = '#/users' })
await p.waitForSelector('table')
const created = await api('/api/auth/users', { method: 'POST', body: JSON.stringify({ username: 'reader', password: 'Reader-pass1', role: 'user' }) })
await p.click('[data-testid=logout]')
await p.waitForSelector('[data-testid=login-user]')
await p.type('[data-testid=login-user]', 'reader'); await p.type('[data-testid=login-pass]', 'Reader-pass1'); await p.click('[data-testid=login-submit]')
await p.waitForSelector('[data-testid=logout]')
const q = p
const seen = await q.evaluate(async (id) => (await fetch(`/api/projects/${id}`, { headers: { Authorization: 'Bearer ' + localStorage.getItem('bm_token') } })).status, bookId)
const lib = await q.evaluate(async () => (await (await fetch('/api/projects', { headers: { Authorization: 'Bearer ' + localStorage.getItem('bm_token') } })).json()).length)
ok('second account is isolated', created.status === 201 && seen === 404 && lib === 0, `create=${created.status} get=${seen} books=${lib}`)
await q.screenshot({ path: OUT + 'e2e-6-user.png' })
ok('no browser console errors', errors.filter((e) => !/401|Failed to load resource/.test(e)).length === 0, errors.join(' | ').slice(0, 400))
console.log(JSON.stringify({ book: bookId, results }, null, 1))
await b.close()
