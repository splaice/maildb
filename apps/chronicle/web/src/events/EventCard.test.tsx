import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { EventCard } from './EventCard'

const eventPayload = {
  id: 'evt-1',
  title: 'Metal roof decision',
  time_start: '2015-06-15T00:00:00Z',
  time_end: null,
  time_precision: 'month',
  origin: 'analyst',
  event_type: 'decision',
  status: 'unreviewed',
  evidence_strength: 'high',
  current_version: 1,
  summary: 'Chose metal roofing.',
  version: {
    version: 1,
    author: 'analyst',
    title: 'Metal roof decision',
    summary: 'Chose metal roofing.',
    derivation: {},
  },
  claims: [
    {
      id: 'c1',
      position: 0,
      text: 'Metal roof selected',
      status: 'direct',
      citations: [
        {
          source_id: 'msg_42',
          source_type: 'message',
          subject: 'Roof quote',
          sender: 'Alice',
          date: '2015-06-10T12:00:00Z',
        },
      ],
    },
  ],
}

const automaticEventPayload = {
  ...eventPayload,
  origin: 'automatic',
  evidence_strength: 'medium',
  derivation: {
    generated_at: '2026-07-13T12:00:00Z',
    process_version: 'event-v1',
    model_route: 'local-llama',
    scope_fingerprint: 'qs_abc123def456',
  },
  version: {
    ...eventPayload.version,
    author: 'automatic',
    derivation: {
      generated_at: '2026-07-13T12:00:00Z',
      process_version: 'event-v1',
      model_route: 'local-llama',
      scope_fingerprint: 'qs_abc123def456',
    },
  },
}

function renderCard(
  eventId = 'evt-1',
  initialEntries: string[] = ['/?vf=2015-01-01T00:00:00Z&vt=2016-01-01T00:00:00Z&sel=e:evt-1'],
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="/" element={<EventCard eventId={eventId} />} />
          <Route
            path="/events/:id/reconstruction"
            element={<div data-testid="recon-stub">recon</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('EventCard', () => {
  beforeEach(() => {
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('renders origin, status, precision text, and claims', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => eventPayload,
      }),
    )

    renderCard()
    await waitFor(() => {
      expect(screen.getByTestId('event-card')).toBeInTheDocument()
    })
    expect(screen.getByTestId('event-origin-badge')).toHaveTextContent(/Analyst/)
    expect(screen.getByTestId('event-status-badge')).toHaveTextContent(/unreviewed/)
    expect(screen.getByTestId('event-time')).toHaveTextContent(/month precision/)
    expect(screen.getByTestId('event-time')).toHaveTextContent(/June 2015/)
    expect(screen.getByTestId('event-evidence-strength')).toHaveTextContent(/high/)
    expect(screen.getByTestId('event-summary')).toHaveTextContent('Chose metal roofing.')
    expect(screen.getByTestId('event-claim')).toHaveTextContent('Metal roof selected')
    expect(screen.getByTestId('event-claim')).toHaveTextContent(/1 citation/)
    expect(screen.getByTestId('event-reconstruction')).toBeEnabled()
  })

  it('Open reconstruction navigates with chronicle search params', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => eventPayload,
      }),
    )
    renderCard()
    await waitFor(() => expect(screen.getByTestId('event-card')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('event-reconstruction'))
    await waitFor(() => {
      expect(screen.getByTestId('recon-stub')).toBeInTheDocument()
    })
  })

  it('Compare before/after sets ±90d ranges around event start', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => eventPayload,
      }),
    )
    renderCard()
    await waitFor(() => expect(screen.getByTestId('event-card')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('event-compare-before-after'))
    const cmp = useWorkingSetStore.getState().compare
    expect(cmp).not.toBeNull()
    const start = Date.parse(eventPayload.time_start)
    const delta = 90 * 24 * 60 * 60 * 1000
    expect(cmp!.a.fromMs).toBe(start - delta)
    expect(cmp!.a.toMs).toBe(start)
    expect(cmp!.b.fromMs).toBe(start)
    expect(cmp!.b.toMs).toBe(start + delta)
    expect(useWorkingSetStore.getState().historyIntent).toBe('analytical')
  })

  it('confirm and dismiss patch with optimistic version; 409 shows banner', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url)
      if (u.includes('/api/events/evt-1') && (!init || init.method === 'GET' || !init.method)) {
        return {
          ok: true,
          status: 200,
          json: async () => eventPayload,
        } as Response
      }
      if (u.includes('/api/events/evt-1') && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { current_version: number; status?: string }
        if (body.current_version !== 1) {
          return {
            ok: false,
            status: 409,
            json: async () => ({ detail: { error: 'version_conflict' } }),
          } as Response
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({ ...eventPayload, status: body.status ?? eventPayload.status }),
        } as Response
      }
      throw new Error(`unexpected: ${u} ${init?.method}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderCard()
    await waitFor(() => expect(screen.getByTestId('event-card')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('event-confirm'))
    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(
        (c) => (c[1] as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patchCalls.length).toBeGreaterThanOrEqual(1)
      const body = JSON.parse(String((patchCalls[0]![1] as RequestInit).body))
      expect(body).toEqual({ current_version: 1, status: 'confirmed' })
    })

    // Force 409 path
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/api/events/evt-1') && init?.method === 'PATCH') {
        return {
          ok: false,
          status: 409,
          json: async () => ({ detail: { error: 'version_conflict' } }),
        } as Response
      }
      return {
        ok: true,
        status: 200,
        json: async () => eventPayload,
      } as Response
    })

    fireEvent.click(screen.getByTestId('event-dismiss'))
    await waitFor(() => {
      expect(screen.getByTestId('event-conflict-banner')).toBeInTheDocument()
    })
  })

  it('citation click selects source in inspector', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => eventPayload,
      }),
    )
    renderCard()
    await waitFor(() => expect(screen.getByTestId('event-citation')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('event-citation'))
    expect(useWorkingSetStore.getState().selection).toEqual({
      kind: 'message',
      sid: 'msg_42',
    })
  })

  it('automatic origin shows derivation line', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => automaticEventPayload,
      }),
    )
    renderCard()
    await waitFor(() => expect(screen.getByTestId('event-card')).toBeInTheDocument())
    expect(screen.getByTestId('event-origin-badge')).toHaveTextContent(/Automatic/)
    expect(screen.getByTestId('event-derivation')).toHaveTextContent(/Generated 2026-07-13/)
    expect(screen.getByTestId('event-derivation')).toHaveTextContent(/event-v1/)
    expect(screen.getByTestId('event-derivation')).toHaveTextContent(/model route local-llama/)
  })

  it('Enter key opens reconstruction route', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => eventPayload,
      }),
    )
    renderCard()
    await waitFor(() => expect(screen.getByTestId('event-card')).toBeInTheDocument())
    fireEvent.keyDown(window, { key: 'Enter' })
    await waitFor(() => {
      expect(screen.getByTestId('recon-stub')).toBeInTheDocument()
    })
  })

  it('P key opens pin-to-workspace menu', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
        const u = String(url)
        const method = (init?.method || 'GET').toUpperCase()
        if (u.includes('/api/events/evt-1')) {
          return {
            ok: true,
            status: 200,
            json: async () => eventPayload,
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
    renderCard()
    await waitFor(() => expect(screen.getByTestId('event-card')).toBeInTheDocument())
    expect(screen.getByTestId('pin-to-workspace-btn')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'p' })
    expect(await screen.findByTestId('pin-workspace-menu')).toBeInTheDocument()
  })
})
