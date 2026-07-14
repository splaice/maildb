/**
 * Chronicle acceptance tests — spec §4.10 criteria 1–8.
 * Mocked-API integration (no real server). One `it` per criterion.
 *
 * Criterion 4 (generated events) is deferred to Phase 3 as `it.todo`.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChronicleBuckets } from '../api/types'
import {
  mockArchiveSummary,
  mockSessionOk,
  renderApp,
} from '../test/test-utils'
import { resetUrlSyncForTests } from '../workingset/useUrlSync'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { encodeState } from '../workingset/urlState'

const EXTENT_FROM = '2014-01-01T00:00:00.000Z'
const EXTENT_TO = '2019-01-01T00:00:00.000Z'

function mockBuckets(overrides: Partial<ChronicleBuckets> = {}): Response {
  const body: ChronicleBuckets = {
    scope_fingerprint: 'qs_test',
    aggregation: 'month',
    unit: 'month',
    viewport: { from: EXTENT_FROM, to: EXTENT_TO },
    lanes: {
      messages: [
        { bucket: '2014-01-01T00:00:00.000Z', count: 100 },
        { bucket: '2015-01-01T00:00:00.000Z', count: 200 },
        { bucket: '2016-01-01T00:00:00.000Z', count: 150 },
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
    extent: { from: EXTENT_FROM, to: EXTENT_TO },
    generated_at: '2026-01-01T00:00:00.000Z',
    ...overrides,
  }
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response
}

function mockSourceList(items: unknown[] = []) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      items,
      next_cursor: null,
      scope_fingerprint: 'qs',
    }),
  } as Response
}

function mockMessageSource(sid = 'msg_42') {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      kind: 'msg',
      envelope: {
        id: sid,
        thread_id: 'thr_1',
        subject: 'Acceptance subject',
        sender_name: 'Alice',
        sender_address: 'alice@example.com',
        recipients: { to: ['bob@example.com'], cc: [], bcc: [] },
        date: '2015-06-01T12:00:00Z',
        mailbox: 'me@example.com',
        labels: [],
        has_attachment: false,
        attachments: [],
      },
      body: {
        text: 'body',
        html: null,
        remote_resources_blocked: 0,
        had_active_content: false,
      },
    }),
  } as Response
}

function installFetch(
  impl?: (url: string, init?: RequestInit) => Promise<unknown> | unknown,
) {
  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const u = String(url)
    if (u.includes('/api/auth/session')) return mockSessionOk()
    if (u.includes('/api/archive/summary')) return mockArchiveSummary()
    if (impl) {
      const r = await impl(u, init)
      if (r !== undefined) return r
    }
    if (u.includes('/api/chronicle/buckets')) return mockBuckets()
    if (u.includes('/api/sources/list')) return mockSourceList()
    if (u.includes('/api/sources/')) return mockMessageSource()
    throw new Error(`unexpected fetch: ${u}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('Chronicle acceptance (§4.10)', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
    resetUrlSyncForTests()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
    resetUrlSyncForTests()
  })

  it('lands on chronicle with full archive', async () => {
    installFetch()
    renderApp(['/'])

    expect(await screen.findByTestId('workstation-shell')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chronicle' })).toBeInTheDocument()

    // Viewport bootstraps to extent from the first buckets response.
    await waitFor(() => {
      const vp = useWorkingSetStore.getState().viewport
      expect(vp).not.toBeNull()
      expect(vp!.fromMs).toBe(Date.parse(EXTENT_FROM))
      expect(vp!.toMs).toBe(Date.parse(EXTENT_TO))
    })
    expect(await screen.findByTestId('visible-period')).toHaveTextContent(/Jan 2014/)
  })

  it('zoom preserves working set and selection', async () => {
    const fetchMock = installFetch()
    // Deep-link a scope + selection so hydrate restores them before fetches.
    const qs = encodeState({
      scope: { mailboxes: ['me@example.com'] },
      viewport: {
        fromMs: Date.parse(EXTENT_FROM),
        toMs: Date.parse(EXTENT_TO),
      },
      aggregation: 'auto',
      view: 'canvas',
      selection: {
        kind: 'bucket',
        lane: 'messages',
        bucketIso: '2015-01-01T00:00:00.000Z',
      },
      lanes: null,
    }).toString()
    window.history.replaceState(null, '', `/?${qs}`)

    renderApp(['/'])
    expect(await screen.findByTestId('timeline-toolbar')).toBeInTheDocument()

    // Scope chip + selection present
    expect(screen.getByTestId('scope-chip-mailbox')).toBeInTheDocument()
    expect(useWorkingSetStore.getState().selection).toEqual({
      kind: 'bucket',
      lane: 'messages',
      bucketIso: '2015-01-01T00:00:00.000Z',
    })

    const beforeVp = useWorkingSetStore.getState().viewport!
    fireEvent.click(screen.getByRole('button', { name: /zoom in/i }))

    await waitFor(() => {
      const after = useWorkingSetStore.getState().viewport!
      expect(after.toMs - after.fromMs).toBeLessThan(beforeVp.toMs - beforeVp.fromMs)
    })

    // Chips and selection unchanged
    expect(screen.getByTestId('scope-chip-mailbox')).toBeInTheDocument()
    expect(useWorkingSetStore.getState().selection).toEqual({
      kind: 'bucket',
      lane: 'messages',
      bucketIso: '2015-01-01T00:00:00.000Z',
    })
    expect(useWorkingSetStore.getState().scope.mailboxes).toEqual(['me@example.com'])

    // Buckets re-issued with the same scope
    const scoped = fetchMock.mock.calls.filter((c) => {
      if (!String(c[0]).includes('/api/chronicle/buckets')) return false
      const body = JSON.parse(String((c[1] as RequestInit).body)) as {
        scope: { mailboxes?: string[] }
      }
      return body.scope?.mailboxes?.[0] === 'me@example.com'
    })
    expect(scoped.length).toBeGreaterThanOrEqual(1)
  })

  it('date range change updates lanes and count consistently', async () => {
    const fetchMock = installFetch(async (url, init) => {
      if (url.includes('/api/chronicle/buckets')) {
        const body = JSON.parse(String((init as RequestInit).body)) as {
          scope: { date?: { from?: string; to?: string } }
        }
        // Reflect scope date in message totals so scope-bar count updates.
        const scoped = !!body.scope?.date?.from
        return mockBuckets({
          lanes: {
            messages: scoped
              ? [
                  { bucket: '2015-01-01T00:00:00.000Z', count: 42 },
                  { bucket: '2015-06-01T00:00:00.000Z', count: 8 },
                ]
              : [
                  { bucket: '2014-01-01T00:00:00.000Z', count: 100 },
                  { bucket: '2015-01-01T00:00:00.000Z', count: 200 },
                ],
            attachments: [],
          },
        })
      }
      return undefined
    })

    renderApp(['/'])
    expect(await screen.findByTestId('timeline-toolbar')).toBeInTheDocument()

    // Open date editor and apply a range
    fireEvent.click(screen.getByRole('button', { name: /add date filter/i }))
    const editor = await screen.findByTestId('date-range-editor')
    const from = within(editor).getByLabelText(/date from/i)
    const to = within(editor).getByLabelText(/date to/i)
    fireEvent.change(from, { target: { value: '2015-01-01' } })
    fireEvent.change(to, { target: { value: '2015-12-31' } })
    fireEvent.click(within(editor).getByRole('button', { name: /^apply$/i }))

    await waitFor(() => {
      expect(useWorkingSetStore.getState().scope.date).toEqual({
        from: '2015-01-01',
        to: '2015-12-31',
      })
    })

    // Buckets request carries the scope date
    await waitFor(() => {
      const hit = fetchMock.mock.calls.some((c) => {
        if (!String(c[0]).includes('/api/chronicle/buckets')) return false
        const body = JSON.parse(String((c[1] as RequestInit).body)) as {
          scope: { date?: { from?: string; to?: string } }
        }
        return (
          body.scope?.date?.from === '2015-01-01' &&
          body.scope?.date?.to === '2015-12-31'
        )
      })
      expect(hit).toBe(true)
    })

    // Scope-bar count updates from the response (42+8=50)
    await waitFor(() => {
      expect(useWorkingSetStore.getState().resultCount).toBe(50)
    })
    expect(await screen.findByTestId('scope-result-count')).toHaveTextContent(
      /50 messages in scope/,
    )
  })

  // Phase 3: generated events expose origin/evidence/derivation — not in Phase 1.
  it.todo('generated events expose origin/evidence/derivation')

  it('open source and return restores viewport and selection', async () => {
    // Re-assert the 1.4 return contract through focus mode:
    // focus → select message → URL with ff/ft+sel → popstate → still in focus.
    installFetch()

    const focus = {
      fromMs: Date.UTC(2015, 0, 1),
      toMs: Date.UTC(2016, 0, 1),
    }
    const viewport = {
      fromMs: Date.parse(EXTENT_FROM),
      toMs: Date.parse(EXTENT_TO),
    }
    const selection = { kind: 'message' as const, sid: 'msg_42' }

    const chronicleState = {
      scope: { mailboxes: ['me@example.com'] },
      viewport,
      aggregation: 'auto' as const,
      view: 'canvas' as const,
      selection,
      lanes: null as string[] | null,
      focus,
    }
    const qs = encodeState(chronicleState).toString()
    const chronicleUrl = qs ? `/?${qs}` : '/'
    window.history.replaceState(null, '', chronicleUrl)
    useWorkingSetStore.getState().hydrate(chronicleState)

    renderApp(['/'])

    // In focus mode with selection preserved
    expect(await screen.findByTestId('focus-mode')).toBeInTheDocument()
    expect(useWorkingSetStore.getState().focus).toEqual(focus)
    expect(useWorkingSetStore.getState().selection).toEqual(selection)

    // Simulate open full source: push reader path; then return via popstate
    // with the chronicle URL (params preserve focus + selection).
    window.history.pushState(null, '', '/source/msg_42')

    // Corrupt store as if remounted mid-navigation
    useWorkingSetStore.setState({
      focus: null,
      selection: null,
      viewport: { fromMs: 0, toMs: 1 },
      historyIntent: 'silent',
    })

    // Restore chronicle URL and fire popstate (return contract)
    window.history.replaceState(null, '', chronicleUrl)
    window.dispatchEvent(new PopStateEvent('popstate'))

    await waitFor(() => {
      const s = useWorkingSetStore.getState()
      expect(s.focus).toEqual(focus)
      expect(s.selection).toEqual(selection)
      expect(s.viewport?.fromMs).toBe(viewport.fromMs)
      expect(s.viewport?.toMs).toBe(viewport.toMs)
    })

    // Leave a clean URL for subsequent tests (avoid ff/ft leak)
    window.history.replaceState(null, '', '/')
  })

  it('brushed period opens in focus with same scope', async () => {
    installFetch()
    renderApp(['/'])
    expect(await screen.findByTestId('timeline-toolbar')).toBeInTheDocument()

    // Set scope chips + brush via store (scope object must survive focus round-trip)
    useWorkingSetStore.getState().setScopeDate({ from: '2014-01-01', to: '2018-01-01' })
    useWorkingSetStore.getState().addMailbox('me@example.com')

    await waitFor(() => {
      expect(screen.getByTestId('scope-chip-mailbox')).toBeInTheDocument()
      expect(screen.getByTestId('scope-chip-date')).toBeInTheDocument()
    })

    // Programmatically set a brush then click Focus period
    // (canvas brush drag is hard to synthesize reliably in jsdom)
    const brush = {
      fromMs: Date.UTC(2015, 0, 1),
      toMs: Date.UTC(2015, 6, 1),
    }
    useWorkingSetStore.getState().setBrush(brush)

    await waitFor(() => {
      expect(screen.getByTestId('focus-period-btn')).not.toBeDisabled()
    })
    fireEvent.click(screen.getByTestId('focus-period-btn'))

    expect(await screen.findByTestId('focus-mode')).toBeInTheDocument()
    expect(screen.getByTestId('focus-period-label')).toHaveTextContent(/Jan 2015/)
    // Scope chips intact (Research Desk/Topic Atlas transfer is Phase 2/4)
    expect(screen.getByTestId('scope-chip-mailbox')).toBeInTheDocument()
    expect(screen.getByTestId('scope-chip-date')).toBeInTheDocument()
    expect(useWorkingSetStore.getState().scope.mailboxes).toEqual(['me@example.com'])
    expect(useWorkingSetStore.getState().brush).toBeNull()
  })

  it('dense periods aggregate with open-as-list', async () => {
    installFetch(async (url) => {
      if (url.includes('/api/chronicle/buckets')) {
        return mockBuckets({
          aggregation: 'year',
          unit: 'year',
          lanes: {
            messages: [
              { bucket: '2014-01-01T00:00:00.000Z', count: 50000 },
              { bucket: '2015-01-01T00:00:00.000Z', count: 80000 },
            ],
            attachments: [
              { bucket: '2014-01-01T00:00:00.000Z', count: 500 },
            ],
          },
        })
      }
      if (url.includes('/api/sources/list')) {
        return mockSourceList([
          {
            id: 'msg_dense',
            subject: 'Dense period source',
            sender_name: 'A',
            sender_address: 'a@b.com',
            date: '2014-06-01T00:00:00Z',
            mailbox: 'me@example.com',
            has_attachment: false,
            attachment_count: 0,
            thread_id: null,
          },
        ])
      }
      return undefined
    })

    renderApp(['/'])
    expect(await screen.findByTestId('timeline-canvas')).toBeInTheDocument()
    // Unit from response is year (aggregated)
    expect(await screen.findByTestId('visible-period')).toHaveTextContent(/year buckets/)

    // Bucket selection → inspector list ("Open as list")
    useWorkingSetStore.getState().setSelection({
      kind: 'bucket',
      lane: 'messages',
      bucketIso: '2014-01-01T00:00:00.000Z',
    })
    useWorkingSetStore.getState().setTimelineUnit('year')

    expect(await screen.findByTestId('inspector-bucket')).toBeInTheDocument()
    expect(screen.getByText(/Open as list/i)).toBeInTheDocument()
    expect(await screen.findByTestId('source-list')).toBeInTheDocument()
    expect(screen.getByText('Dense period source')).toBeInTheDocument()
  })

  it('usable with AI disabled', async () => {
    // No model-dependent code paths exist yet in Phase 1. Assert the app
    // renders fully with only the implemented endpoints mocked — guards
    // against future accidental hard-dependencies on AI services
    // (no /api/ask, no embedding generation, no event generation).
    const fetchMock = installFetch()

    renderApp(['/'])

    expect(await screen.findByTestId('workstation-shell')).toBeInTheDocument()
    expect(await screen.findByTestId('timeline-toolbar')).toBeInTheDocument()
    expect(screen.getByTestId('evidence-inspector')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chronicle' })).toBeInTheDocument()

    const urls = fetchMock.mock.calls.map((c) => String(c[0]))
    const allowed = [
      '/api/auth/session',
      '/api/archive/summary',
      '/api/chronicle/buckets',
      '/api/sources/',
    ]
    for (const u of urls) {
      const ok = allowed.some((a) => u.includes(a))
      expect(ok, `unexpected endpoint (AI?): ${u}`).toBe(true)
    }
    expect(urls.some((u) => u.includes('/api/ask'))).toBe(false)
    expect(urls.some((u) => u.includes('/api/events'))).toBe(false)
  })
})
