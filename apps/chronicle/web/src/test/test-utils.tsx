import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'

import { LoginPage } from '../auth/LoginPage'
import { RequireAuth } from '../auth/RequireAuth'
import { ChroniclePage } from '../routes/ChroniclePage'
import { StubPage } from '../routes/StubPage'
import { Workstation } from '../shell/Workstation'

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

export function mockSessionOk(username = 'analyst') {
  return {
    ok: true,
    status: 200,
    json: async () => ({ username }),
  } as Response
}

export function mockUnauthorized() {
  return {
    ok: false,
    status: 401,
    json: async () => ({ detail: 'Not authenticated' }),
  } as Response
}

export function mockArchiveSummary(overrides: Record<string, unknown> = {}) {
  const body = {
    accounts: [{ account: 'me@example.com', messages: 100 }],
    date_range: { from: '2010-01-01T00:00:00', to: '2024-12-31T00:00:00' },
    counts: {
      messages: 1280000,
      threads: 400000,
      attachments: 50000,
      contacts: 12000,
    },
    extraction: {
      extracted: 40000,
      failed: 100,
      skipped: 200,
      pending: 50,
    },
    embedding: { embedded: 1200000, missing: 80000 },
    versions: { schema: 'maildb', api: '0.1.0' },
    ...overrides,
  }
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response
}

function Providers({
  children,
  client,
  initialEntries = ['/'],
}: {
  children: ReactNode
  client: QueryClient
  initialEntries?: string[]
}) {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

export function renderWithProviders(
  ui: ReactElement,
  options?: {
    client?: QueryClient
    initialEntries?: string[]
    renderOptions?: Omit<RenderOptions, 'wrapper'>
  },
) {
  const client = options?.client ?? createTestQueryClient()
  return {
    client,
    ...render(ui, {
      ...options?.renderOptions,
      wrapper: ({ children }) => (
        <Providers client={client} initialEntries={options?.initialEntries}>
          {children}
        </Providers>
      ),
    }),
  }
}

/** Full app routes (login + shell) for integration-style tests. */
export function renderApp(initialEntries: string[] = ['/']) {
  const client = createTestQueryClient()
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={initialEntries}>
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
              <Route path="research" element={<StubPage title="Research" />} />
              <Route path="topics" element={<StubPage title="Topics" />} />
              <Route path="people" element={<StubPage title="People" />} />
              <Route path="files" element={<StubPage title="Files" />} />
              <Route
                path="data-health"
                element={<StubPage title="Data Health" />}
              />
              <Route path="settings" element={<StubPage title="Settings" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}
