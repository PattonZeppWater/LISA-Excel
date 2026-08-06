import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Sidebar              from './components/Sidebar'
import Home                 from './pages/Home'
import ToolsDocForge        from './pages/ToolsDocForge'
import ToolsSubmittalLog    from './pages/ToolsSubmittalLog'
import IodbGeneration       from './pages/IodbGeneration'
import IdpGeneration        from './pages/IdpGeneration'
import PersistentAutofill   from './pages/IdpAutofill'
import SharedTimesheets     from './pages/SharedTimesheets'

export default function App() {
  const [theme, setTheme] = useState(() => {
    try {
      const t = localStorage.getItem('lisa_theme')
      return t === 'light' ? 'light' : 'dark'
    } catch {
      return 'dark'
    }
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('lisa_theme', theme) } catch { /* storage unavailable */ }
  }, [theme])

  const toggleTheme = () => setTheme(t => (t === 'dark' ? 'light' : 'dark'))

  return (
    <BrowserRouter>
      <div className="app">
        <Sidebar theme={theme} onToggleTheme={toggleTheme} />
        <main className="main-content">
          <Routes>
            <Route path="/"                        element={<Home />} />
            <Route path="/tools/docforge"           element={<ToolsDocForge />} />
            <Route path="/tools/submittal-log"      element={<ToolsSubmittalLog />} />
            <Route path="/iodb/generation"          element={<IodbGeneration />} />
            <Route path="/idp/generation"           element={<IdpGeneration />} />
            <Route path="/idp/autofill"             element={<></>} />
            <Route path="/shared/timesheets"        element={<SharedTimesheets />} />
          </Routes>
          {/* Mounted once, kept alive across tab switches; shows only on /idp/autofill. */}
          <PersistentAutofill />
        </main>
      </div>
    </BrowserRouter>
  )
}
