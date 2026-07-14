import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { WorkspacePage } from './WorkspacePage'

const baseWorkspace = {
  id: 'ws-1',
  name: 'Notebook',
  description: 'desc',
  scope: { senders: ['alice@example.com'] },
  version: 1,
  blocks: [
    {
      id: 'b-h',
      workspace_id: 'ws-1',
      position: 0,
      block_type: 'heading' as const,
      content: { text: 'Findings' },
    },
    {
      id: 'b-n',
      workspace_id: 'ws-1',
      position: 1,
      block_type: 'note' as const,
      content: { text: 'Plain <script>alert(1)</script> note' },
    },
    {
      id: 'b-p',
      workspace_id: 'ws-1',
      position: 2,
      block_type: 'pin' as const,
      content: {
        source_id: 'msg_1',
        source_type: 'message',
        title: 'Pinned mail',
        date: '2015-01-01',
        sender: 'Alice',
        excerpt: 'snippet',
      },
    },
    {
      id: 'b-a',
      workspace_id: 'ws-1',
      position: 3,
      block_type: 'answer' as const,
      content: { answer_id: 'ans-1' },
      answer: {
        answer_id: 'ans-1',
        answer_text: 'Metal roof [S1].',
        citations: [
          {
            marker: 'S1',
            source_id: 'msg_1',
            source_type: 'message',
            excerpt: 'metal',
          },
        ],
      },
    },
  ],
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/workspaces/ws-1']}>
        <Routes>
          <Route path="/workspaces/:id" element={<WorkspacePage />} />
          <Route path="/" element={<div data-testid="chronicle-home">home</div>} />
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

describe('WorkspacePage notebook', () => {
  let workspace = structuredClone(baseWorkspace)

  beforeEach(() => {
    resetWorkingSetStore()
    workspace = structuredClone(baseWorkspace)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('renders all four block types; notes are text only (no HTML injection)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/workspaces/ws-1') && !String(url).includes('/blocks')) {
          return jsonResponse(workspace)
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    renderPage()
    await screen.findByTestId('workspace-page')

    expect(screen.getByTestId('heading-view-b-h')).toHaveTextContent('Findings')
    const note = screen.getByTestId('note-view-b-n')
    expect(note).toHaveTextContent('Plain <script>alert(1)</script> note')
    // Must not inject HTML: no script node inside note view
    expect(note.querySelector('script')).toBeNull()
    expect(note.innerHTML).not.toMatch(/<script>/i)

    expect(screen.getByTestId('pin-block-b-p')).toHaveTextContent('Pinned mail')
    expect(screen.getByTestId('pin-link-b-p')).toHaveAttribute(
      'href',
      '/source/msg_1',
    )
    expect(screen.getByTestId('answer-text-b-a')).toHaveTextContent(/Metal roof/)
    expect(screen.getByTestId('answer-citations-b-a')).toHaveTextContent('msg_1')
  })

  it('reorders blocks with Up/Down', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
        const u = String(url)
        const method = (init?.method || 'GET').toUpperCase()
        if (u === '/api/workspaces/ws-1' && method === 'GET') {
          return jsonResponse(workspace)
        }
        if (u.includes('/blocks/b-n') && method === 'PATCH') {
          const body = JSON.parse(String(init?.body || '{}')) as { position?: number }
          // swap note (pos 1) up with heading (pos 0)
          const note = workspace.blocks.find((b) => b.id === 'b-n')!
          const heading = workspace.blocks.find((b) => b.id === 'b-h')!
          if (body.position === 0) {
            note.position = 0
            heading.position = 1
          }
          return jsonResponse({ ...note })
        }
        throw new Error(`unexpected: ${method} ${u}`)
      }),
    )

    renderPage()
    await screen.findByTestId('notebook-blocks')
    fireEvent.click(screen.getByTestId('block-up-b-n'))

    await waitFor(() => {
      const notebook = screen.getByTestId('notebook-blocks')
      const articles = within(notebook).getAllByRole('article')
      expect(articles[0]).toHaveAttribute('data-testid', 'block-b-n')
    })
  })

  it('exports trigger blob download', async () => {
    const createObjectURL = vi.fn(() => 'blob:mock')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        const u = String(url)
        if (u === '/api/workspaces/ws-1') {
          return jsonResponse(workspace)
        }
        if (u.includes('/export?format=markdown')) {
          return {
            ok: true,
            status: 200,
            headers: new Headers({
              'Content-Disposition': 'attachment; filename="Notebook.md"',
              'X-Manifest-Fingerprint': 'abc',
            }),
            blob: async () => new Blob(['# Notebook'], { type: 'text/markdown' }),
          } as Response
        }
        throw new Error(`unexpected: ${u}`)
      }),
    )

    renderPage()
    await screen.findByTestId('workspace-page')
    // open details menu
    const summary = screen.getByText('Export')
    fireEvent.click(summary)
    fireEvent.click(screen.getByTestId('export-markdown'))

    await waitFor(() => {
      expect(createObjectURL).toHaveBeenCalled()
    })
  })

  it('shows 409 version-conflict banner on name edit', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
        const u = String(url)
        const method = (init?.method || 'GET').toUpperCase()
        if (u === '/api/workspaces/ws-1' && method === 'GET') {
          return jsonResponse(workspace)
        }
        if (u === '/api/workspaces/ws-1' && method === 'PATCH') {
          return jsonResponse({ detail: 'Version conflict' }, 409)
        }
        throw new Error(`unexpected: ${method} ${u}`)
      }),
    )

    renderPage()
    await screen.findByTestId('workspace-name')
    fireEvent.click(screen.getByTestId('workspace-name'))
    const input = screen.getByTestId('workspace-name-edit')
    fireEvent.change(input, { target: { value: 'Renamed' } })
    fireEvent.blur(input)

    expect(await screen.findByTestId('version-conflict-banner')).toBeInTheDocument()
  })

  it('opens workspace scope in Chronicle', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url) === '/api/workspaces/ws-1') {
          return jsonResponse(workspace)
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    renderPage()
    await screen.findByTestId('open-workspace-scope')
    fireEvent.click(screen.getByTestId('open-workspace-scope'))

    await waitFor(() => {
      expect(screen.getByTestId('chronicle-home')).toBeInTheDocument()
    })
    expect(useWorkingSetStore.getState().scope).toEqual({
      senders: ['alice@example.com'],
    })
  })
})
