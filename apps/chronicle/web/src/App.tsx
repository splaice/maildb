import { useMemo, type ReactNode } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router'

import { LoginPage } from './auth/LoginPage'
import { RequireAuth } from './auth/RequireAuth'
import { ReconstructionView } from './events/ReconstructionView'
import { FilesPage } from './files/FilesPage'
import {
  setStatusHint,
  ShortcutProvider,
  useRegisterNavigationShortcuts,
  useShortcuts,
} from './keyboard'
import {
  CommandPalette,
  CommandRegistryProvider,
  ContextCommands,
} from './palette'
import { SourcePage } from './reader/SourcePage'
import { ResearchDeskPage } from './research/ResearchDeskPage'
import { PeoplePage } from './people/PeoplePage'
import { PersonProfilePage } from './people/PersonProfilePage'
import { ChroniclePage } from './routes/ChroniclePage'
import { DataHealthPage } from './routes/DataHealthPage'
import { SettingsPage } from './settings'
import { Workstation } from './shell/Workstation'
import { TopicsPage } from './topics/TopicsPage'
import { WorkspacePage } from './workspaces/WorkspacePage'
import { WorkspacesListPage } from './workspaces/WorkspacesListPage'

/** Binds T/R/M navigation custom events to react-router + G person-graph. */
function NavigationBindings() {
  const navigate = useNavigate()
  useRegisterNavigationShortcuts((to) => {
    navigate(to)
  })
  return null
}

function GlobalPersonGraphShortcut() {
  // G: open person graph when a person profile is in context; else status hint.
  const navigate = useNavigate()
  const bindings = useMemo(
    () => [
      {
        id: 'global.person-graph',
        chord: { key: 'g' as const },
        description: 'Open person graph when a person is selected',
        group: 'Navigation',
        run: () => {
          const path = window.location.pathname
          const m = /^\/people\/([^/]+)/.exec(path)
          if (m) {
            navigate(`/people/${encodeURIComponent(m[1]!)}`)
            return true
          }
          setStatusHint('Select a person first to open the graph')
          return true
        },
      },
    ],
    [navigate],
  )
  useShortcuts(bindings)
  return null
}

function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ShortcutProvider>
      <CommandRegistryProvider>
        <NavigationBindings />
        <GlobalPersonGraphShortcut />
        <ContextCommands />
        <CommandPalette />
        {children}
      </CommandRegistryProvider>
    </ShortcutProvider>
  )
}

export function App() {
  return (
    <AppProviders>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <Workstation />
            </RequireAuth>
          }
        >
          <Route index element={<ChroniclePage />} />
          <Route path="chronicle" element={<ChroniclePage />} />
          <Route
            path="events/:id/reconstruction"
            element={<ReconstructionView />}
          />
          <Route path="source/:sid" element={<SourcePage />} />
          <Route path="research" element={<ResearchDeskPage />} />
          <Route path="topics" element={<TopicsPage />} />
          <Route path="people" element={<PeoplePage />} />
          <Route path="people/:id" element={<PersonProfilePage />} />
          <Route path="files" element={<FilesPage />} />
          <Route path="workspaces" element={<WorkspacesListPage />} />
          <Route path="workspaces/:id" element={<WorkspacePage />} />
          <Route path="data-health" element={<DataHealthPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppProviders>
  )
}

export default App
