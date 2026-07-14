import { NavLink, useLocation } from 'react-router'

const NAV_ITEMS = [
  { to: '/research', label: 'Research' },
  { to: '/topics', label: 'Topics' },
  { to: '/people', label: 'People' },
  { to: '/files', label: 'Files' },
  { to: '/workspaces', label: 'Workspaces' },
  { to: '/data-health', label: 'Data Health' },
  { to: '/settings', label: 'Settings' },
] as const

function navClass(isActive: boolean): string {
  return [
    'flex flex-col items-center justify-center gap-0.5 px-1 py-2 text-center text-[10px] leading-tight',
    'border-l-2',
    isActive
      ? 'border-action bg-graphite-800 text-action'
      : 'border-transparent text-text-muted hover:bg-graphite-800 hover:text-text-primary',
  ].join(' ')
}

export function PrimaryNav() {
  const location = useLocation()
  const chronicleActive =
    location.pathname === '/' || location.pathname === '/chronicle'

  return (
    <nav
      className="flex flex-col border-r border-steel bg-graphite-900 py-1"
      style={{ width: 56 }}
      aria-label="Primary"
    >
      <NavLink
        to="/"
        end
        className={() => navClass(chronicleActive)}
        aria-current={chronicleActive ? 'page' : undefined}
      >
        Chronicle
      </NavLink>
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) => navClass(isActive)}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}
