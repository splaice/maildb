import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DismissedEventsList } from './DismissedEventsList'

const viewport = {
  fromMs: Date.parse('2015-01-01T00:00:00Z'),
  toMs: Date.parse('2016-01-01T00:00:00Z'),
}

function renderList() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <DismissedEventsList scope={{}} viewport={viewport} />
    </QueryClientProvider>,
  )
}

describe('DismissedEventsList', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lists dismissed events and restores via PATCH', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url)
      if (u.includes('/api/events/list') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { include_dismissed?: boolean }
        expect(body.include_dismissed).toBe(true)
        return {
          ok: true,
          status: 200,
          json: async () => ({
            items: [
              {
                id: 'evt-d1',
                title: 'Dismissed meeting',
                time_start: '2015-06-01T00:00:00Z',
                time_end: null,
                time_precision: 'day',
                origin: 'automatic',
                event_type: 'meeting',
                status: 'dismissed',
                evidence_strength: null,
                current_version: 1,
              },
              {
                id: 'evt-active',
                title: 'Active',
                time_start: '2015-06-02T00:00:00Z',
                time_end: null,
                time_precision: 'day',
                origin: 'analyst',
                event_type: 'meeting',
                status: 'confirmed',
                evidence_strength: null,
                current_version: 1,
              },
            ],
            next_cursor: null,
          }),
        } as Response
      }
      if (u.includes('/api/events/evt-d1') && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as {
          current_version: number
          status: string
        }
        expect(body).toEqual({ current_version: 1, status: 'unreviewed' })
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: 'evt-d1',
            title: 'Dismissed meeting',
            status: 'unreviewed',
            current_version: 1,
            time_start: '2015-06-01T00:00:00Z',
            time_end: null,
            time_precision: 'day',
            origin: 'automatic',
            event_type: 'meeting',
            evidence_strength: null,
          }),
        } as Response
      }
      throw new Error(`unexpected ${u} ${init?.method}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderList()
    // Collapsed by default — no fetch until expanded
    expect(fetchMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('dismissed-events-toggle'))
    await waitFor(() => {
      expect(screen.getByTestId('dismissed-events-list')).toHaveTextContent(
        'Dismissed events (1)',
      )
    })
    expect(screen.getByTestId('dismissed-event-row')).toHaveTextContent(
      'Dismissed meeting',
    )
    expect(screen.queryByText('Active')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('dismissed-restore'))
    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(
        (c) => (c[1] as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patchCalls.length).toBeGreaterThanOrEqual(1)
    })
  })

  it('shows empty state when none dismissed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ items: [], next_cursor: null }),
      }),
    )
    renderList()
    fireEvent.click(screen.getByTestId('dismissed-events-toggle'))
    await waitFor(() => {
      expect(screen.getByTestId('dismissed-empty')).toBeInTheDocument()
    })
  })
})
