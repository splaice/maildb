import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  mockArchiveSummary,
  mockSessionOk,
  mockUnauthorized,
  renderApp,
} from '../test/test-utils'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'

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
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
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

describe('ChroniclePage working-set wiring', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
  })

  it('sends store scope on buckets request', async () => {
    // Deep-link scope so mount hydrate restores it before the buckets fetch.
    window.history.replaceState(
      null,
      '',
      '/?mb=me%40example.com&df=2014-01-01&dt=2018-01-01',
    )

    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (String(url).includes('/api/auth/session')) return mockSessionOk()
      if (String(url).includes('/api/archive/summary')) return mockArchiveSummary()
      if (String(url).includes('/api/chronicle/buckets')) return mockBucketsOk()
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderApp(['/'])

    await waitFor(() => {
      const scoped = fetchMock.mock.calls.some((c) => {
        if (!String(c[0]).includes('/api/chronicle/buckets')) return false
        const body = JSON.parse(String((c[1] as RequestInit).body)) as {
          scope: { mailboxes?: string[]; date?: { from?: string; to?: string } }
        }
        return (
          body.scope?.mailboxes?.[0] === 'me@example.com' &&
          body.scope?.date?.from === '2014-01-01'
        )
      })
      expect(scoped).toBe(true)
    })
  })

  it('View as table toggles store view', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockSessionOk()
        if (String(url).includes('/api/archive/summary')) return mockArchiveSummary()
        if (String(url).includes('/api/chronicle/buckets')) return mockBucketsOk()
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/'])
    expect(await screen.findByTestId('visible-period')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /view as table/i }))
    expect(useWorkingSetStore.getState().view).toBe('table')
    expect(await screen.findByTestId('timeline-table')).toBeInTheDocument()
  })

  it('lane config toggle/reorder update store and URL', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockSessionOk()
        if (String(url).includes('/api/archive/summary')) return mockArchiveSummary()
        if (String(url).includes('/api/chronicle/buckets')) return mockBucketsOk()
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/'])
    expect(await screen.findByTestId('lane-config-panel')).toBeInTheDocument()
    expect(await screen.findByTestId('timeline-canvas-wrap')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Show People (distinct)'))
    expect(useWorkingSetStore.getState().lanes).toContain('people')

    await waitFor(() => {
      expect(window.location.search).toMatch(/ln=/)
    })
    expect(window.location.search).toMatch(/people/)

    fireEvent.click(screen.getByLabelText('Move Messages down'))
    const lanes = useWorkingSetStore.getState().lanes
    expect(lanes.indexOf('messages')).toBeGreaterThan(0)
  })

  it('canvas height responds to lane config', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockSessionOk()
        if (String(url).includes('/api/archive/summary')) return mockArchiveSummary()
        if (String(url).includes('/api/chronicle/buckets')) return mockBucketsOk()
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/'])
    const wrap = await screen.findByTestId('timeline-canvas-wrap')
    const hBefore = Number(wrap.getAttribute('data-canvas-h'))
    expect(hBefore).toBeGreaterThan(0)

    // Hide attachments → fewer bars → shorter canvas
    fireEvent.click(screen.getByLabelText('Show Attachments'))
    await waitFor(() => {
      const hAfter = Number(
        screen.getByTestId('timeline-canvas-wrap').getAttribute('data-canvas-h'),
      )
      expect(hAfter).toBeLessThan(hBefore)
    })
  })

  it('Enter key enters focus when a brush exists', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockSessionOk()
        if (String(url).includes('/api/archive/summary')) return mockArchiveSummary()
        if (String(url).includes('/api/chronicle/buckets')) return mockBucketsOk()
        if (String(url).includes('/api/sources/list')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              items: [],
              next_cursor: null,
              scope_fingerprint: 'qs',
            }),
          } as Response
        }
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/'])
    expect(await screen.findByTestId('timeline-toolbar')).toBeInTheDocument()

    const brush = {
      fromMs: Date.UTC(2015, 0, 1),
      toMs: Date.UTC(2016, 0, 1),
    }
    useWorkingSetStore.getState().setBrush(brush)

    fireEvent.keyDown(window, { key: 'Enter' })

    await waitFor(() => {
      expect(useWorkingSetStore.getState().focus).toEqual(brush)
    })
    expect(await screen.findByTestId('focus-mode')).toBeInTheDocument()
    expect(useWorkingSetStore.getState().brush).toBeNull()
  })
})
