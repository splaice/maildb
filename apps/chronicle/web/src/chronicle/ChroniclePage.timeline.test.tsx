import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChronicleBuckets } from '../api/types'
import {
  mockArchiveSummary,
  mockSessionOk,
  renderApp,
} from '../test/test-utils'
import { resetWorkingSetStore } from '../workingset/store'

function mockBuckets(overrides: Partial<ChronicleBuckets> = {}): Response {
  const body: ChronicleBuckets = {
    scope_fingerprint: 'qs_test',
    aggregation: 'month',
    unit: 'month',
    viewport: { from: '2014-01-01T00:00:00.000Z', to: '2019-01-01T00:00:00.000Z' },
    lanes: {
      messages: [
        { bucket: '2014-01-01T00:00:00.000Z', count: 100 },
        { bucket: '2015-01-01T00:00:00.000Z', count: 200 },
      ],
      attachments: [{ bucket: '2014-01-01T00:00:00.000Z', count: 10 }],
    },
    density: {
      unit: 'year',
      buckets: [
        { bucket: '2014-01-01T00:00:00.000Z', count: 100 },
        { bucket: '2015-01-01T00:00:00.000Z', count: 200 },
      ],
    },
    extent: {
      from: '2014-01-01T00:00:00.000Z',
      to: '2019-01-01T00:00:00.000Z',
    },
    generated_at: '2026-01-01T00:00:00.000Z',
    ...overrides,
  }
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response
}

function installFetch(
  impl: (url: string, init?: RequestInit) => Promise<unknown> | unknown,
) {
  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (String(url).includes('/api/auth/session')) return mockSessionOk()
    if (String(url).includes('/api/archive/summary')) return mockArchiveSummary()
    return impl(String(url), init)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('ChroniclePage timeline', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
  })

  it('bootstrap sets viewport to extent', async () => {
    const fetchMock = installFetch(async (url) => {
      if (url.includes('/api/chronicle/buckets')) return mockBuckets()
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderApp(['/'])

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/chronicle/buckets'))).toBe(
        true,
      )
    })

    // Visible period reflects extent (Jan 2014 – Jan 2019)
    expect(await screen.findByTestId('visible-period')).toHaveTextContent(/Jan 2014/)
    expect(screen.getByTestId('visible-period')).toHaveTextContent(/Jan 2019|Dec 2018/)

    // Bootstrap request used the sentinel full-range viewport
    const bucketCalls = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes('/api/chronicle/buckets'),
    )
    expect(bucketCalls.length).toBeGreaterThanOrEqual(1)
    const firstBody = JSON.parse(String((bucketCalls[0]![1] as RequestInit).body)) as {
      viewport: { from: string; to: string }
    }
    expect(firstBody.viewport.from.startsWith('1970-01-01')).toBe(true)
    expect(firstBody.viewport.to.startsWith('2100-01-01')).toBe(true)
  })

  it('error → Retry refetches', async () => {
    let fail = true
    const fetchMock = installFetch(async (url) => {
      if (url.includes('/api/chronicle/buckets')) {
        if (fail) {
          return {
            ok: false,
            status: 500,
            json: async () => ({ detail: 'boom' }),
          }
        }
        return mockBuckets()
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderApp(['/'])

    const alert = await screen.findByTestId('timeline-error')
    expect(alert).toHaveTextContent(/Failed to load timeline/)

    const retries = screen.getAllByRole('button', { name: /^retry$/i })
    const timelineRetry = retries.find((b) => alert.contains(b)) ?? retries[0]!
    fail = false
    fireEvent.click(timelineRetry)

    await waitFor(() => {
      expect(screen.queryByTestId('timeline-error')).not.toBeInTheDocument()
    })
    expect(
      fetchMock.mock.calls.filter((c) => String(c[0]).includes('/api/chronicle/buckets')).length,
    ).toBeGreaterThanOrEqual(2)
  })
})
