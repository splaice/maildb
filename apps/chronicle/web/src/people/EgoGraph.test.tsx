import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type {
  ContactCard,
  EgoGraphResponse,
  ThreadResponse,
} from '../api/types'
import { EgoGraph } from './EgoGraph'

const EGO_ID = '11111111-1111-1111-1111-111111111111'
const BOB_ID = '22222222-2222-2222-2222-222222222222'
const THR_A = 'thr_YQ'
const THR_B = 'thr_Yg'

function mockCard(overrides: Partial<ContactCard> = {}): ContactCard {
  return {
    id: EGO_ID,
    display_name: 'Ego Person',
    kind: 'human',
    kind_source: 'manual',
    tags: [],
    human_probability: 0.9,
    addresses: ['ego@example.com'],
    name_variants: [],
    messages_from: 10,
    messages_to: 2,
    first_seen: '2014-01-01T00:00:00Z',
    last_seen: '2018-12-31T00:00:00Z',
    notes: null,
    metadata: {},
    classification_signals: null,
    classified_at: null,
    address_classes: { 'ego@example.com': 'external' },
    address_details: [],
    activity: [],
    topics: [],
    thread_count: 5,
    ...overrides,
  }
}

function mockGraph(overrides: Partial<EgoGraphResponse> = {}): EgoGraphResponse {
  return {
    nodes: [
      { id: EGO_ID, label: 'Ego Person', kind: 'human', is_ego: true },
      { id: BOB_ID, label: 'Alice Chen', kind: 'human', is_ego: false },
      {
        id: 'addr:stranger@example.com',
        label: 'stranger@example.com',
        kind: 'address',
        is_ego: false,
      },
    ],
    edges: [
      {
        source: EGO_ID,
        target: BOB_ID,
        kind: 'thread_co_participation',
        shared_threads: 12,
        first: '2014-01-01',
        last: '2018-06-01',
        evidence: { thread_ids: [THR_A, THR_B] },
      },
      {
        source: EGO_ID,
        target: 'addr:stranger@example.com',
        kind: 'thread_co_participation',
        shared_threads: 3,
        first: '2015-01-01',
        last: '2015-06-01',
        evidence: { thread_ids: [THR_A] },
      },
    ],
    truncated: true,
    total_coparticipants: 40,
    ...overrides,
  }
}

function mockThread(tid: string, subject: string): ThreadResponse {
  return {
    thread_id: tid,
    subject,
    date_range: { from: '2015-01-01T00:00:00Z', to: '2015-02-01T00:00:00Z' },
    participants: [{ name: 'Alice Chen', address: 'alice@example.com' }],
    message_count: 2,
    messages: [
      {
        id: `msg_${tid}`,
        subject,
        sender_name: 'Alice Chen',
        sender_address: 'alice@example.com',
        recipients: {},
        date: '2015-01-01T00:00:00Z',
        mailbox: 'inbox',
        labels: [],
        has_attachment: false,
      },
    ],
    truncated: false,
  }
}

