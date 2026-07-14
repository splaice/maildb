import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
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
      markdown: null,
      truncated: false,
      text_offset: 0,
    }

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/sources/att_42')) {
          return { ok: true, status: 200, json: async () => attSource } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    useWorkingSetStore.getState().setSelection({ kind: 'attachment', sid: 'att_42' })
    renderPanel()
    expect(await screen.findByTestId('attachment-card')).toBeInTheDocument()
    expect(screen.getByText('invoice.pdf')).toBeInTheDocument()
    expect(screen.getByText(/application\/pdf/)).toBeInTheDocument()
    expect(screen.getByText(/extracted/i)).toBeInTheDocument()
  })
})
