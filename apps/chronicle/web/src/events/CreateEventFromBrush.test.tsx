import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CreateEventFromBrush } from './CreateEventFromBrush'

const brush = {
  fromMs: Date.UTC(2015, 5, 1),
  toMs: Date.UTC(2015, 5, 15),
}

function renderForm() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <CreateEventFromBrush brush={brush} />
    </QueryClientProvider>,
  )
}

describe('CreateEventFromBrush', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts correct payload from brush range', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({
        id: 'new-evt',
        title: 'Trip',
        current_version: 1,
        origin: 'analyst',
        status: 'confirmed',
        time_start: '2015-06-01T00:00:00Z',
        time_end: '2015-06-15T00:00:00Z',
        time_precision: 'day',
        event_type: 'travel',
        evidence_strength: null,
        claims: [],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    renderForm()
    fireEvent.click(screen.getByTestId('create-event-from-selection'))
    fireEvent.change(screen.getByTestId('create-event-title'), {
      target: { value: 'Trip' },
    })
    fireEvent.change(screen.getByTestId('create-event-type'), {
      target: { value: 'travel' },
    })
    fireEvent.change(screen.getByTestId('create-event-precision'), {
      target: { value: 'day' },
    })
    fireEvent.click(screen.getByTestId('create-event-submit'))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toContain('/api/events')
    expect((init as RequestInit).method).toBe('POST')
    const body = JSON.parse(String((init as RequestInit).body))
    expect(body.title).toBe('Trip')
    expect(body.event_type).toBe('travel')
    expect(body.time_precision).toBe('day')
    expect(body.time_start).toBe('2015-06-01T00:00:00Z')
    expect(body.time_end).toBe('2015-06-15T00:00:00Z')
  })
})
