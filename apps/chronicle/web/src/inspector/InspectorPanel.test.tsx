import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { InspectorPanel } from './InspectorPanel'

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <InspectorPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const msgSource = {
  kind: 'msg' as const,
  envelope: {
    id: 'msg_1',
    thread_id: 'thr_YQ',
    subject: 'Hello world',
    sender_name: 'Alice',
    sender_address: 'alice@example.com',
    recipients: { to: ['bob@example.com'], cc: [], bcc: [] },
    date: '2014-06-01T12:00:00Z',
    mailbox: 'test@example.com',
    labels: ['INBOX'],
    has_attachment: false,
    attachments: [],
  },
  body: {
    text: 'Line one\n> quoted\nLine two',
    html: null,
    remote_resources_blocked: 2,
    had_active_content: false,
  },
}

describe('InspectorPanel flow', () => {
  beforeEach(() => {
    resetWorkingSetStore()
    useWorkingSetStore.getState().setTimelineUnit('month')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('shows empty state with no selection', () => {
    renderPanel()
    expect(screen.getByTestId('inspector-empty')).toBeInTheDocument()
  })

  it('event selection shows EventCard with origin text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          id: 'evt-99',
          title: 'Burst',
          time_start: '2015-06-01T00:00:00Z',
          time_end: null,
          time_precision: 'day',
          origin: 'automatic',
          event_type: 'communication',
          status: 'unreviewed',
          evidence_strength: null,
          current_version: 1,
          summary: null,
          claims: [],
          version: {
            version: 1,
            author: 'automatic',
            title: 'Burst',
            summary: null,
            derivation: {},
          },
        }),
      }),
    )
    useWorkingSetStore.getState().setSelection({ kind: 'event', eventId: 'evt-99' })
    renderPanel()
    expect(await screen.findByTestId('event-card')).toBeInTheDocument()
    expect(screen.getByTestId('event-origin-badge')).toHaveTextContent(/Automatic/)
    expect(screen.getByTestId('pin-to-workspace-btn')).toBeInTheDocument()
  })

  it('Enter on selected event opens reconstruction; P opens pin menu', async () => {
    const eventBody = {
      id: 'evt-99',
      title: 'Burst',
      time_start: '2015-06-01T00:00:00Z',
      time_end: null,
      time_precision: 'day',
      origin: 'automatic',
      event_type: 'decision',
      status: 'unreviewed',
      evidence_strength: 'high',
      current_version: 1,
      summary: null,
      claims: [],
      version: {
        version: 1,
        author: 'automatic',
        title: 'Burst',
        summary: null,
        derivation: {
          generated_at: '2026-07-13T12:00:00Z',
          process_version: 'event-v1',
          model_route: 'local-llama',
        },
      },
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
        const u = String(url)
        const method = (init?.method || 'GET').toUpperCase()
        if (u.includes('/api/events/evt-99')) {
          return { ok: true, status: 200, json: async () => eventBody } as Response
        }
        if (
          u.includes('/api/workspaces') &&
          method === 'GET' &&
          !u.includes('/blocks')
        ) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              items: [
                {
                  id: 'ws-1',
                  name: 'Case',
                  updated_at: null,
                  counts: {
                    blocks: 0,
                    pins: 0,
                    notes: 0,
                    answers: 0,
                    headings: 0,
                  },
                },
              ],
            }),
          } as Response
        }
        throw new Error(`unexpected: ${method} ${u}`)
      }),
    )

    useWorkingSetStore.getState().setSelection({ kind: 'event', eventId: 'evt-99' })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route path="/" element={<InspectorPanel />} />
            <Route
              path="/events/:id/reconstruction"
              element={<div data-testid="recon-stub">recon</div>}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByTestId('event-card')).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'p' })
    expect(await screen.findByTestId('pin-workspace-menu')).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'Enter' })
    await waitFor(() => {
      expect(screen.getByTestId('recon-stub')).toBeInTheDocument()
    })
  })

  it('bucket → list → message with mocked API', async () => {
    const listPage = {
      items: [
        {
          id: 'msg_1',
          subject: 'Hello world',
          sender_name: 'Alice',
          sender_address: 'alice@example.com',
          date: '2014-06-01T12:00:00Z',
          mailbox: 'test@example.com',
          has_attachment: false,
          attachment_count: 0,
          thread_id: 'thr_YQ',
        },
      ],
      next_cursor: null,
      scope_fingerprint: 'qs_test',
    }

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        const u = String(url)
        if (u.includes('/api/sources/list')) {
          return { ok: true, status: 200, json: async () => listPage } as Response
        }
        if (u.includes('/api/sources/msg_1')) {
          return { ok: true, status: 200, json: async () => msgSource } as Response
        }
        throw new Error(`unexpected fetch: ${u}`)
      }),
    )

    useWorkingSetStore.getState().setSelection({
      kind: 'bucket',
      lane: 'messages',
      bucketIso: '2014-06-01T00:00:00.000Z',
    })

    renderPanel()

    expect(await screen.findByTestId('inspector-bucket')).toBeInTheDocument()
    expect(screen.getByText(/messages ·/i)).toBeInTheDocument()
    expect(await screen.findByTestId('source-list')).toBeInTheDocument()
    expect(screen.getByText('Hello world')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('source-row-msg_1'))

    expect(await screen.findByTestId('message-card')).toBeInTheDocument()
    expect(screen.getByText('Hello world')).toBeInTheDocument()
    expect(screen.getByTestId('remote-blocked')).toHaveTextContent(
      '2 remote resources blocked',
    )
    expect(screen.getByTestId('open-full-source')).toHaveAttribute(
      'href',
      '/source/msg_1',
    )
    // Quoted plain-text collapse
    expect(screen.getByText(/Show quoted text/i)).toBeInTheDocument()
  })

  it('message close restores bucket', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/sources/msg_1')) {
          return { ok: true, status: 200, json: async () => msgSource } as Response
        }
        if (String(url).includes('/api/sources/list')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              items: [],
              next_cursor: null,
              scope_fingerprint: 'qs',
            }),
          } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    useWorkingSetStore.getState().setSelection({
      kind: 'bucket',
      lane: 'messages',
      bucketIso: '2014-06-01T00:00:00.000Z',
    })
    useWorkingSetStore.getState().setSelection({ kind: 'message', sid: 'msg_1' })

    renderPanel()
    expect(await screen.findByTestId('message-card')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('message-close'))
    await waitFor(() => {
      expect(useWorkingSetStore.getState().selection?.kind).toBe('bucket')
    })
  })

  it('attachment selection shows attachment metadata card', async () => {
    const attSource = {
      kind: 'att' as const,
      id: 'att_42',
      filename: 'invoice.pdf',
      content_type: 'application/pdf',
      size: 2048,
      source_message_id: 'msg_1',
      source_envelope: {
        id: 'msg_1',
        thread_id: null,
        subject: 'Invoice',
        sender_name: 'Bob',
        sender_address: 'bob@example.com',
        recipients: {},
        date: '2015-01-01T00:00:00Z',
        mailbox: 'me@example.com',
        labels: [],
        has_attachment: true,
        attachments: [],
      },
      extraction_status: 'extracted',
      extraction_reason: null,
      markdown: 'line one of extracted text',
      truncated: false,
      text_offset: 0,
    }

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/sources/att_42')) {
          return { ok: true, status: 200, json: async () => attSource } as Response
        }
        if (String(url).includes('/preview')) {
          return {
            ok: true,
            status: 200,
            headers: new Headers({ 'content-type': 'application/pdf' }),
            text: async () => '',
            json: async () => ({}),
          } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    useWorkingSetStore.getState().setSelection({ kind: 'attachment', sid: 'att_42' })
    renderPanel()
    expect(await screen.findByTestId('attachment-card')).toBeInTheDocument()
    expect(screen.getByText('invoice.pdf')).toBeInTheDocument()
    expect(screen.getByText(/application\/pdf/)).toBeInTheDocument()
    expect(screen.getByText(/Extraction:\s*extracted/i)).toBeInTheDocument()
    expect(screen.getByTestId('attachment-extracted')).toHaveTextContent(
      'line one of extracted text',
    )
    expect(screen.getByTestId('attachment-download')).toHaveAttribute(
      'href',
      '/api/attachments/att_42/download',
    )
    fireEvent.click(screen.getByTestId('attachment-preview'))
    expect(await screen.findByTestId('preview-panel')).toBeInTheDocument()
  })

  it('pins message source to a workspace via menu', async () => {
    const posts: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: RequestInfo | URL, init?: RequestInit) => {
        const u = String(url)
        const method = (init?.method || 'GET').toUpperCase()
        if (u.includes('/api/sources/msg_1') && method === 'GET') {
          return { ok: true, status: 200, json: async () => msgSource } as Response
        }
        if (
          u.includes('/api/workspaces') &&
          method === 'GET' &&
          !u.includes('/blocks')
        ) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              items: [
                {
                  id: 'ws-1',
                  name: 'Case',
                  updated_at: null,
                  counts: {
                    blocks: 0,
                    pins: 0,
                    notes: 0,
                    answers: 0,
                    headings: 0,
                  },
                },
              ],
            }),
          } as Response
        }
        if (u.includes('/api/workspaces/ws-1/blocks') && method === 'POST') {
          posts.push(JSON.parse(String(init?.body || '{}')))
          return {
            ok: true,
            status: 201,
            json: async () => ({
              id: 'blk-1',
              workspace_id: 'ws-1',
              position: 0,
              block_type: 'pin',
              content: posts[0],
            }),
          } as Response
        }
        throw new Error(`unexpected: ${method} ${u}`)
      }),
    )

    useWorkingSetStore.getState().setSelection({ kind: 'message', sid: 'msg_1' })
    renderPanel()
    expect(await screen.findByTestId('message-card')).toBeInTheDocument()
    expect(screen.getByTestId('pin-to-workspace-btn')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('pin-to-workspace-btn'))
    expect(await screen.findByTestId('pin-workspace-menu')).toBeInTheDocument()
    fireEvent.click(await screen.findByTestId('pin-workspace-ws-1'))

    await waitFor(() => {
      expect(posts.length).toBe(1)
    })
    const body = posts[0] as {
      block_type: string
      content: { source_id: string; source_type: string; title: string }
    }
    expect(body.block_type).toBe('pin')
    expect(body.content.source_id).toBe('msg_1')
    expect(body.content.source_type).toBe('message')
    expect(body.content.title).toMatch(/Hello world/)
    expect(await screen.findByTestId('pin-status')).toHaveTextContent('Pinned')
  })
})
