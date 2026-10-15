import { useCallback, useEffect, useMemo, useState } from 'react'
import { LangContext, makeT } from './i18n'
import { Toast } from './components/common'
import Dashboard from './pages/Dashboard'
import NewBook from './pages/NewBook'
import Library from './pages/Library'
import Book from './pages/Book'
import Providers from './pages/Providers'
import { Login, Users } from './pages/Auth'
import { api, setToken } from './api'

function useHashRoute() {
  const [hash, setHash] = useState(window.location.hash || '#/')
  useEffect(() => {
    const on = () => setHash(window.location.hash || '#/')
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return hash.replace(/^#/, '') || '/'
}

export const navigate = (path) => { window.location.hash = path }

export default function App() {
  const route = useHashRoute()
  const [lang, setLang] = useState(() => localStorage.getItem('bm_ui_lang') || (navigator.language?.startsWith('fa') ? 'fa' : 'en'))
  const [toast, setToast] = useState(null)
  const t = useMemo(() => makeT(lang), [lang])
  useEffect(() => {
    document.documentElement.lang = lang
    document.documentElement.dir = lang === 'fa' ? 'rtl' : 'ltr'
    localStorage.setItem('bm_ui_lang', lang)
  }, [lang])
  const notify = useCallback((text, type = 'info') => setToast({ text, type }), [])
  // auth: mode none -> no login; token / accounts -> login screen when the API answers 401
  const [auth, setAuth] = useState({ status: null, me: null, need: false })
  const loadAuth = useCallback(async () => {
    try {
      const status = await api('/api/auth/status')
      if (status.mode === 'none') { setAuth({ status, me: { role: 'admin', username: 'local' }, need: false }); return }
      try { const me = await api('/api/auth/me'); setAuth({ status, me, need: false }) }
      catch { setAuth({ status, me: null, need: true }) }
    } catch { setAuth({ status: { mode: 'none' }, me: { role: 'admin' }, need: false }) }
  }, [])
  useEffect(() => { loadAuth() }, [loadAuth])
  useEffect(() => {
    const on = () => setAuth((a) => (a.status && a.status.mode !== 'none' ? { ...a, need: true } : a))
    window.addEventListener('bm-auth-required', on)
    return () => window.removeEventListener('bm-auth-required', on)
  }, [])
  const logout = async () => { try { await api('/api/auth/logout', { method: 'POST' }) } catch { /* already gone */ } setToken(''); loadAuth() }
  const ctx = useMemo(() => ({ lang, t, notify, me: auth.me }), [lang, t, notify, auth.me])

  let page
  const isAdmin = auth.me?.role === 'admin'
  const m = route.match(/^\/book\/([a-f0-9]+)(?:\/(\w+))?/)
  if (route.startsWith('/new')) page = <NewBook />
  else if (route.startsWith('/library')) page = <Library />
  else if (route.startsWith('/providers')) page = <Providers admin={isAdmin} />
  else if (route.startsWith('/users')) page = <Users me={auth.me} />
  else if (m) page = <Book id={m[1]} tab={m[2] || 'pages'} />
  else page = <Dashboard />
  if (auth.need || (auth.status?.needs_setup)) page = <Login status={auth.status} onDone={loadAuth} />
  else if (!auth.status) page = <p className="muted page">…</p>

  const link = (to, label) => <a href={`#${to}`} className={(route === to || (to !== '/' && route.startsWith(to))) ? 'active' : ''}>{label}</a>
  return (
    <LangContext.Provider value={ctx}>
      <header className="topbar">
        <a href="#/" className="brand"><span className="logo">📖</span><span><b>{t('app')}</b><small>{t('tagline')}</small></span></a>
        <nav>
          {link('/', t('dashboard'))}
          {link('/new', t('newBook'))}
          {link('/library', t('library'))}
          {link('/providers', t('providers'))}
          {auth.status?.mode === 'accounts' && auth.me && link('/users', t('users'))}
        </nav>
        {auth.status?.mode !== 'none' && auth.me && <button className="ghost" onClick={logout} data-testid="logout">{auth.me.username} · {t('logout')}</button>}
        <button className="ghost lang" onClick={() => setLang(lang === 'fa' ? 'en' : 'fa')} aria-label="language">{lang === 'fa' ? 'English' : 'فارسی'}</button>
      </header>
      <main>{page}</main>
      <Toast msg={toast} onClose={() => setToast(null)} />
    </LangContext.Provider>
  )
}
