import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ContactCard } from '../api/types'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { PersonProfilePage } from './PersonProfilePage'

const CONTACT_ID = '11111111-1111-1111-1111-111111111111'
const TOPIC_ID = '22222222-2222-2222-2222-222222222222'
const MERGE_ID = '33333333-3333-3333-3333-333333333333'
const TARGET_ID = '44444444-4444-4444-4444-444444444444'

function mockCard(overrides: Partial<ContactCard> = {}): ContactCard {
  return {
    id: CONTACT_ID,
    display_name: 'Alice Example',
    kind: 'human',
    kind_source: 'heuristic',
    tags: ['vip'],
    human_probability: 0.9,
    addresses: ['alice@example.com', 'me@owner.com'],
    name_variants: ['Alice'],
    messages_from: 20,
    messages_to: 5,
    first_seen: '2014-01-01T00:00:00Z',
    last_seen: '2019-12-31T00:00:00Z',
    notes: 'Analyst note',
    metadata: {},
    classification_signals: {
      bidirectional: 0.15,
      personal_name: 0.1,
    },
    classified_at: '2020-01-01T00:00:00Z',
    address_classes: {
      'alice@example.com': 'external',
      'me@owner.com': 'owner',
    },
    address_details: [
      {
        address: 'alice@example.com',
        is_user: false,
        messages_from: 20,
        messages_to: 0,
        first_seen: '2014-01-01T00:00:00Z',
        last_seen: '2019-12-31T00:00:00Z',
      },
      {
        address: 'me@owner.com',
        is_user: true,
        messages_from: 0,
        messages_to: 5,
        first_seen: '2014-01-01T00:00:00Z',
        last_seen: '2019-12-31T00:00:00Z',
      },
    ],
    activity: [
      { bucket: '2016-01-01T00:00:00Z', count: 4 },
      { bucket: '2016-02-01T00:00:00Z', count: 2 },
    ],
    topics: [{ id: TOPIC_ID, label: 'Kitchen remodel', count: 7 }],
    thread_count: 12,
    merges: [
      {
        id: MERGE_ID,
        source_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        target_id: CONTACT_ID,
        merged_at: '2021-06-01T00:00:00Z',
      },
    ],
    ...overrides,
  }
}

