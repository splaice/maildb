/**
 * Workflow D — investigate a person (spec §19.4).
 * Mocked-API: Chronicle people-lane → profile → unmerge → ego graph evidence.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChronicleBuckets, ContactCard } from '../api/types'
import { App } from '../App'
import {
  createTestQueryClient,
  mockArchiveSummary,
  mockSessionOk,
} from '../test/test-utils'
import { resetUrlSyncForTests } from '../workingset/useUrlSync'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'

const EXTENT_FROM = '2010-01-01T00:00:00.000Z'
const EXTENT_TO = '2020-01-01T00:00:00.000Z'
const CONTACT_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
const MERGE_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
const SOURCE_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
const NEIGHBOR_ID = 'dddddddd-dddd-dddd-dddd-dddddddddddd'
const THR_A = 'thr_evidence_a'
const THR_B = 'thr_evidence_b'

function mockCard(overrides: Partial<ContactCard> = {}): ContactCard {
  return {
    id: CONTACT_ID,
    display_name: 'Alice Merged',
    kind: 'human',
    kind_source: 'heuristic',
    tags: [],
    human_probability: 0.9,
    addresses: ['alice@example.com', 'wrongly-merged@example.com'],
    name_variants: ['Alice'],
    messages_from: 40,
    messages_to: 10,
    first_seen: '2012-01-01T00:00:00Z',
    last_seen: '2018-12-31T00:00:00Z',
    notes: null,
    metadata: {},
    classification_signals: { bidirectional: 0.2 },
    classified_at: '2020-01-01T00:00:00Z',
    address_classes: {
      'alice@example.com': 'external',
      'wrongly-merged@example.com': 'external',
    },
    address_details: [
      {
        address: 'alice@example.com',
        is_user: false,
        messages_from: 30,
        messages_to: 5,
        first_seen: '2012-01-01T00:00:00Z',
        last_seen: '2018-12-31T00:00:00Z',
      },
      {
        address: 'wrongly-merged@example.com',
        is_user: false,
        messages_from: 10,
        messages_to: 5,
        first_seen: '2014-01-01T00:00:00Z',
        last_seen: '2016-01-01T00:00:00Z',
      },
    ],
    activity: [
      { bucket: '2015-01-01T00:00:00Z', count: 8 },
      { bucket: '2016-01-01T00:00:00Z', count: 12 },
    ],
    topics: [{ id: 'topic-alice', label: 'Renovation', count: 5 }],
    thread_count: 18,
    merges: [
      {
        id: MERGE_ID,
        source_id: SOURCE_ID,
        target_id: CONTACT_ID,
        merged_at: '2021-06-01T00:00:00Z',
      },
    ],
    ...overrides,
  }
}

function mockBuckets(): Response {
  const body: ChronicleBuckets = {
    scope_fingerprint: 'qs_workflow_d',
    aggregation: 'year',
    unit: 'year',
    viewport: { from: EXTENT_FROM, to: EXTENT_TO },
    lanes: {
      messages: [{ bucket: '2015-01-01T00:00:00.000Z', count: 100 }],
      attachments: [],
      top_people: {
        contacts: [
          {
            contact_id: CONTACT_ID,
            display_name: 'Alice Merged',
            buckets: [{ bucket: '2015-01-01T00:00:00.000Z', count: 40 }],
          },
        ],
      },
      events: { events: [], truncated: false },
    },
    density: {
      unit: 'year',
      buckets: [{ bucket: '2015-01-01T00:00:00.000Z', count: 100 }],
    },
    extent: { from: EXTENT_FROM, to: EXTENT_TO },
    generated_at: '2026-01-01T00:00:00.000Z',
  }
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response
}

function installWorkflowDFetch() {
  let card = mockCard()
  const unmergePosts: unknown[] = []

  const graphWithEvidence = {
    nodes: [
      { id: CONTACT_ID, label: 'Alice Merged', kind: 'human', is_ego: true },
      { id: NEIGHBOR_ID, label: 'Bob Builder', kind: 'human', is_ego: false },
    ],
    edges: [
      {
        source: CONTACT_ID,
        target: NEIGHBOR_ID,
        kind: 'thread_co_participation',
        shared_threads: 2,
        first: '2015-01-01T00:00:00Z',
        last: '2016-06-01T00:00:00Z',
        evidence: { thread_ids: [THR_A, THR_B] },
      },
    ],
    truncated: false,
    total_coparticipants: 1,
  }

  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const u = String(url)
    const method = (init?.method || 'GET').toUpperCase()

    if (u.includes('/api/auth/session')) return mockSessionOk()
    if (u.includes('/api/archive/summary')) return mockArchiveSummary()
    if (u.includes('/api/chronicle/buckets')) return mockBuckets()

    if (u.includes('/api/people/unmerge') && method === 'POST') {
      const body = JSON.parse(String(init?.body || '{}')) as { merge_id?: string }
      unmergePosts.push(body)
      card = mockCard({
        addresses: ['alice@example.com'],
        address_classes: { 'alice@example.com': 'external' },
        address_details: [
          {
            address: 'alice@example.com',
            is_user: false,
            messages_from: 30,
            messages_to: 5,
            first_seen: '2012-01-01T00:00:00Z',
            last_seen: '2018-12-31T00:00:00Z',
          },
        ],
        merges: [],
        display_name: 'Alice Example',
      })
      return {
        ok: true,
        status: 200,
        json: async () => ({
          source: mockCard({
            id: SOURCE_ID,
            display_name: 'Wrong Identity',
            addresses: ['wrongly-merged@example.com'],
            merges: [],
          }),
          target: card,
        }),
      } as Response
    }

    if (u.includes(`/api/people/${CONTACT_ID}/graph`)) {
      return {
        ok: true,
        status: 200,
        json: async () => graphWithEvidence,
      } as Response
    }

    if (u.includes(`/api/people/${CONTACT_ID}`) && method === 'GET') {
      return {
        ok: true,
        status: 200,
        json: async () => card,
      } as Response
    }

    if (
      u.includes('/api/people') &&
      method === 'GET' &&
      !u.includes('/graph') &&
      !u.includes('/merge')
    ) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          items: [
            {
              id: CONTACT_ID,
              display_name: card.display_name,
              kind: 'human',
              kind_source: 'heuristic',
              tags: [],
              human_probability: 0.9,
              addresses: card.addresses,
              name_variants: ['Alice'],
              messages_from: 40,
              messages_to: 10,
              first_seen: card.first_seen,
              last_seen: card.last_seen,
            },
          ],
          next_cursor: null,
        }),
      } as Response
    }

    if (u.includes(`/api/threads/${THR_A}`) || u.includes(`/api/threads/${THR_B}`)) {
      const tid = u.includes(THR_A) ? THR_A : THR_B
      return {
        ok: true,
        status: 200,
        json: async () => ({
          id: tid,
          subject: tid === THR_A ? 'Quote discussion' : 'Site visit',
          date_range: {
            from: tid === THR_A ? '2015-02-01T00:00:00Z' : '2016-03-01T00:00:00Z',
            to: tid === THR_A ? '2015-03-01T00:00:00Z' : '2016-04-01T00:00:00Z',
          },
          participants: [
            { name: 'Alice', address: 'alice@example.com' },
            { name: 'Bob', address: 'bob@example.com' },
          ],
          messages: [
            {
              id: tid === THR_A ? 'msg_thr_a' : 'msg_thr_b',
              subject: tid === THR_A ? 'Quote discussion' : 'Site visit',
              sender_name: 'Bob',
              sender_address: 'bob@example.com',
              date: tid === THR_A ? '2015-02-01T12:00:00Z' : '2016-03-01T12:00:00Z',
            },
          ],
        }),
      } as Response
    }

    if (u.includes('/api/sources/list')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          items: [],
          next_cursor: null,
          scope_fingerprint: 'qs_workflow_d',
        }),
      } as Response
    }

    // merge-candidates rail on people list
    if (u.includes('/api/people/merge-candidates')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ items: [] }),
      } as Response
    }

    throw new Error(`unexpected fetch: ${method} ${u}`)
  })

  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, unmergePosts, getCard: () => card }
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

describe('Workflow D — investigate a person (§19.4)', () => {
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

  it('runs end-to-end: people-lane → profile → unmerge → ego evidence', async () => {
    const { fetchMock, unmergePosts } = installWorkflowDFetch()
    renderFullApp()

    // 1. Chronicle with top_people contact in fixture (people-lane row)
    expect(await screen.findByTestId('workstation-shell')).toBeInTheDocument()
    expect(await screen.findByTestId('timeline-toolbar')).toBeInTheDocument()

    // Switch to table alt so people-lane contact column is reachable
    fireEvent.click(screen.getByRole('button', { name: /View as table/i }))
    const table = await screen.findByTestId('timeline-table')
    expect(table.querySelector(`[data-contact-col="${CONTACT_ID}"]`)).toBeTruthy()
    expect(table.querySelector(`[data-contact-col="${CONTACT_ID}"]`)).toHaveTextContent(
      /Alice/,
    )

    // Open person from people-lane context → profile
    // (canvas row → people list with same contact → profile)
    fireEvent.click(screen.getByRole('link', { name: 'People' }))
    expect(await screen.findByTestId('people-list')).toBeInTheDocument()
    fireEvent.click(await screen.findByTestId(`people-row-${CONTACT_ID}`))

    expect(await screen.findByTestId('person-profile')).toBeInTheDocument()
    expect(window.location.pathname).toBe(`/people/${CONTACT_ID}`)

    // Profile renders aliases/span/activity/topics + ego co-participants
    expect(screen.getByTestId('person-identity')).toBeInTheDocument()
    expect(screen.getByTestId('person-span')).toBeInTheDocument()
    expect(screen.getByTestId('person-activity')).toBeInTheDocument()
    expect(screen.getByTestId('person-topics')).toBeInTheDocument()
    expect(await screen.findByTestId('ego-graph')).toBeInTheDocument()
    expect(screen.getByText('wrongly-merged@example.com')).toBeInTheDocument()

    // 2. Correct identity: unmerge wrongly-merged address
    expect(screen.getByTestId('person-merge-history')).toBeInTheDocument()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    fireEvent.click(screen.getByTestId(`unmerge-${MERGE_ID}`))

    await waitFor(() => {
      expect(unmergePosts.length).toBe(1)
    })
    const unmergeBody = unmergePosts[0] as { merge_id: string }
    // Pass condition: unmerge carries audit-relevant merge_id
    expect(unmergeBody.merge_id).toBe(MERGE_ID)

    // Profile refetch reflects the split (mock swap)
    await waitFor(() => {
      expect(screen.queryByText('wrongly-merged@example.com')).not.toBeInTheDocument()
    })
    expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    confirmSpy.mockRestore()

    // 3. Ego graph: only evidence-backed edges; select edge → thread list + date range
    expect(await screen.findByTestId('ego-svg')).toBeInTheDocument()
    // Render guard: edge exists only with evidence in fixture
    const edge = screen.getByTestId(`ego-edge-${NEIGHBOR_ID}`)
    expect(edge).toBeInTheDocument()
    fireEvent.click(edge)

    expect(await screen.findByTestId('ego-evidence')).toBeInTheDocument()
    // Date range visible on evidence panel
    expect(screen.getByTestId('ego-evidence')).toHaveTextContent(/2015/)
    expect(screen.getByTestId('ego-evidence')).toHaveTextContent(/2016/)
    expect(await screen.findByTestId(`ego-evidence-row-${THR_A}`)).toBeInTheDocument()
    expect(screen.getByTestId(`ego-evidence-row-${THR_B}`)).toBeInTheDocument()

    // Fixture invariant: graph response edges all have thread evidence
    const graphOk = fetchMock.mock.calls.some((c) => String(c[0]).includes('/graph'))
    expect(graphOk).toBe(true)
    // No edge without evidence in fixtures — assert rows present for every thread_id
    for (const tid of [THR_A, THR_B]) {
      expect(screen.getByTestId(`ego-evidence-row-${tid}`)).toBeInTheDocument()
    }

    // Store still healthy after profile hops
    expect(useWorkingSetStore.getState()).toBeTruthy()
  })
})
