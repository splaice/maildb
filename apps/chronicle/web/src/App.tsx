import { Navigate, Route, Routes } from 'react-router'

import { LoginPage } from './auth/LoginPage'
import { RequireAuth } from './auth/RequireAuth'
import { ReconstructionView } from './events/ReconstructionView'
import { FilesPage } from './files/FilesPage'
import { SourcePage } from './reader/SourcePage'
import { ResearchDeskPage } from './research/ResearchDeskPage'
import { ResearchNavShortcut } from './research/ResearchNavShortcut'
import { ChroniclePage } from './routes/ChroniclePage'
import { DataHealthPage } from './routes/DataHealthPage'
import { StubPage } from './routes/StubPage'
import { Workstation } from './shell/Workstation'
import { TopicsPage } from './topics/TopicsPage'
import { WorkspacePage } from './workspaces/WorkspacePage'
import { WorkspacesListPage } from './workspaces/WorkspacesListPage'

export function App() {
  return (
    <>
      <ResearchNavShortcut />
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
          <Route path="people" element={<StubPage title="People" />} />
          <Route path="files" element={<FilesPage />} />
          <Route path="workspaces" element={<WorkspacesListPage />} />
          <Route path="workspaces/:id" element={<WorkspacePage />} />
          <Route path="data-health" element={<DataHealthPage />} />
          <Route path="settings" element={<StubPage title="Settings" />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  )
}

export default App
