import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { WorkspacesListPage } from './WorkspacesListPage'
import { WorkspacePage } from './WorkspacePage'

function renderList(initial = ['/workspaces']) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initial}>
        <Routes>
          <Route path="/workspaces" element={<WorkspacesListPage />} />
          <Route path="/workspaces/:id" element={<WorkspacePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: new Headers({ 'Content-Type': 'application/json' }),
  } as Response
}

describe('WorkspacesListPage', () => {
  beforeEach(() => {
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('lists workspaces and supports create + delete with confirm', async () => {
    const items = [
      {
        id: 'ws-1',
        name: 'Case Alpha',
        updated_at: '2026-07-01T12:00:00Z',
        counts: { blocks: 3, pins: 1, notes: 1, answers: 1, headings: 0 },
      },
    ]

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
        const u = String(url)
        const method = (init?.method || 'GET').toUpperCase()
        if (u === '/api/workspaces' && method === 'GET') {
          return jsonResponse({ items })
        }
        if (u === '/api/workspaces' && method === 'POST') {
          const body = JSON.parse(String(init?.body || '{}')) as {
            name: string
            scope?: unknown
          }
          return jsonResponse(
            {
              id: 'ws-new',
              name: body.name,
              description: null,
              scope: body.scope || {},
              version: 1,
              blocks: [],
            },
            201,
          )
        }
        if (u === '/api/workspaces/ws-1' && method === 'DELETE') {
          items.splice(0, 1)
          return { ok: true, status: 204, json: async () => undefined } as Response
        }
        if (u.startsWith('/api/workspaces/ws-new')) {
          return jsonResponse({
            id: 'ws-new',
            name: 'Fresh',
            description: null,
            scope: {},
            version: 1,
            blocks: [],
          })
        }
        throw new Error(`unexpected fetch: ${method} ${u}`)
      }),
    )

    renderList()

    expect(await screen.findByTestId('workspace-row-ws-1')).toHaveTextContent(
      'Case Alpha',
    )
    expect(screen.getByTestId('workspace-row-ws-1')).toHaveTextContent(/3 blocks/)

    // delete with confirm
    fireEvent.click(screen.getByTestId('delete-workspace-ws-1'))
    expect(screen.getByTestId('delete-confirm')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('delete-confirm-yes'))
    await waitFor(() => {
      expect(screen.queryByTestId('workspace-row-ws-1')).not.toBeInTheDocument()
    })

    // create with current scope
    useWorkingSetStore.getState().setScope({ senders: ['a@example.com'] })
    fireEvent.change(screen.getByTestId('new-workspace-name'), {
      target: { value: 'Fresh' },
    })
    expect(screen.getByTestId('use-current-scope')).toBeChecked()
    fireEvent.click(screen.getByTestId('create-workspace'))

    await waitFor(() => {
      expect(screen.getByTestId('workspace-page')).toBeInTheDocument()
    })
    expect(screen.getByTestId('workspace-name')).toHaveTextContent('Fresh')

    const postCall = vi
      .mocked(fetch)
      .mock.calls.find(
        (c) => String(c[0]) === '/api/workspaces' && (c[1] as RequestInit)?.method === 'POST',
      )
    expect(postCall).toBeTruthy()
    const posted = JSON.parse(String((postCall![1] as RequestInit).body))
    expect(posted.scope).toEqual({ senders: ['a@example.com'] })
  })
})
