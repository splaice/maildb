/**
 * Workflow A — reconstruct a decision (spec §19.1).
 * Mocked-API end-to-end: focus June 2015 → automatic decision event →
 * reconstruction → source → double Back → confirm → pin.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { BrowserRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChronicleBuckets } from '../api/types'
import { App } from '../App'
import {
  createTestQueryClient,
  mockArchiveSummary,
  mockSessionOk,
} from '../test/test-utils'
import { resetUrlSyncForTests, writeStoreToUrlNow } from '../workingset/useUrlSync'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'

const EXTENT_FROM = '2014-01-01T00:00:00.000Z'
const EXTENT_TO = '2019-01-01T00:00:00.000Z'
const EVENT_ID = 'evt-decision-june-2015'

const june2015Focus = {
  fromMs: Date.UTC(2015, 5, 1),
  toMs: Date.UTC(2015, 6, 1),
}

const automaticDecision = {
  id: EVENT_ID,
  title: 'Roof material decision',
  time_start: '2015-06-15T00:00:00Z',
  time_end: null,
  time_precision: 'day',
  origin: 'automatic',
  event_type: 'decision',
  status: 'unreviewed',
  evidence_strength: 'high',
  current_version: 1,
  summary: 'Chose metal over slate after June quotes.',
  derivation: {
    generated_at: '2026-07-13T12:00:00Z',
    process_version: 'event-v1',
    model_route: 'local-llama',
    scope_fingerprint: 'qs_workflow_a',
  },
  version: {
    version: 1,
    author: 'automatic',
    title: 'Roof material decision',
    summary: 'Chose metal over slate after June quotes.',
    derivation: {
      generated_at: '2026-07-13T12:00:00Z',
      process_version: 'event-v1',
      model_route: 'local-llama',
      scope_fingerprint: 'qs_workflow_a',
    },
  },
  claims: [
    {
      id: 'c-direct',
      position: 0,
      text: 'Metal roof was selected',
      status: 'direct',
      citations: [
        {
          source_id: 'msg_metal_1',
          source_type: 'message',
          subject: 'Metal quote accepted',
          sender: 'Alice',
          date: '2015-06-10T12:00:00Z',
          excerpt: 'we go with metal',
          excerpt_hash: 'hash-m1',
        },
        {
          source_id: 'msg_metal_2',
          source_type: 'message',
          subject: 'Confirm metal',
          sender: 'Bob',
          date: '2015-06-12T12:00:00Z',
          excerpt: 'metal confirmed',
          excerpt_hash: 'hash-m2',
        },
      ],
    },
    {
      id: 'c-conflict',
      position: 1,
      text: 'Or slate instead?',
      status: 'conflicting',
      citations: [
        {
          source_id: 'msg_slate_1',
          source_type: 'message',
          subject: 'Prefer slate',
          sender: 'Carol',
          date: '2015-06-14T12:00:00Z',
          excerpt: 'prefer slate for look',
          excerpt_hash: 'hash-s1',
        },
        {
          source_id: 'msg_slate_2',
          source_type: 'message',
          subject: 'Slate warranty',
          sender: 'Dave',
          date: '2015-06-15T12:00:00Z',
          excerpt: 'slate warranty longer',
          excerpt_hash: 'hash-s2',
        },
      ],
    },
  ],
  conflicts: [{ claim_position: 1, statuses: ['conflicting'] }],
}

function mockBuckets(overrides: Partial<ChronicleBuckets> = {}): Response {
  const body: ChronicleBuckets = {
    scope_fingerprint: 'qs_workflow_a',
    aggregation: 'month',
    unit: 'month',
    viewport: { from: EXTENT_FROM, to: EXTENT_TO },
    lanes: {
      messages: [
        { bucket: '2015-01-01T00:00:00.000Z', count: 100 },
        { bucket: '2015-06-01T00:00:00.000Z', count: 220 },
      ],
      attachments: [{ bucket: '2015-06-01T00:00:00.000Z', count: 12 }],
      events: {
        events: [
          {
            event_id: EVENT_ID,
            title: automaticDecision.title,
            time_start: automaticDecision.time_start,
            time_end: null,
            time_precision: 'day',
            origin: 'automatic',
            event_type: 'decision',
            status: automaticDecision.status,
            evidence_strength: 'high',
          },
        ],
        truncated: false,
      },
    },
    density: {
      unit: 'year',
      buckets: [
        { bucket: '2014-01-01T00:00:00.000Z', count: 100 },
        { bucket: '2015-01-01T00:00:00.000Z', count: 320 },
      ],
    },
    extent: { from: EXTENT_FROM, to: EXTENT_TO },
    generated_at: '2026-01-01T00:00:00.000Z',
    ...overrides,
  }
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response
}

function mockSourceList() {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      items: [
        {
          id: 'msg_metal_1',
          subject: 'Metal quote accepted',
          sender_name: 'Alice',
          sender_address: 'alice@example.com',
          date: '2015-06-10T12:00:00Z',
          mailbox: 'me@example.com',
          has_attachment: false,
          attachment_count: 0,
          thread_id: 'thr_roof',
        },
        {
          id: 'msg_slate_1',
          subject: 'Prefer slate',
          sender_name: 'Carol',
          sender_address: 'carol@example.com',
          date: '2015-06-14T12:00:00Z',
          mailbox: 'me@example.com',
          has_attachment: true,
          attachment_count: 1,
          thread_id: 'thr_roof',
        },
      ],
      next_cursor: null,
      scope_fingerprint: 'qs_workflow_a',
    }),
  } as Response
}

function mockMessageSource(sid: string) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      kind: 'msg',
      envelope: {
        id: sid,
        thread_id: 'thr_roof',
        subject: sid === 'msg_slate_1' ? 'Prefer slate' : 'Metal quote accepted',
        sender_name: 'Alice',
        sender_address: 'alice@example.com',
        recipients: { to: ['bob@example.com'], cc: [], bcc: [] },
        date: '2015-06-10T12:00:00Z',
        mailbox: 'me@example.com',
        labels: [],
        has_attachment: false,
        attachments: [],
      },
      body: {
        text: 'body for ' + sid,
        html: null,
        remote_resources_blocked: 0,
        had_active_content: false,
      },
    }),
  } as Response
}

function installWorkflowFetch() {
  let eventStatus = 'unreviewed'
  const pinPosts: unknown[] = []

  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const u = String(url)
    const method = (init?.method || 'GET').toUpperCase()

    if (u.includes('/api/auth/session')) return mockSessionOk()
    if (u.includes('/api/archive/summary')) return mockArchiveSummary()
    if (u.includes('/api/chronicle/buckets')) return mockBuckets()
    if (u.includes('/api/sources/list')) return mockSourceList()
    if (u.includes('/api/sources/')) {
      const sid = u.split('/api/sources/')[1]?.split('?')[0] ?? 'msg_metal_1'
      return mockMessageSource(decodeURIComponent(sid))
    }
    if (u.includes(`/api/events/${EVENT_ID}`) && method === 'PATCH') {
      const body = JSON.parse(String(init?.body || '{}')) as {
        current_version?: number
        status?: string
      }
      if (body.status) eventStatus = body.status
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ...automaticDecision,
          status: eventStatus,
          current_version: body.current_version ?? 1,
        }),
      } as Response
    }
    if (u.includes(`/api/events/${EVENT_ID}`) && !u.includes('/versions')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ ...automaticDecision, status: eventStatus }),
      } as Response
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
              id: 'ws-workflow-a',
              name: 'Renovation case',
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
    if (u.includes('/api/workspaces/ws-workflow-a/blocks') && method === 'POST') {
      const body = JSON.parse(String(init?.body || '{}'))
      pinPosts.push(body)
      return {
        ok: true,
        status: 201,
        json: async () => ({
          id: 'blk-pin-1',
          workspace_id: 'ws-workflow-a',
          position: 0,
          block_type: 'pin',
          content: (body as { content: unknown }).content,
        }),
      } as Response
    }
    throw new Error(`unexpected fetch: ${method} ${u}`)
  })

  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, pinPosts, getEventStatus: () => eventStatus }
}

function renderFullApp() {
  const client = createTestQueryClient()
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('Workflow A — reconstruct a decision (§19.1)', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
    resetUrlSyncForTests()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
    resetUrlSyncForTests()
  })

  it('runs end-to-end: focus → event → reconstruction → double Back → confirm → pin', async () => {
    const { pinPosts, fetchMock } = installWorkflowFetch()
    renderFullApp()

    // 1. Open Chronicle; brush June 2015; enter Focus mode
    expect(await screen.findByTestId('workstation-shell')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chronicle' })).toBeInTheDocument()
    expect(await screen.findByTestId('timeline-toolbar')).toBeInTheDocument()

    useWorkingSetStore.getState().setBrush(june2015Focus)
    await waitFor(() => {
      expect(screen.getByTestId('focus-period-btn')).not.toBeDisabled()
    })
    fireEvent.click(screen.getByTestId('focus-period-btn'))

    expect(await screen.findByTestId('focus-mode')).toBeInTheDocument()
    expect(screen.getByTestId('focus-header')).toBeInTheDocument()
    expect(screen.getByTestId('focus-period-label')).toHaveTextContent(/Jun 2015|June 2015/)
    expect(await screen.findByTestId('source-list')).toBeInTheDocument()
    expect(screen.getByTestId('focus-source-sequence')).toBeInTheDocument()

    // 2. Inferred decision event visible; select it — inspector evidence/status/claims
    useWorkingSetStore.getState().setSelection({
      kind: 'event',
      eventId: EVENT_ID,
    })
    // Flush transient selection into the window URL before leaving Chronicle
    // (useUrlSync debounce would otherwise drop sel= on reconstruction nav).
    writeStoreToUrlNow()
    expect(await screen.findByTestId('event-card')).toBeInTheDocument()
    expect(screen.getByTestId('event-origin-badge')).toHaveTextContent(/Automatic/)
    expect(screen.getByTestId('event-evidence-strength')).toHaveTextContent(/high/)
    expect(screen.getByTestId('event-status-badge')).toHaveTextContent(/unreviewed/)
    expect(screen.getByTestId('event-claims')).toBeInTheDocument()
    const inspectorClaims = screen.getAllByTestId('event-claim')
    expect(inspectorClaims).toHaveLength(2)

    // 3. Open reconstruction — both claims; conflicting shows Supporting + Conflicting
    fireEvent.click(screen.getByTestId('event-reconstruction'))
    expect(await screen.findByTestId('reconstruction-view')).toBeInTheDocument()
    expect(screen.getByTestId('claim-matrix')).toBeInTheDocument()

    const claimRows = screen.getAllByTestId('claim-matrix-row')
    expect(claimRows).toHaveLength(2)
    expect(claimRows[0]).toHaveTextContent(/Metal roof was selected/)
    expect(claimRows[0]).toHaveTextContent(/2 citations/)
    expect(claimRows[1]).toHaveTextContent(/Or slate instead/)
    expect(claimRows[1]).toHaveTextContent(/2 citations/)

    // Fixture invariant through UI: every claim row has citation count ≥ 1
    for (const row of claimRows) {
      const text = row.textContent ?? ''
      const match = text.match(/(\d+)\s+citation/)
      expect(match).not.toBeNull()
      expect(Number(match![1])).toBeGreaterThanOrEqual(1)
    }

    fireEvent.click(claimRows[1]!)
    expect(await screen.findByTestId('conflict-chains')).toBeInTheDocument()
    expect(screen.getByTestId('supporting-heading')).toHaveTextContent(/Supporting/)
    expect(screen.getByTestId('conflicting-heading')).toHaveTextContent(/Conflicting/)

    // 4. Open original message from a citation → Back → recon → Back → focused Chronicle
    const focusBeforeSource = useWorkingSetStore.getState().focus
    const viewportBeforeSource = useWorkingSetStore.getState().viewport
    const selectionBeforeSource = useWorkingSetStore.getState().selection

    const citations = screen.getAllByTestId('recon-citation')
    expect(citations.length).toBeGreaterThanOrEqual(1)
    fireEvent.click(within(citations[0]!).getByTestId('recon-citation-toggle'))
    fireEvent.click(await screen.findByTestId('recon-citation-open-full'))

    expect(await screen.findByTestId('source-page')).toBeInTheDocument()
    expect(window.location.pathname).toMatch(/^\/source\//)

    fireEvent.click(screen.getByTestId('source-back'))
    expect(
      await screen.findByTestId('reconstruction-back', {}, { timeout: 3000 }),
    ).toBeInTheDocument()
    expect(screen.getByTestId('reconstruction-view')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('reconstruction-back'))
    expect(await screen.findByTestId('focus-mode')).toBeInTheDocument()
    await waitFor(() => {
      const s = useWorkingSetStore.getState()
      expect(s.focus).toEqual(focusBeforeSource)
      expect(s.selection).toEqual(selectionBeforeSource)
      expect(s.selection).toEqual({ kind: 'event', eventId: EVENT_ID })
      if (viewportBeforeSource) {
        expect(s.viewport?.fromMs).toBe(viewportBeforeSource.fromMs)
        expect(s.viewport?.toMs).toBe(viewportBeforeSource.toMs)
      }
    })

    // 5. Confirm event; pin to workspace
    expect(await screen.findByTestId('event-card')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('event-confirm'))
    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(
        (c) => (c[1] as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patchCalls.length).toBeGreaterThanOrEqual(1)
      const body = JSON.parse(String((patchCalls[0]![1] as RequestInit).body)) as {
        status?: string
      }
      expect(body.status).toBe('confirmed')
    })

    fireEvent.click(screen.getByTestId('pin-to-workspace-btn'))
    expect(await screen.findByTestId('pin-workspace-menu')).toBeInTheDocument()
    fireEvent.click(await screen.findByTestId('pin-workspace-ws-workflow-a'))

    await waitFor(() => {
      expect(pinPosts.length).toBe(1)
    })
    const pinBody = pinPosts[0] as {
      block_type: string
      content: { source_id: string; source_type: string; title: string }
    }
    expect(pinBody.block_type).toBe('pin')
    expect(pinBody.content.source_id).toBe(EVENT_ID)
    expect(pinBody.content.title).toMatch(/Roof material decision/)
    expect(await screen.findByTestId('pin-status')).toHaveTextContent('Pinned')
  })
})
