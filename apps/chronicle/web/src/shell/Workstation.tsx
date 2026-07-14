import { useEffect } from 'react'
import { Outlet } from 'react-router'

import { applyAppearanceFromStorage, readDensity } from '../settings/appearance'
import { CommandBar } from './CommandBar'
import { Inspector } from './Inspector'
import { PrimaryNav } from './PrimaryNav'
import { ScopeBar } from './ScopeBar'
import { StatusStrip } from './StatusStrip'

export function Workstation() {
  useEffect(() => {
    applyAppearanceFromStorage()
  }, [])

  const density = readDensity()

  return (
    <div
      className="grid h-full w-full bg-graphite-950 text-text-primary"
      style={{
        gridTemplateRows: '56px 44px 1fr 24px',
        gridTemplateColumns: '56px 1fr 360px',
      }}
      data-testid="workstation-shell"
      data-density={density}
    >
      <a href="#main" className="skip-link">
        Skip to main content
      </a>
      <CommandBar />
      <ScopeBar />
      <PrimaryNav />
      <main
        id="main"
        className="min-h-0 min-w-0 overflow-auto bg-graphite-950 p-3"
        tabIndex={-1}
      >
        <Outlet />
      </main>
      <Inspector />
      <StatusStrip />
    </div>
  )
}
