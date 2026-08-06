import { useLocation } from 'react-router-dom'

// Persistent Autofill panel. The iframe (the merged IDP-Extractor UI, served by the Flask
// blueprint at /autofill/) mounts ONCE and stays alive across route changes — hidden with CSS
// when you're on another tab, shown when you're on IDP → Autofill.
//
// Why: if this iframe were rendered by a normal <Route>, React would unmount it every time you
// left the tab and re-create it on return, forcing a full panel reload + its startup calls
// (get_settings, etc.) on every single switch. That blank-then-reload is what felt slow. Kept
// mounted, the first switch loads it once and every later switch is instant.
export default function PersistentAutofill() {
  const { pathname } = useLocation()
  const active = pathname === '/idp/autofill'
  return (
    <div style={{ display: active ? 'block' : 'none', height: '100vh' }}>
      <iframe
        title="Autofill"
        src="/autofill/"
        style={{
          width: '100%',
          height: '100vh',
          border: 'none',
          display: 'block',
          background: 'var(--bg, #0a1220)',
        }}
      />
    </div>
  )
}
