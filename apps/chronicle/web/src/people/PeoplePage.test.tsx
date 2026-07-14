import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ContactSummary,
  MergeCandidatesResponse,
  PeopleListResponse,
} from '../api/types'
import { PeoplePage } from './PeoplePage'

function contact(overrides: Partial<ContactSummary> = {}): ContactSummary {
  return {
    id: 'c1',
    display_name: 'Alice Example',
    kind: 'human',
    kind_source: 'manual',
    tags: [],
    human_probability: 0.87,
    addresses: ['alice@example.com'],
    name_variants: ['Alice'],
    messages_from: 10,
    messages_to: 2,
    first_seen: '2015-01-01T00:00:00Z',
    last_seen: '2018-06-01T00:00:00Z',
    ...overrides,
  }
}

function listResponse(
  items: ContactSummary[],
  total: number | null = items.length,
): PeopleListResponse {
  return {
    items,
    total,
    next_cursor: null,
    limit: 50,
    offset: 0,
  }
}

function renderPeople(initial = '/people') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/people" element={<PeoplePage />} />
          <Route path="/people/:id" element={<div>Profile</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('PeoplePage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/api/people/merge-candidates')) {
          const body: MergeCandidatesResponse = {
            items: [
              {
                norm_name: 'alice',
                a: {
                  display_name: 'Alice A',
                  primary_address: 'a@x.com',
                  msg_count: 5,
                  contact_id: 'ca',
                },
                b: {
                  display_name: 'Alice B',
                  primary_address: 'b@x.com',
                  msg_count: 3,
                  contact_id: 'cb',
                },
              },
            ],
          }
          return {
            ok: true,
            status: 200,
            json: async () => body,
          } as Response
        }
        if (url.includes('/api/people')) {
          const u = new URL(url, 'http://local')
          const q = u.searchParams.get('q')
          const kind = u.searchParams.get('kind')
          const review = u.searchParams.get('needs_review')
          let items = [
            contact({ id: 'c1', kind: 'human', display_name: 'Alice' }),
            contact({
              id: 'c2',
              kind: 'organization',
              display_name: 'Acme Corp',
              human_probability: 0.2,
            }),
            contact({
              id: 'c3',
              kind: 'unknown',
              display_name: 'Bob Unknown',
              human_probability: 0.5,
            }),
          ]
          if (q) items = items.filter((i) => i.display_name?.includes(q))
          if (kind) items = items.filter((i) => i.kind === kind)
          if (review === 'true') items = items.filter((i) => i.kind === 'unknown')
          return {
            ok: true,
            status: 200,
            json: async () => listResponse(items),
          } as Response
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({}),
        } as Response
      }),
    )
  })

  it('renders index rows with kind badge, probability, volume, span', async () => {
    renderPeople()
    expect(await screen.findByTestId('people-page')).toBeInTheDocument()
    expect(await screen.findByTestId('people-row-c1')).toBeInTheDocument()
    expect(screen.getByTestId('kind-badge-c1')).toHaveTextContent('human')
    expect(screen.getByTestId('people-row-c1')).toHaveTextContent('0.87')
    expect(screen.getByTestId('people-row-c1')).toHaveTextContent('12')
    expect(screen.getByTestId('people-row-c1')).toHaveTextContent('2015-01-01')
  })

  it('search updates pq and refilters', async () => {
    renderPeople()
    await screen.findByTestId('people-row-c1')
    fireEvent.change(screen.getByTestId('people-search'), {
      target: { value: 'Acme' },
    })
    fireEvent.click(screen.getByTestId('people-search-submit'))
    await waitFor(() => {
      expect(screen.getByTestId('people-row-c2')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('people-row-c1')).not.toBeInTheDocument()
  })

  it('kind filter chips filter list; organizations tab preset', async () => {
    renderPeople('/people?kind=organization')
    expect(await screen.findByTestId('people-row-c2')).toBeInTheDocument()
    expect(screen.queryByTestId('people-row-c1')).not.toBeInTheDocument()
    expect(screen.getByTestId('people-kind-organization')).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('needs-review toggle filters to unknown queue', async () => {
    renderPeople()
    await screen.findByTestId('people-row-c1')
    fireEvent.click(screen.getByTestId('people-needs-review'))
    await waitFor(() => {
      expect(screen.getByTestId('people-row-c3')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('people-row-c1')).not.toBeInTheDocument()
  })

  it('merge candidates rail with confirm dialog naming direction', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPeople()
    expect(await screen.findByTestId('merge-pair-ca')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('merge-into-b-ca'))
    expect(confirmSpy).toHaveBeenCalled()
    const msg = String(confirmSpy.mock.calls[0]?.[0] ?? '')
    expect(msg).toMatch(/Merge Alice A into Alice B/)
    confirmSpy.mockRestore()
  })

  it('row click navigates to profile', async () => {
    renderPeople()
    fireEvent.click(await screen.findByTestId('people-row-c1'))
    expect(await screen.findByText('Profile')).toBeInTheDocument()
  })
})
