import { Navigate, Route, Routes } from 'react-router'

import { LoginPage } from './auth/LoginPage'
import { RequireAuth } from './auth/RequireAuth'
import { SourcePage } from './reader/SourcePage'
import { ChroniclePage } from './routes/ChroniclePage'
import { DataHealthPage } from './routes/DataHealthPage'
import { StubPage } from './routes/StubPage'
import { Workstation } from './shell/Workstation'

export function App() {
  return (
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
        <Route path="source/:sid" element={<SourcePage />} />
        <Route path="research" element={<StubPage title="Research" />} />
        <Route path="topics" element={<StubPage title="Topics" />} />
        <Route path="people" element={<StubPage title="People" />} />
        <Route path="files" element={<StubPage title="Files" />} />
        <Route path="data-health" element={<DataHealthPage />} />
        <Route path="settings" element={<StubPage title="Settings" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
