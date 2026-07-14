import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore } from '../workingset/store'
import { ReconstructionView } from './ReconstructionView'

function ChronicleHomeStub() {
  const loc = useLocation()
  return (
    <div data-testid="chronicle-home">
      chronicle{loc.search}
    </div>
  )
}

const baseEvent = {
  id: 'evt-1',
  title: 'Roof decision',
  time_start: '2015-06-15T00:00:00Z',
  time_end: null,
  time_precision: 'day',
  origin: 'automatic',
  event_type: 'decision',
  status: 'unreviewed',
  evidence_strength: 'medium',
  current_version: 1,
  summary: 'Chose metal roof.',
  has_suggestions: true,
  conflicts: [{ claim_position: 2, statuses: ['conflicting'] }],
  derivation: {
    generated_at: '2026-07-13T12:00:00Z',
    process_version: 'event-v1',
    model_route: 'local-llama',
    scope_fingerprint: 'qs_abc123def456',
  },
  version: {
    version: 1,
    author: 'automatic',
    title: 'Roof decision',
    summary: 'Chose metal roof.',
    derivation: {
      generated_at: '2026-07-13T12:00:00Z',
      process_version: 'event-v1',
      model_route: 'local-llama',
      scope_fingerprint: 'qs_abc123def456',
    },
  },
  claims: [
    {
      id: 'c-direct',
      position: 0,
      text: 'Metal was selected',
      status: 'direct',
      citations: [
        {
          source_id: 'msg_1',
          source_type: 'message',
          subject: 'Quote A',
          sender: 'Alice',
          date: '2015-06-01T12:00:00Z',
          excerpt: 'we go with metal',
          excerpt_hash: 'hash-live',
          location: { char_start: 0, char_end: 16 },
        },
      ],
    },
    {
      id: 'c-supported',
      position: 1,
      text: 'Budget approved',
      status: 'supported',
      citations: [
        {
          source_id: 'msg_2',
          source_type: 'message',
          subject: 'Budget',
          sender: 'Bob',
          date: '2015-06-05T12:00:00Z',
          excerpt: 'budget ok',
          excerpt_hash: 'hash-ok',
          location: { char_start: 0, char_end: 9 },
        },
      ],
    },
    {
      id: 'c-conflict',
      position: 2,
      text: 'Or slate?',
      status: 'conflicting',
      citations: [
        {
          source_id: 'msg_3',
          source_type: 'message',
          subject: 'Counter',
          sender: 'Carol',
          date: '2015-06-10T12:00:00Z',
          excerpt: 'prefer slate',
          excerpt_hash: 'hash-c',
          location: { char_start: 0, char_end: 12 },
        },
      ],
    },
    {
      id: 'c-unresolved',
      position: 3,
      text: 'Warranty unclear',
      status: 'unresolved',
      citations: [],
    },
  ],
}

const versionsPayload = {
  event_id: 'evt-1',
  current_version: 1,
  versions: [
    {
      version: 1,
      author: 'automatic',
      title: 'Roof decision',
      summary: 'Chose metal roof.',
      derivation: {},
      created_at: '2026-07-01T00:00:00Z',
      claims: baseEvent.claims,
      is_suggestion: false,
    },
    {
      version: 2,
      author: 'automatic',
      title: 'Updated roof decision',
      summary: 'Metal confirmed after review.',
      derivation: { process_version: 'event-v1' },
      created_at: '2026-07-13T00:00:00Z',
      claims: [],
      is_suggestion: true,
    },
  ],
}

