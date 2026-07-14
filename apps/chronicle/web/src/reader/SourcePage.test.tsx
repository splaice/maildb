import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { encodeState } from '../workingset/urlState'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { SourcePage } from './SourcePage'
import { useUrlSync, resetUrlSyncForTests } from '../workingset/useUrlSync'

const msgSource = {
  kind: 'msg' as const,
  envelope: {
    id: 'msg_42',
    thread_id: 'thr_YQ',
    subject: 'Reader subject',
    sender_name: 'Alice',
    sender_address: 'alice@example.com',
    recipients: { to: ['bob@example.com'], cc: ['carol@example.com'], bcc: [] },
    date: '2014-06-01T12:00:00+00:00',
    mailbox: 'personal@example.com',
    labels: ['INBOX', 'Archive'],
    has_attachment: true,
    attachments: [
      {
        id: 'att_7',
        filename: 'note.txt',
        content_type: 'text/plain',
        size: 12,
      },
    ],
  },
  body: {
    text: 'Plain body line\n> quote',
    html: '<p>Hello</p><blockquote><p>q</p></blockquote>',
    remote_resources_blocked: 1,
    had_active_content: true,
  },
}

function renderSource(path = '/source/msg_42') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/source/:sid" element={<SourcePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SourcePage reader', () => {
  beforeEach(() => {
    resetWorkingSetStore()
    resetUrlSyncForTests()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
    resetUrlSyncForTests()
  })

  it('renders envelope, modes, attachments, and quoted HTML collapse', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/sources/msg_42')) {
          return { ok: true, status: 200, json: async () => msgSource } as Response
        }
        if (String(url).includes('/api/sources/att_7')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              kind: 'att',
              id: 'att_7',
              filename: 'note.txt',
              content_type: 'text/plain',
              size: 12,
              extraction_status: 'extracted',
              extraction_reason: null,
              markdown: 'hi',
              truncated: false,
              text_offset: 0,
              source_message_id: 'msg_42',
              source_envelope: null,
            }),
          } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    renderSource()

    expect(
      await screen.findByRole('heading', { name: 'Reader subject' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/alice@example.com/i)).toBeInTheDocument()
    expect(screen.getByText(/carol@example.com/)).toBeInTheDocument()
    expect(screen.getByText(/personal@example.com/)).toBeInTheDocument()
    expect(screen.getByText('msg_42')).toBeInTheDocument()
    expect(screen.getByText('INBOX')).toBeInTheDocument()

    // HTML reading mode with blockquote collapsed
    expect(screen.getByTestId('reader-body-html')).toBeInTheDocument()
    expect(screen.getByText(/Show quoted text/i)).toBeInTheDocument()

    // Plain text mode
    fireEvent.click(screen.getByTestId('mode-plain'))
    expect(screen.getByText(/Plain body line/)).toBeInTheDocument()

    // Attachment card
    expect(screen.getByText('note.txt')).toBeInTheDocument()
    expect(screen.getByText('att_7')).toBeInTheDocument()
  })

  it('return contract: back restores viewport + selection', async () => {
    // Chronicle URL serializes viewport + selection; navigating to /source/:sid
    // is a pushState route change; history.back() + popstate rehydrates the store.
    const chronicleState = {
      scope: {},
      viewport: {
        fromMs: Date.UTC(2014, 0, 1),
        toMs: Date.UTC(2016, 0, 1),
      },
      aggregation: 'auto' as const,
      view: 'canvas' as const,
      selection: {
        kind: 'message' as const,
        sid: 'msg_42',
      },
      lanes: null,
    }
    const qs = encodeState(chronicleState).toString()
    window.history.replaceState(null, '', qs ? `/?${qs}` : '/')
    useWorkingSetStore.getState().hydrate(chronicleState)

    // pushState like react-router navigating to the reader
    window.history.pushState(null, '', '/source/msg_42')

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/sources/msg_42')) {
          return { ok: true, status: 200, json: async () => msgSource } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    function Harness() {
      useUrlSync()
      return <SourcePage />
    }

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    // Provide :sid via MemoryRouter while Back uses window.history
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/source/msg_42']}>
          <Routes>
            <Route path="/source/:sid" element={<Harness />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByTestId('source-page')).toBeInTheDocument()

    // Mutate store so restore is observable (reader does not clear it, but
    // simulates any intermediate drift / remount)
    useWorkingSetStore.setState({
      viewport: { fromMs: 0, toMs: 1 },
      selection: null,
      historyIntent: 'silent',
    })

    fireEvent.click(screen.getByTestId('source-back'))

    await waitFor(() => {
      const s = useWorkingSetStore.getState()
      expect(s.viewport?.fromMs).toBe(Date.UTC(2014, 0, 1))
      expect(s.viewport?.toMs).toBe(Date.UTC(2016, 0, 1))
      expect(s.selection).toEqual({ kind: 'message', sid: 'msg_42' })
    })
  })
})
