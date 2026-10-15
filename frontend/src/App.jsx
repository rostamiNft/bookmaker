import { useCallback, useEffect, useMemo, useState } from 'react'
import { LangContext, makeT } from './i18n'
import { Toast } from './components/common'
import Dashboard from './pages/Dashboard'
import NewBook from './pages/NewBook'
import Library from './pages/Library'
import Book from './pages/Book'
import Providers from './pages/Providers'

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
  const ctx = useMemo(() => ({ lang, t, notify }), [lang, t, notify])

  let page
  const m = route.match(/^\/book\/([a-f0-9]+)(?:\/(\w+))?/)
  if (route.startsWith('/new')) page = <NewBook />
  else if (route.startsWith('/library')) page = <Library />
  else if (route.startsWith('/providers')) page = <Providers />
  else if (m) page = <Book id={m[1]} tab={m[2] || 'pages'} />
  else page = <Dashboard />

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
        </nav>
        <button className="ghost lang" onClick={() => setLang(lang === 'fa' ? 'en' : 'fa')} aria-label="language">{lang === 'fa' ? 'English' : 'فارسی'}</button>
      </header>
      <main>{page}</main>
      <Toast msg={toast} onClose={() => setToast(null)} />
    </LangContext.Provider>
  )
}
