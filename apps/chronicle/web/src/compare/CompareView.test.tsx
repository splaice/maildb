import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore } from '../workingset/store'
import { CompareView } from './CompareView'

const a = {
  fromMs: Date.UTC(2015, 0, 1),
  toMs: Date.UTC(2015, 6, 1),
}
const b = {
  fromMs: Date.UTC(2016, 0, 1),
  toMs: Date.UTC(2016, 6, 1),
}

function mockCompare(aligned = true) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      unit: 'month',
      aligned,
      scope_fingerprint: 'qs_cmp',
      a: {
        viewport: {
          from: '2015-01-01T00:00:00.000Z',
          to: '2015-07-01T00:00:00.000Z',
        },
        lanes: {
          messages: [
            { bucket: '2015-01-01T00:00:00.000Z', count: 100 },
            { bucket: '2015-02-01T00:00:00.000Z', count: 50 },
          ],
          attachments: [{ bucket: '2015-01-01T00:00:00.000Z', count: 10 }],
        },
      },
      b: {
        viewport: {
          from: '2016-01-01T00:00:00.000Z',
          to: '2016-07-01T00:00:00.000Z',
        },
        lanes: {
          messages: [
            { bucket: '2016-01-01T00:00:00.000Z', count: 200 },
            { bucket: '2016-02-01T00:00:00.000Z', count: 100 },
          ],
          attachments: [{ bucket: '2016-01-01T00:00:00.000Z', count: 5 }],
        },
      },
      totals: {
        a: { messages: 150, attachments: 10 },
        b: { messages: 300, attachments: 5 },
      },
    }),
  } as Response
}

function renderCompare(
  props: Partial<Parameters<typeof CompareView>[0]> = {},
  aligned = true,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const onExit = vi.fn()
  const onUpdateSide = vi.fn()
  const fetchMock = vi.fn().mockImplementation(async (url: string) => {
    if (String(url).includes('/api/chronicle/compare')) return mockCompare(aligned)
    throw new Error(`unexpected: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  const result = render(
    <QueryClientProvider client={client}>
      <CompareView
        a={a}
        b={b}
        scope={{}}
        lanes={['messages', 'attachments']}
        onExit={onExit}
        onUpdateSide={onUpdateSide}
        {...props}
      />
    </QueryClientProvider>,
  )
  return { ...result, onExit, onUpdateSide, fetchMock, client }
}

describe('CompareView', () => {
  beforeEach(() => {
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('renders aligned branch when server reports aligned', async () => {
    renderCompare({}, true)
    await waitFor(() => {
      expect(screen.getByTestId('compare-aligned')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('compare-multiples')).not.toBeInTheDocument()
  })

  it('renders small-multiples branch when unaligned', async () => {
    renderCompare({}, false)
    await waitFor(() => {
      expect(screen.getByTestId('compare-multiples')).toBeInTheDocument()
    })
  })

  it('shows totals delta B vs A', async () => {
    renderCompare()
    await waitFor(() => {
      expect(screen.getByTestId('compare-delta')).toHaveTextContent(/B vs A/)
    })
    // 300 vs 150 messages = +100%
    expect(screen.getByTestId('compare-delta')).toHaveTextContent(/\+100% messages/)
    // 5 vs 10 attachments = -50%
    expect(screen.getByTestId('compare-delta')).toHaveTextContent(/-50% attachments/)
  })

  it('legend reflects Absolute | Normalized toggle', async () => {
    renderCompare()
    await waitFor(() => {
      expect(screen.getByTestId('compare-legend')).toHaveTextContent(/Absolute counts/)
    })
    fireEvent.click(screen.getByTestId('compare-scale-normalized'))
    expect(screen.getByTestId('compare-legend')).toHaveTextContent(/Normalized/)
    fireEvent.click(screen.getByTestId('compare-scale-absolute'))
    expect(screen.getByTestId('compare-legend')).toHaveTextContent(/Absolute counts/)
  })

  it('View as table shows accessible compare table', async () => {
    renderCompare()
    await waitFor(() => {
      expect(screen.getByTestId('compare-view')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('compare-table-toggle'))
    expect(screen.getByTestId('compare-table')).toBeInTheDocument()
    expect(screen.getByTestId('compare-table').querySelector('caption')).toBeTruthy()
    expect(screen.getByTestId('compare-table').querySelector('th[scope="col"]')).toBeTruthy()
  })

  it('Close button exits', async () => {
    const { onExit } = renderCompare()
    await waitFor(() => {
      expect(screen.getByTestId('compare-exit')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('compare-exit'))
    expect(onExit).toHaveBeenCalledTimes(1)
  })

  it('fetches compare endpoint with both ranges', async () => {
    const { fetchMock } = renderCompare()
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) =>
          String(c[0]).includes('/api/chronicle/compare'),
        ),
      ).toBe(true)
    })
    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/api/chronicle/compare'),
    )
    const init = call?.[1] as RequestInit
    const body = JSON.parse(String(init.body)) as {
      a: { from: string }
      b: { from: string }
    }
    expect(body.a.from).toMatch(/2015/)
    expect(body.b.from).toMatch(/2016/)
  })
})
