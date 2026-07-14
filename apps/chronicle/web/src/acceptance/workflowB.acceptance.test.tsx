/**
 * Workflow B — explore an unknown period (spec §19.2).
 * Mocked-API end-to-end: 2007 burst → Focus → Topic Atlas (same scope) →
 * topic select → Chronicle → save workspace with scope.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChronicleBuckets, QueryScope } from '../api/types'
import { App } from '../App'
import {
  createTestQueryClient,
  mockArchiveSummary,
  mockSessionOk,
} from '../test/test-utils'
import { resetUrlSyncForTests, writeStoreToUrlNow } from '../workingset/useUrlSync'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'

const EXTENT_FROM = '2005-01-01T00:00:00.000Z'
const EXTENT_TO = '2010-01-01T00:00:00.000Z'
const TOPIC_ID = 'topic-2007-burst'
const WS_ID = 'ws-workflow-b'

const burst2007 = {
  fromMs: Date.UTC(2007, 0, 1),
  toMs: Date.UTC(2008, 0, 1),
}

function mockBuckets(overrides: Partial<ChronicleBuckets> = {}): Response {
  const body: ChronicleBuckets = {
    scope_fingerprint: 'qs_workflow_b',
    aggregation: 'year',
    unit: 'year',
    viewport: { from: EXTENT_FROM, to: EXTENT_TO },
    lanes: {
      messages: [
        { bucket: '2005-01-01T00:00:00.000Z', count: 40 },
        { bucket: '2006-01-01T00:00:00.000Z', count: 55 },
        // Activity burst in 2007
        { bucket: '2007-01-01T00:00:00.000Z', count: 480 },
        { bucket: '2008-01-01T00:00:00.000Z', count: 70 },
        { bucket: '2009-01-01T00:00:00.000Z', count: 60 },
      ],
      attachments: [{ bucket: '2007-01-01T00:00:00.000Z', count: 42 }],
      top_people: {
        contacts: [
          {
            contact_id: 'contact-alice',
            display_name: 'Alice',
            buckets: [{ bucket: '2007-01-01T00:00:00.000Z', count: 30 }],
          },
        ],
      },
      topics: {
        topics: [
          {
            topic_id: TOPIC_ID,
            label: 'Kitchen remodel',
            origin: 'automatic',
            buckets: [{ bucket: '2007-01-01T00:00:00.000Z', count: 80 }],
          },
        ],
      },
      events: { events: [], truncated: false },
    },
    density: {
      unit: 'year',
      buckets: [
        { bucket: '2005-01-01T00:00:00.000Z', count: 40 },
        { bucket: '2006-01-01T00:00:00.000Z', count: 55 },
        { bucket: '2007-01-01T00:00:00.000Z', count: 480 },
        { bucket: '2008-01-01T00:00:00.000Z', count: 70 },
        { bucket: '2009-01-01T00:00:00.000Z', count: 60 },
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
          id: 'msg_2007_1',
          subject: 'Kitchen plans',
          sender_name: 'Alice',
          sender_address: 'alice@example.com',
          date: '2007-03-15T12:00:00Z',
          mailbox: 'me@example.com',
          has_attachment: true,
          attachment_count: 1,
          thread_id: 'thr_kitchen',
        },
        {
          id: 'msg_2007_2',
          subject: 'Tile samples',
          sender_name: 'Bob',
          sender_address: 'bob@example.com',
          date: '2007-04-01T12:00:00Z',
          mailbox: 'me@example.com',
          has_attachment: false,
          attachment_count: 0,
          thread_id: 'thr_kitchen',
        },
      ],
      next_cursor: null,
      scope_fingerprint: 'qs_workflow_b',
    }),
  } as Response
}

function installWorkflowBFetch() {
  const createdWorkspaces: { name: string; scope: QueryScope }[] = []

  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const u = String(url)
    const method = (init?.method || 'GET').toUpperCase()

    if (u.includes('/api/auth/session')) return mockSessionOk()
    if (u.includes('/api/archive/summary')) return mockArchiveSummary({
      date_range: { from: '2005-01-01T00:00:00', to: '2009-12-31T00:00:00' },
    })
    if (u.includes('/api/chronicle/buckets')) return mockBuckets()
    if (u.includes('/api/sources/list')) return mockSourceList()

    if (u.includes('/api/topics/') && u.includes('/members')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          items: [
            {
              id: 'msg_2007_1',
              subject: 'Kitchen plans',
              sender_name: 'Alice',
              sender_address: 'alice@example.com',
              date: '2007-03-15T12:00:00Z',
              mailbox: 'me@example.com',
              has_attachment: true,
              attachment_count: 1,
              thread_id: 'thr_kitchen',
            },
          ],
          next_cursor: null,
        }),
      } as Response
    }
    if (u.includes(`/api/topics/${TOPIC_ID}`) && !u.includes('/members')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          id: TOPIC_ID,
          label: 'Kitchen remodel',
          origin: 'automatic',
          description: null,
          parent_id: null,
          hidden: false,
          member_count: 12,
          generation: 1,
          top_terms: ['kitchen', 'tile'],
          created_at: null,
          updated_at: null,
          activity: [{ bucket: '2007-01-01T00:00:00Z', count: 80 }],
          members: [
            {
              id: 'msg_2007_1',
              subject: 'Kitchen plans',
              sender_name: 'Alice',
              sender_address: 'alice@example.com',
              date: '2007-03-15T12:00:00Z',
              mailbox: 'me@example.com',
              thread_id: 'thr_kitchen',
              distance: 0.1,
            },
          ],
        }),
      } as Response
    }
    if (u.includes('/api/topics/river')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          unit: 'month',
          mode_hint: 'absolute',
          from: '2007-01-01T00:00:00Z',
          to: '2008-01-01T00:00:00Z',
          topics: [
            {
              topic_id: TOPIC_ID,
              label: 'Kitchen remodel',
              origin: 'automatic',
              buckets: [
                { bucket: '2007-03-01T00:00:00Z', count: 20 },
                { bucket: '2007-04-01T00:00:00Z', count: 30 },
              ],
            },
          ],
        }),
      } as Response
    }
    if (u.includes('/api/topics') && method === 'GET' && !u.includes('river') && !u.includes('matrix') && !u.includes('projection')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          topics: [
            {
              id: TOPIC_ID,
              label: 'Kitchen remodel',
              origin: 'automatic',
              member_count: 12,
              hidden: false,
              children: [],
              top_terms: ['kitchen', 'tile'],
            },
          ],
        }),
      } as Response
    }

    if (u.includes('/api/workspaces') && method === 'GET' && !u.includes('/blocks')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ items: [] }),
      } as Response
    }
    if (u.includes('/api/workspaces') && method === 'POST' && !u.includes('/blocks')) {
      const body = JSON.parse(String(init?.body || '{}')) as {
        name: string
        scope: QueryScope
      }
      createdWorkspaces.push(body)
      return {
        ok: true,
        status: 201,
        json: async () => ({
          id: WS_ID,
          name: body.name,
          scope: body.scope,
          version: 1,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          counts: { blocks: 0, pins: 0, notes: 0, answers: 0, headings: 0 },
        }),
      } as Response
    }
    if (u.includes(`/api/workspaces/${WS_ID}`)) {
      const last = createdWorkspaces[createdWorkspaces.length - 1]
      return {
        ok: true,
        status: 200,
        json: async () => ({
          id: WS_ID,
          name: last?.name ?? '2007 period',
          scope: last?.scope ?? {},
          version: 1,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          counts: { blocks: 0, pins: 0, notes: 0, answers: 0, headings: 0 },
          blocks: [],
        }),
      } as Response
    }

    throw new Error(`unexpected fetch: ${method} ${u}`)
  })

  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, createdWorkspaces }
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

function scopeSnapshot() {
  const s = useWorkingSetStore.getState()
  return {
    scope: structuredClone(s.scope),
    focus: s.focus ? { ...s.focus } : null,
    viewport: s.viewport ? { ...s.viewport } : null,
  }
}

describe('Workflow B — explore an unknown period (§19.2)', () => {
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

  it('runs end-to-end: 2007 burst → Focus → Topics river → Chronicle → workspace', async () => {
    const { createdWorkspaces } = installWorkflowBFetch()
    renderFullApp()

    // 1. Full-archive Chronicle; activity burst in 2007 (fixture)
    expect(await screen.findByTestId('workstation-shell')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chronicle' })).toBeInTheDocument()
    expect(await screen.findByTestId('timeline-toolbar')).toBeInTheDocument()

    // Double-click burst → Focus mode (store path mirrors canvas double-click)
    useWorkingSetStore.getState().setFocus(burst2007)
    writeStoreToUrlNow()

    expect(await screen.findByTestId('focus-mode')).toBeInTheDocument()
    expect(screen.getByTestId('focus-period-label')).toHaveTextContent(/2007/)
    // Authoritative source list in focus
    expect(await screen.findByTestId('source-list')).toBeInTheDocument()
    expect(screen.getByTestId('focus-source-sequence')).toBeInTheDocument()

    // Apply focus as scope date so chips reflect the period across lens hops
    fireEvent.click(screen.getByTestId('focus-set-scope-date'))
    await waitFor(() => {
      expect(screen.queryByTestId('focus-mode')).not.toBeInTheDocument()
    })
    expect(await screen.findByTestId('scope-chip-date')).toBeInTheDocument()
    const snapAfterFocus = scopeSnapshot()
    expect(snapAfterFocus.scope.date).toBeTruthy()

    // 2. Open Topic Atlas via nav — same scope chips; river for the range
    fireEvent.click(screen.getByRole('link', { name: 'Topics' }))
    expect(await screen.findByTestId('topics-page')).toBeInTheDocument()
    // Scope bar chips unchanged
    expect(screen.getByTestId('scope-chip-date')).toBeInTheDocument()
    const snapOnTopics = scopeSnapshot()
    expect(snapOnTopics.scope).toEqual(snapAfterFocus.scope)

    fireEvent.click(screen.getByTestId('topic-view-river'))
    expect(await screen.findByTestId('river-view')).toBeInTheDocument()
    // River renders for the range without re-entering it
    await waitFor(() => {
      expect(screen.queryByTestId('river-empty')).not.toBeInTheDocument()
    })

    // 3. Select a topic cluster → back to Chronicle — scope intact
    // Prefer hierarchy select for reliable selection + member list
    fireEvent.click(screen.getByTestId('topic-view-hierarchy'))
    expect(await screen.findByTestId('hierarchy-view')).toBeInTheDocument()
    fireEvent.click(await screen.findByTestId(`topic-treeitem-${TOPIC_ID}`))

    // Topic card exposes authoritative member list
    expect(await screen.findByTestId('topic-card')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('topic-open-sources'))
    expect(await screen.findByTestId('topic-member-list')).toBeInTheDocument()
    // Focus source list was reachable earlier; topic member list is the
    // authoritative aggregated-visual list for the Atlas hop.
    expect(screen.getByTestId('topic-list-member-msg_2007_1')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: 'Chronicle' }))
    expect(await screen.findByRole('heading', { name: 'Chronicle' })).toBeInTheDocument()
    expect(screen.getByTestId('scope-chip-date')).toBeInTheDocument()
    const snapBack = scopeSnapshot()
    expect(snapBack.scope).toEqual(snapAfterFocus.scope)

    // 4. Save the period as a workspace (create with current scope)
    fireEvent.click(screen.getByRole('link', { name: 'Workspaces' }))
    expect(await screen.findByTestId('workspaces-list-page')).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('new-workspace-name'), {
      target: { value: '2007 exploration' },
    })
    // use current scope checked by default
    expect(screen.getByTestId('use-current-scope')).toBeChecked()
    fireEvent.click(screen.getByTestId('create-workspace'))

    await waitFor(() => {
      expect(createdWorkspaces.length).toBe(1)
    })
    const ws = createdWorkspaces[0]!
    expect(ws.name).toBe('2007 exploration')
    expect(ws.scope).toEqual(snapAfterFocus.scope)

    // Pass condition: working set identical across lens changes; authoritative lists reachable
    expect(snapOnTopics.scope).toEqual(snapAfterFocus.scope)
    expect(snapBack.scope).toEqual(snapAfterFocus.scope)
  })
})
