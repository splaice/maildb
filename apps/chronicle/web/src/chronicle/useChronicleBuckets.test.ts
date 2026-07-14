import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChronicleBuckets } from '../api/types'
import {
  quantizePixelWidth,
  useChronicleBuckets,
} from './useChronicleBuckets'

function mockBuckets(overrides: Partial<ChronicleBuckets> = {}): ChronicleBuckets {
  return {
    scope_fingerprint: 'qs_test',
    aggregation: 'month',
    unit: 'month',
    viewport: { from: '2014-01-01T00:00:00.000Z', to: '2019-01-01T00:00:00.000Z' },
    lanes: {
      messages: [{ bucket: '2014-01-01T00:00:00.000Z', count: 10 }],
      attachments: [{ bucket: '2014-01-01T00:00:00.000Z', count: 2 }],
    },
    density: {
      unit: 'year',
      buckets: [{ bucket: '2014-01-01T00:00:00.000Z', count: 10 }],
    },
    extent: { from: '2010-01-01T00:00:00.000Z', to: '2020-01-01T00:00:00.000Z' },
    generated_at: '2026-01-01T00:00:00.000Z',
    ...overrides,
  }
}

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children)
  }
}

describe('quantizePixelWidth', () => {
  it('quantizes to 32px steps', () => {
    expect(quantizePixelWidth(920)).toBe(928) // round(920/32)*32
    expect(quantizePixelWidth(32)).toBe(32)
    expect(quantizePixelWidth(31)).toBe(32)
    expect(quantizePixelWidth(64)).toBe(64)
    expect(quantizePixelWidth(80)).toBe(96) // round(2.5)*32
  })
})

describe('useChronicleBuckets', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('fetch called with quantized pixel width', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockBuckets(),
    })
    vi.stubGlobal('fetch', fetchMock)

    const viewport = {
      fromMs: Date.UTC(2014, 0, 1),
      toMs: Date.UTC(2019, 0, 1),
    }

    renderHook(
      () => useChronicleBuckets({ viewport, pixelWidth: 920 }),
      { wrapper: createWrapper() },
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(String(init.body)) as { pixel_width: number }
    expect(body.pixel_width).toBe(quantizePixelWidth(920))
  })

  it('changing viewport aborts the prior request', async () => {
    const signals: AbortSignal[] = []
    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.signal) signals.push(init.signal)
      return new Promise((resolve) => {
        // Never resolve quickly — stay in-flight so the next key aborts us.
        const signal = init?.signal
        const done = () =>
          resolve({
            ok: true,
            status: 200,
            json: async () => mockBuckets(),
          })
        if (signal?.aborted) {
          const err = new Error('Aborted')
          err.name = 'AbortError'
          throw err
        }
        signal?.addEventListener('abort', () => {
          const err = new Error('Aborted')
          err.name = 'AbortError'
          // reject by throwing in microtask is awkward; leave hanging
        })
        // Resolve after long delay if not aborted
        setTimeout(done, 5000)
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { rerender } = renderHook(
      ({ fromMs }: { fromMs: number }) =>
        useChronicleBuckets({
          viewport: { fromMs, toMs: fromMs + 365 * 24 * 3600 * 1000 },
          pixelWidth: 920,
        }),
      {
        wrapper: createWrapper(),
        initialProps: { fromMs: Date.UTC(2014, 0, 1) },
      },
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    rerender({ fromMs: Date.UTC(2015, 0, 1) })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    expect(signals.length).toBeGreaterThanOrEqual(1)
    expect(signals[0]!.aborted).toBe(true)
  })

  it('keepPreviousData exposes stale data while refetching', async () => {
    let resolveFirst: ((v: unknown) => void) | undefined
    let call = 0
    const fetchMock = vi.fn().mockImplementation(() => {
      call++
      if (call === 1) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () =>
            mockBuckets({
              lanes: {
                messages: [{ bucket: '2014-01-01T00:00:00.000Z', count: 99 }],
                attachments: [],
              },
            }),
        })
      }
      return new Promise((resolve) => {
        resolveFirst = resolve
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result, rerender } = renderHook(
      ({ fromMs }: { fromMs: number }) =>
        useChronicleBuckets({
          viewport: { fromMs, toMs: fromMs + 5 * 365 * 24 * 3600 * 1000 },
          pixelWidth: 920,
        }),
      {
        wrapper: createWrapper(),
        initialProps: { fromMs: Date.UTC(2014, 0, 1) },
      },
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    await waitFor(() => {
      expect(result.current.data?.lanes.messages?.[0]?.count).toBe(99)
    })

    rerender({ fromMs: Date.UTC(2016, 0, 1) })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
    })

    // While second request is in flight, previous data remains available
    expect(result.current.isFetching).toBe(true)
    expect(result.current.data?.lanes.messages?.[0]?.count).toBe(99)
    expect(result.current.isPlaceholderData).toBe(true)

    await act(async () => {
      resolveFirst?.({
        ok: true,
        status: 200,
        json: async () =>
          mockBuckets({
            lanes: {
              messages: [{ bucket: '2016-01-01T00:00:00.000Z', count: 1 }],
              attachments: [],
            },
          }),
      })
    })
  })
})
