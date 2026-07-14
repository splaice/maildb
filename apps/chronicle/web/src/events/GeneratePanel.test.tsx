import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  formatGenerateResultLine,
  GeneratePanel,
} from './GeneratePanel'

const viewport = {
  fromMs: Date.UTC(2015, 0, 1),
  toMs: Date.UTC(2016, 0, 1),
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries')
  const result = render(
    <QueryClientProvider client={client}>
      <GeneratePanel scope={{}} viewport={viewport} />
    </QueryClientProvider>,
  )
  return { ...result, client, invalidateSpy }
}

describe('formatGenerateResultLine', () => {
  it('matches the task framing examples', () => {
    expect(
      formatGenerateResultLine({
        bursts: 7,
        created: 4,
        superseded: 0,
        suggested: 1,
        skipped_unavailable: false,
      }),
    ).toBe('4 created · 1 suggested update · 2 bursts empty')
  })
})

describe('GeneratePanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts scope+viewport and shows result line; refreshes lane', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        bursts: 3,
        created: 2,
        superseded: 0,
        suggested: 1,
        skipped_unavailable: false,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const { invalidateSpy } = renderPanel()

    expect(
      screen.getByText('Inferred events are hypotheses — review before trusting'),
    ).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('generate-events-button'))

    await waitFor(() => {
      expect(screen.getByTestId('generate-events-result')).toBeInTheDocument()
    })
    expect(screen.getByTestId('generate-events-result').textContent).toContain(
      '2 created',
    )
    expect(screen.getByTestId('generate-events-result').textContent).toContain(
      '1 suggested update',
    )

    expect(fetchMock).toHaveBeenCalled()
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toContain('/api/events/generate')
    expect((init as RequestInit).method).toBe('POST')
    const body = JSON.parse(String((init as RequestInit).body))
    expect(body.viewport.from).toBe('2015-01-01T00:00:00Z')
    expect(body.viewport.to).toBe('2016-01-01T00:00:00Z')

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['chronicle', 'buckets'] }),
    )
  })

  it('shows muted unavailable text when model is down', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ available: false }),
      }),
    )

    renderPanel()
    fireEvent.click(screen.getByTestId('generate-events-button'))

    await waitFor(() => {
      expect(screen.getByTestId('generate-events-unavailable')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('generate-events-result')).not.toBeInTheDocument()
  })
})
