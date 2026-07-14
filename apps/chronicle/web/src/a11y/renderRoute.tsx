/**
 * Shared harness for a11y route sweeps: full shell + lightweight page stubs.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'

import { RequireAuth } from '../auth/RequireAuth'
import { ShortcutProvider } from '../keyboard'
import { SettingsPage } from '../settings'
import { Workstation } from '../shell/Workstation'
import {
  createTestQueryClient,
  mockArchiveSummary,
  mockSessionOk,
} from '../test/test-utils'

function Stub({ title }: { title: string }) {
  return (
    <div>
      <h1>{title}</h1>
      <p>Page content for {title}</p>
      <button type="button">Sample action</button>
    </div>
  )
}

export const A11Y_ROUTES = [
  '/',
  '/research',
  '/topics',
  '/people',
  '/files',
  '/workspaces',
  '/data-health',
  '/settings',
] as const

export type A11yRoute = (typeof A11Y_ROUTES)[number]

export function stubA11yFetch() {
  const settings = {
    ai: {
      ask_enabled: true,
      interpret_enabled: true,
      generate_enabled: true,
      answer_model: 'llama3.2',
      retention_note: 'Local only',
    },
    privacy: { session_max_age_s: 43200 },
    search: { default_mode: 'hybrid' },
    chronicle: { default_lanes: ['messages', 'attachments', 'top_people'] },
  }
  return async (url: string) => {
    const path = String(url)
    if (path.includes('/api/auth/session')) return mockSessionOk()
    if (path.includes('/api/archive/summary')) return mockArchiveSummary()
    if (path.includes('/api/settings')) {
      return {
        ok: true,
        status: 200,
        json: async () => settings,
      } as Response
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({}),
    } as Response
  }
}

export function renderA11yRoute(route: string) {
  const client = createTestQueryClient()
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[route]}>
          <ShortcutProvider>
            <Routes>
              <Route
                element={
                  <RequireAuth>
                    <Workstation />
                  </RequireAuth>
                }
              >
                <Route index element={<Stub title="Chronicle" />} />
                <Route path="chronicle" element={<Stub title="Chronicle" />} />
                <Route path="research" element={<Stub title="Research" />} />
                <Route path="topics" element={<Stub title="Topics" />} />
                <Route path="people" element={<Stub title="People" />} />
                <Route path="files" element={<Stub title="Files" />} />
                <Route path="workspaces" element={<Stub title="Workspaces" />} />
                <Route path="data-health" element={<Stub title="Data Health" />} />
                <Route path="settings" element={<SettingsPage />} />
              </Route>
            </Routes>
          </ShortcutProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}
