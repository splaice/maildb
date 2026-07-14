import { Outlet } from 'react-router'

import { CommandBar } from './CommandBar'
import { Inspector } from './Inspector'
import { PrimaryNav } from './PrimaryNav'
import { ScopeBar } from './ScopeBar'
import { StatusStrip } from './StatusStrip'

export function Workstation() {
  return (
    <div
      className="grid h-full w-full bg-graphite-950 text-text-primary"
      style={{
        gridTemplateRows: '56px 44px 1fr 24px',
        gridTemplateColumns: '56px 1fr 360px',
      }}
      data-testid="workstation-shell"
    >
      <CommandBar />
      <ScopeBar />
      <PrimaryNav />
      <main className="min-h-0 min-w-0 overflow-auto bg-graphite-950 p-3">
        <Outlet />
      </main>
      <Inspector />
      <StatusStrip />
    </div>
  )
}
