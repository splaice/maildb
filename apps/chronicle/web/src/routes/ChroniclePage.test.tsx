import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  mockArchiveSummary,
  mockSessionOk,
  mockUnauthorized,
  renderApp,
} from '../test/test-utils'

function mockBucketsOk() {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      scope_fingerprint: 'qs_test',
      aggregation: 'month',
      unit: 'month',
      viewport: { from: '2014-01-01T00:00:00.000Z', to: '2019-01-01T00:00:00.000Z' },
      lanes: { messages: [], attachments: [] },
      density: { unit: 'year', buckets: [] },
      extent: { from: '2014-01-01T00:00:00.000Z', to: '2019-01-01T00:00:00.000Z' },
      generated_at: '2026-01-01T00:00:00.000Z',
    }),
  } as Response
}

describe('Archive summary panel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders mocked ArchiveSummary counts', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockSessionOk()
        if (String(url).includes('/api/archive/summary')) {
          return mockArchiveSummary()
        }
        if (String(url).includes('/api/chronicle/buckets')) return mockBucketsOk()
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/'])

    // Summary label is always in the collapsible details; wait for loaded counts.
    expect(await screen.findByText('1,280,000')).toBeInTheDocument()
    expect(screen.getByText('Archive coverage')).toBeInTheDocument()
    expect(screen.getByText('400,000')).toBeInTheDocument()
    expect(screen.getByText('50,000')).toBeInTheDocument()
    expect(screen.getByText('12,000')).toBeInTheDocument()
    expect(
      screen.getByText(/40000 extracted \/ 100 failed \/ 200 skipped \/ 50 pending/),
    ).toBeInTheDocument()
    expect(screen.getByText(/1200000 embedded \/ 80000 missing/)).toBeInTheDocument()
  })

  it('shows Retry on error state', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (String(url).includes('/api/auth/session')) return mockSessionOk()
      if (String(url).includes('/api/archive/summary')) {
        return {
          ok: false,
          status: 500,
          json: async () => ({ detail: 'boom' }),
        } as Response
      }
      if (String(url).includes('/api/chronicle/buckets')) return mockBucketsOk()
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderApp(['/'])

    const alerts = await screen.findAllByRole('alert')
    const coverageAlert = alerts.find((a) =>
      /Failed to load archive coverage/.test(a.textContent ?? ''),
    )
    expect(coverageAlert).toBeTruthy()
    const retry = screen
      .getAllByRole('button', { name: /retry/i })
      .find((b) => coverageAlert!.contains(b))
    expect(retry).toBeTruthy()

    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/auth/session')) return mockSessionOk()
      if (String(url).includes('/api/archive/summary')) {
        return mockArchiveSummary()
      }
      if (String(url).includes('/api/chronicle/buckets')) return mockBucketsOk()
      throw new Error(`unexpected fetch: ${url}`)
    })

    fireEvent.click(retry!)

    await waitFor(() => {
      expect(screen.getByText('Archive coverage')).toBeInTheDocument()
      expect(
        screen.queryByText(/Failed to load archive coverage/),
      ).not.toBeInTheDocument()
    })
  })

  it('redirects unauthenticated visits to /login', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockUnauthorized()
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/'])

    expect(await screen.findByLabelText(/username/i)).toBeInTheDocument()
    expect(screen.queryByTestId('workstation-shell')).not.toBeInTheDocument()
  })
})
