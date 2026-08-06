import { NavLink } from 'react-router-dom'

const tree = [
  {
    category: 'Tools',
    services: [
      { label: 'DocForge',        path: '/tools/docforge',      ready: true },
      { label: 'Submittal Log',   path: '/tools/submittal-log', ready: true },
    ],
  },
  {
    category: 'IDP',
    services: [
      { label: 'Generation', path: '/idp/generation', ready: true },
      { label: 'Autofill',   path: '/idp/autofill',   ready: true },
    ],
  },
  {
    category: 'IODB',
    services: [
      { label: 'Generation', path: '/iodb/generation', ready: true },
    ],
  },
  {
    category: 'Shared',
    services: [
      { label: 'Timesheets', path: '/shared/timesheets', ready: true },
    ],
  },
  {
    category: 'SAC',
    services: [
      { label: 'Generation', path: '/sac/generation', ready: false },
    ],
  },
]

export default function Sidebar({ theme = 'dark', onToggleTheme }) {
  const isDark = theme === 'dark'
  return (
    <aside className="sidebar">
      <NavLink to="/" end className="sidebar-brand">LISA</NavLink>

      <ul className="tree-root">
        {tree.map(branch => (
          <li key={branch.category} className="tree-category">
            <span className="tree-category-label">{branch.category}</span>
            <ul className="tree-children">
              {branch.services.map((svc, i) => {
                const isLast = i === branch.services.length - 1
                return (
                  <li key={svc.path ?? svc.label} className={`tree-item${isLast ? ' last' : ''}`}>
                    {svc.ready ? (
                      <NavLink
                        to={svc.path}
                        className={({ isActive }) => 'tree-btn' + (isActive ? ' active' : '')}
                      >
                        {svc.label}
                      </NavLink>
                    ) : (
                      <span className="tree-btn dev">{svc.label}</span>
                    )}
                  </li>
                )
              })}
            </ul>
          </li>
        ))}
      </ul>

      <button
        type="button"
        className="theme-toggle"
        onClick={onToggleTheme}
        title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
        aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      >
        <span className="theme-toggle-icon">{isDark ? '☀' : '☾'}</span>
        {isDark ? 'Light mode' : 'Dark mode'}
      </button>
    </aside>
  )
}