function renderRecon(
  initialEntry = '/events/evt-1/reconstruction?vf=2015-01-01T00:00:00Z&vt=2016-01-01T00:00:00Z&sel=e:evt-1',
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/" element={<ChronicleHomeStub />} />
          <Route
            path="/events/:id/reconstruction"
            element={<ReconstructionView />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function mockFetch(
  handlers: (url: string, init?: RequestInit) => Response | Promise<Response>,
) {
  const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    return handlers(url, init)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('ReconstructionView', () => {
  beforeEach(() => {
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('renders all four claim statuses with text+symbol coding', async () => {
    mockFetch((url) => {
      if (url.includes('/api/events/evt-1') && !url.includes('/versions')) {
        return {
          ok: true,
          status: 200,
          json: async () => baseEvent,
        } as Response
      }
      throw new Error(url)
    })

    renderRecon()
    await waitFor(() => expect(screen.getByTestId('reconstruction-view')).toBeInTheDocument())

    const chips = screen.getAllByTestId('claim-status-chip')
    expect(chips).toHaveLength(4)
    const texts = chips.map((c) => c.textContent)
    expect(texts.some((t) => t?.includes('✓') && t.includes('direct'))).toBe(true)
    expect(texts.some((t) => t?.includes('~') && t.includes('supported'))).toBe(true)
    expect(texts.some((t) => t?.includes('✕') && t.includes('conflicting'))).toBe(true)
    expect(texts.some((t) => t?.includes('?') && t.includes('unresolved'))).toBe(true)
  })

  it('claim selection highlights/dims citations', async () => {
    mockFetch((url) => {
      if (url.includes('/api/events/evt-1') && !url.includes('/versions')) {
        return {
          ok: true,
          status: 200,
          json: async () => baseEvent,
        } as Response
      }
      throw new Error(url)
    })

    renderRecon()
    await waitFor(() => expect(screen.getByTestId('claim-matrix')).toBeInTheDocument())

    const rows = screen.getAllByTestId('claim-matrix-row')
    fireEvent.click(rows[0]!) // direct claim → msg_1

    await waitFor(() => {
      const cits = screen.getAllByTestId('recon-citation')
      const msg1 = cits.find((el) => el.getAttribute('data-source-id') === 'msg_1')
      const msg2 = cits.find((el) => el.getAttribute('data-source-id') === 'msg_2')
      expect(msg1?.getAttribute('data-highlighted')).toBe('true')
      expect(msg2?.getAttribute('data-dimmed')).toBe('true')
    })
  })

  it('conflicting claim shows both chains under Supporting/Conflicting headings', async () => {
    mockFetch((url) => {
      if (url.includes('/api/events/evt-1') && !url.includes('/versions')) {
        return {
          ok: true,
          status: 200,
          json: async () => baseEvent,
        } as Response
      }
      throw new Error(url)
    })

    renderRecon()
    await waitFor(() => expect(screen.getByTestId('claim-matrix')).toBeInTheDocument())

    const rows = screen.getAllByTestId('claim-matrix-row')
    // c-conflict is third claim (index 2)
    fireEvent.click(rows[2]!)

    await waitFor(() => {
      expect(screen.getByTestId('conflict-chains')).toBeInTheDocument()
      expect(screen.getByTestId('supporting-heading')).toHaveTextContent('Supporting')
      expect(screen.getByTestId('conflicting-heading')).toHaveTextContent('Conflicting')
    })
  })

  it('stale marker on hash mismatch after expand (mock context re-fetch)', async () => {
    mockFetch((url) => {
      if (url.includes('/context')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 'msg_1',
            start: 0,
            end: 16,
            excerpt: 'changed text!!!!',
            context_before: '',
            context_after: '',
            sha256: 'hash-different',
            window: 400,
          }),
        } as Response
      }
      if (url.includes('/api/events/evt-1') && !url.includes('/versions')) {
        return {
          ok: true,
          status: 200,
          json: async () => baseEvent,
        } as Response
      }
      throw new Error(url)
    })

    renderRecon()
    await waitFor(() => expect(screen.getAllByTestId('recon-citation').length).toBeGreaterThan(0))

    const first = screen.getAllByTestId('recon-citation')[0]!
    fireEvent.click(within(first).getByTestId('recon-citation-toggle'))

    await waitFor(() => {
      expect(screen.getByTestId('recon-citation-stale')).toHaveTextContent('stale?')
    })
  })

  it('version panel adopt flow shows banner', async () => {
    let current = { ...baseEvent, has_suggestions: true }
    mockFetch((url, init) => {
      if (url.includes('/versions') && (!init || !init.method || init.method === 'GET')) {
        return {
          ok: true,
          status: 200,
          json: async () => versionsPayload,
        } as Response
      }
      if (url.includes('/adopt/2') && init?.method === 'POST') {
        current = {
          ...baseEvent,
          current_version: 2,
          title: 'Updated roof decision',
          status: 'edited',
          has_suggestions: false,
          summary: 'Metal confirmed after review.',
        }
        return {
          ok: true,
          status: 200,
          json: async () => current,
        } as Response
      }
      if (url.includes('/api/events/evt-1')) {
        return {
          ok: true,
          status: 200,
          json: async () => current,
        } as Response
      }
      throw new Error(`${url} ${init?.method}`)
    })

    renderRecon()
    await waitFor(() => expect(screen.getByTestId('recon-suggestion-banner')).toBeInTheDocument())
    expect(screen.getByTestId('recon-suggestion-banner')).toHaveTextContent(
      /An updated automatic version exists/,
    )

    fireEvent.click(screen.getByTestId('recon-version-toggle'))
    await waitFor(() => expect(screen.getByTestId('version-history-panel')).toBeInTheDocument())

    const items = await screen.findAllByTestId('version-list-item')
    const suggestion = items.find((el) => el.getAttribute('data-suggestion') === 'true')
    expect(suggestion).toBeTruthy()
    fireEvent.click(suggestion!)

    await waitFor(() => expect(screen.getByTestId('version-adopt')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('version-adopt'))

    await waitFor(() => {
      expect(screen.getByTestId('recon-adopted-banner')).toBeInTheDocument()
    })
    expect(screen.getByTestId('recon-adopted-banner')).toHaveTextContent(/Adopted version 2/)
  })

  it('Back restores Chronicle state (URL contract)', async () => {
    mockFetch((url) => {
      if (url.includes('/api/events/evt-1') && !url.includes('/versions')) {
        return {
          ok: true,
          status: 200,
          json: async () => baseEvent,
        } as Response
      }
      throw new Error(url)
    })

    const search =
      '?vf=2015-01-01T00:00:00Z&vt=2016-01-01T00:00:00Z&sel=e:evt-1'
    renderRecon(`/events/evt-1/reconstruction${search}`)

    await waitFor(() => expect(screen.getByTestId('reconstruction-view')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('reconstruction-back'))

    await waitFor(() => {
      expect(screen.getByTestId('chronicle-home')).toBeInTheDocument()
    })
    expect(screen.getByTestId('chronicle-home').textContent).toContain(
      'vf=2015-01-01T00:00:00Z',
    )
    expect(screen.getByTestId('chronicle-home').textContent).toContain(
      'vt=2016-01-01T00:00:00Z',
    )
    expect(screen.getByTestId('chronicle-home').textContent).toContain(
      'sel=e:evt-1',
    )
  })

  it('derivation line only for automatic origin', async () => {
    mockFetch((url) => {
      if (url.includes('/api/events/evt-1') && !url.includes('/versions')) {
        return {
          ok: true,
          status: 200,
          json: async () => baseEvent,
        } as Response
      }
      throw new Error(url)
    })

    renderRecon()
    await waitFor(() => expect(screen.getByTestId('recon-derivation')).toBeInTheDocument())
    expect(screen.getByTestId('recon-derivation')).toHaveTextContent(/Generated 2026-07-13/)
    expect(screen.getByTestId('recon-derivation')).toHaveTextContent(/event-v1/)
    expect(screen.getByTestId('recon-derivation')).toHaveTextContent(/model route local-llama/)
    expect(screen.getByTestId('recon-derivation')).toHaveTextContent(/scope qs_/)
  })

  it('no derivation line for analyst origin', async () => {
    mockFetch((url) => {
      if (url.includes('/api/events/evt-1') && !url.includes('/versions')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ...baseEvent,
            origin: 'analyst',
            has_suggestions: false,
          }),
        } as Response
      }
      throw new Error(url)
    })

    renderRecon()
    await waitFor(() => expect(screen.getByTestId('reconstruction-view')).toBeInTheDocument())
    expect(screen.queryByTestId('recon-derivation')).not.toBeInTheDocument()
  })
})