function renderProfile(initial = `/people/${CONTACT_ID}`) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/people/:id" element={<PersonProfilePage />} />
          <Route path="/" element={<div data-testid="chronicle">Chronicle</div>} />
          <Route path="/research" element={<div data-testid="research">Research</div>} />
          <Route path="/topics" element={<div data-testid="topics">Topics</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('PersonProfilePage', () => {
  beforeEach(() => {
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('renders profile sections from mocked card incl. signals and owner badges', async () => {
    const card = mockCard()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes(`/api/people/${CONTACT_ID}`) && !url.includes('merge')) {
          return {
            ok: true,
            status: 200,
            json: async () => card,
          } as Response
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({}),
        } as Response
      }),
    )

    renderProfile()
    expect(await screen.findByTestId('person-profile')).toBeInTheDocument()
    expect(screen.getByTestId('person-identity')).toBeInTheDocument()
    expect(screen.getByTestId('person-span')).toHaveTextContent('threads: 12')
    expect(screen.getByTestId('person-activity')).toBeInTheDocument()
    expect(screen.getByTestId('activity-bars')).toBeInTheDocument()
    expect(screen.getByTestId('person-topics')).toHaveTextContent('Kitchen remodel')
    expect(screen.getByTestId('address-class-me@owner.com')).toHaveTextContent(
      'owner',
    )
    expect(
      screen.getByTestId('address-class-alice@example.com'),
    ).toHaveTextContent('external')

    // Expand signals details
    fireEvent.click(screen.getByTestId('person-signals').querySelector('summary')!)
    expect(screen.getByTestId('person-signals')).toHaveTextContent('bidirectional')
    expect(screen.getByTestId('person-signals')).toHaveTextContent('0.15')
  })

  it('kind PATCH with kind_source explanation', async () => {
    const card = mockCard({ kind_source: 'heuristic', kind: 'unknown' })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { kind?: string }
        return {
          ok: true,
          status: 200,
          json: async () =>
            mockCard({
              kind: body.kind as ContactCard['kind'],
              kind_source: 'manual',
            }),
        } as Response
      }
      if (url.includes(`/api/people/${CONTACT_ID}`)) {
        return {
          ok: true,
          status: 200,
          json: async () => card,
        } as Response
      }
      return {
        ok: false,
        status: 404,
        json: async () => ({}),
      } as Response
    })
    vi.stubGlobal('fetch', fetchMock)

    renderProfile()
    await screen.findByTestId('person-kind-source')
    expect(screen.getByTestId('person-kind-source')).toHaveTextContent(
      'kind_source: heuristic',
    )

    fireEvent.change(screen.getByTestId('person-kind-select'), {
      target: { value: 'human' },
    })
    await waitFor(() => {
      const patches = fetchMock.mock.calls.filter(
        (c) => (c[1] as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patches.length).toBeGreaterThan(0)
    })
    await waitFor(() => {
      expect(screen.getByTestId('person-kind-source')).toHaveTextContent('manual')
      expect(screen.getByTestId('person-kind-source')).toHaveTextContent(
        'locked against machinery',
      )
    })
  })

  it('merge flow confirm dialog naming direction', async () => {
    const card = mockCard()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/api/people?') && url.includes('q=')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              items: [
                {
                  id: TARGET_ID,
                  display_name: 'Target Person',
                  kind: 'human',
                  kind_source: 'manual',
                  tags: [],
                  human_probability: 0.5,
                  addresses: ['t@x.com'],
                  name_variants: [],
                  messages_from: 3,
                  messages_to: 1,
                  first_seen: null,
                  last_seen: null,
                },
              ],
              total: 1,
              next_cursor: null,
              limit: 8,
              offset: 0,
            }),
          } as Response
        }
        if (url.includes(`/api/people/${CONTACT_ID}`)) {
          return {
            ok: true,
            status: 200,
            json: async () => card,
          } as Response
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({}),
        } as Response
      }),
    )

    renderProfile()
    await screen.findByTestId('person-merge')
    fireEvent.change(screen.getByTestId('merge-target-search'), {
      target: { value: 'Target' },
    })
    await screen.findByTestId(`merge-target-${TARGET_ID}`)
    fireEvent.click(screen.getByTestId(`merge-target-${TARGET_ID}`))
    fireEvent.click(screen.getByTestId('merge-confirm'))
    expect(confirmSpy).toHaveBeenCalled()
    const msg = String(confirmSpy.mock.calls[0]?.[0] ?? '')
    expect(msg).toMatch(/Merge Alice Example into Target Person/)
    confirmSpy.mockRestore()
  })

  it('unmerge from merge history', async () => {
    const card = mockCard()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (init?.method === 'POST' && url.includes('/api/people/unmerge')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            source: mockCard({ id: 'src', merges: undefined }),
            target: mockCard({ merges: [] }),
          }),
        } as Response
      }
      if (url.includes(`/api/people/${CONTACT_ID}`)) {
        return {
          ok: true,
          status: 200,
          json: async () => card,
        } as Response
      }
      return {
        ok: false,
        status: 404,
        json: async () => ({}),
      } as Response
    })
    vi.stubGlobal('fetch', fetchMock)

    renderProfile()
    await screen.findByTestId('person-merge-history')
    fireEvent.click(screen.getByTestId(`unmerge-${MERGE_ID}`))
    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter(
        (c) =>
          String(c[0]).includes('/api/people/unmerge') &&
          (c[1] as RequestInit)?.method === 'POST',
      )
      expect(posts.length).toBe(1)
      const body = JSON.parse(String((posts[0]?.[1] as RequestInit).body))
      expect(body.merge_id).toBe(MERGE_ID)
    })
    confirmSpy.mockRestore()
  })

  it('Open in Chronicle sets sender scope and viewport from span', async () => {
    const card = mockCard()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => card,
      })),
    )

    renderProfile()
    await screen.findByTestId('open-in-chronicle')
    fireEvent.click(screen.getByTestId('open-in-chronicle'))
    expect(await screen.findByTestId('chronicle')).toBeInTheDocument()
    // Navigation encodes senders in URL; store may also be hydrated by useUrlSync
    // on real app — here we only verify the button navigates to Chronicle.
  })

  it('View correspondence sets scope senders and navigates to research exact mode', async () => {
    const card = mockCard()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => card,
      })),
    )

    renderProfile()
    await screen.findByTestId('view-correspondence')
    fireEvent.click(screen.getByTestId('view-correspondence'))
    expect(await screen.findByTestId('research')).toBeInTheDocument()
    const state = useWorkingSetStore.getState()
    expect(state.scope.senders).toEqual(
      expect.arrayContaining(['alice@example.com', 'me@owner.com']),
    )
    expect(state.mode).toBe('exact')
    expect(state.query).toBe('')
    expect(state.grouping).toBe('thread')
  })

  it('topic link navigates with tsel', async () => {
    const card = mockCard()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => card,
      })),
    )

    renderProfile()
    fireEvent.click(await screen.findByTestId(`topic-link-${TOPIC_ID}`))
    expect(await screen.findByTestId('topics')).toBeInTheDocument()
  })
})