function renderEgo(
  graph: EgoGraphResponse = mockGraph(),
  card: ContactCard = mockCard(),
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/graph')) {
      return {
        ok: true,
        status: 200,
        json: async () => graph,
      } as Response
    }
    if (url.includes(`/api/threads/${THR_A}`)) {
      return {
        ok: true,
        status: 200,
        json: async () => mockThread(THR_A, 'Roof repair plan'),
      } as Response
    }
    if (url.includes(`/api/threads/${THR_B}`)) {
      return {
        ok: true,
        status: 200,
        json: async () => mockThread(THR_B, 'Follow-up quotes'),
      } as Response
    }
    return {
      ok: false,
      status: 404,
      json: async () => ({}),
    } as Response
  })
  vi.stubGlobal('fetch', fetchMock)

  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/people/${EGO_ID}`]}>
        <Routes>
          <Route
            path="/people/:id"
            element={<EgoGraph contactId={EGO_ID} card={card} />}
          />
          <Route
            path="/source/:sid"
            element={<div data-testid="source-page">Source</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...view, fetchMock }
}

describe('EgoGraph', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders from mock with sizes/widths monotonic in shared_threads', async () => {
    renderEgo()
    await screen.findByTestId('ego-svg')
    expect(screen.getByTestId('ego-truncated')).toHaveTextContent(
      'Showing 2 of 40 co-participants',
    )

    const edgeBob = screen.getByTestId(`ego-edge-${BOB_ID}`)
    const edgeAddr = screen.getByTestId(
      'ego-edge-addr:stranger@example.com',
    )
    const wBob = Number(edgeBob.getAttribute('data-width'))
    const wAddr = Number(edgeAddr.getAttribute('data-width'))
    expect(wBob).toBeGreaterThan(wAddr)
    expect(wBob).toBeLessThanOrEqual(4)
    expect(wAddr).toBeGreaterThanOrEqual(1)

    const nodeBob = screen.getByTestId(`ego-node-${BOB_ID}`)
    const nodeAddr = screen.getByTestId(
      'ego-node-addr:stranger@example.com',
    )
    const rBob = Number(nodeBob.getAttribute('data-radius'))
    const rAddr = Number(nodeAddr.getAttribute('data-radius'))
    expect(rBob).toBeGreaterThan(rAddr)
  })

  it('edge click loads evidence rows via thread fetch', async () => {
    const { fetchMock } = renderEgo()
    await screen.findByTestId('ego-svg')
    fireEvent.click(screen.getByTestId(`ego-edge-${BOB_ID}`))

    expect(await screen.findByTestId('ego-evidence')).toHaveTextContent(
      '12 shared threads with Alice Chen (2014–2018)',
    )

    await waitFor(() => {
      const thrCalls = fetchMock.mock.calls.filter((c) =>
        String(c[0]).includes('/api/threads/'),
      )
      expect(thrCalls.length).toBeGreaterThanOrEqual(2)
    })

    expect(
      await screen.findByTestId(`ego-evidence-row-${THR_A}`),
    ).toHaveTextContent('Roof repair plan')
    expect(screen.getByTestId(`ego-evidence-row-${THR_B}`)).toHaveTextContent(
      'Follow-up quotes',
    )
  })

  it('table alternative has name / shared threads / first / last parity', async () => {
    renderEgo()
    await screen.findByTestId('ego-view-toggle')
    fireEvent.click(screen.getByTestId('ego-view-toggle'))
    const table = await screen.findByTestId('ego-table')
    expect(table).toHaveTextContent('Name')
    expect(table).toHaveTextContent('Shared threads')
    expect(table).toHaveTextContent('First')
    expect(table).toHaveTextContent('Last')
    expect(table).toHaveTextContent('Alice Chen')
    expect(table).toHaveTextContent('12')
    expect(table).toHaveTextContent('2014-01-01')
    expect(table).toHaveTextContent('2018-06-01')
    expect(table).toHaveTextContent('stranger@example.com')
  })

  it('nodes and edges are focusable with shared-thread labels', async () => {
    renderEgo()
    await screen.findByTestId('ego-svg')
    const node = screen.getByTestId(`ego-node-${BOB_ID}`)
    expect(node).toHaveAttribute('tabindex', '0')
    expect(node).toHaveAttribute(
      'aria-label',
      'Alice Chen — 12 shared threads',
    )
    const edge = screen.getByTestId(`ego-edge-${BOB_ID}`)
    expect(edge).toHaveAttribute('tabindex', '0')
    expect(edge).toHaveAttribute(
      'aria-label',
      'Alice Chen — 12 shared threads',
    )
  })

  it('address node shows not yet a contact text', async () => {
    renderEgo()
    await screen.findByTestId('ego-svg')
    fireEvent.click(
      screen.getByTestId('ego-node-addr:stranger@example.com'),
    )
    expect(await screen.findByTestId('ego-address-note')).toHaveTextContent(
      'not yet a contact',
    )
  })

  it('evidence row links to thread in the reader', async () => {
    renderEgo()
    await screen.findByTestId('ego-svg')
    fireEvent.click(screen.getByTestId(`ego-edge-${BOB_ID}`))
    await waitFor(() => {
      const row = screen.getByTestId(`ego-evidence-row-${THR_A}`)
      expect(row.tagName.toLowerCase()).toBe('a')
      expect(row).toHaveAttribute(
        'href',
        `/source/${encodeURIComponent(`msg_${THR_A}`)}?thread=1`,
      )
    })
  })
})
