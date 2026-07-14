import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { FocusMode } from './FocusMode'

const focus = {
  fromMs: Date.UTC(2015, 0, 1),
  toMs: Date.UTC(2016, 0, 1),
}

function mockBucketsFiner() {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      scope_fingerprint: 'qs_focus',
      aggregation: 'month',
      unit: 'month',
      viewport: {
        from: '2015-01-01T00:00:00.000Z',
        to: '2016-01-01T00:00:00.000Z',
      },
      lanes: {
        messages: [
          { bucket: '2015-01-01T00:00:00.000Z', count: 10 },
          { bucket: '2015-06-01T00:00:00.000Z', count: 20 },
        ],
        attachments: [
          { bucket: '2015-01-01T00:00:00.000Z', count: 2 },
          { bucket: '2015-06-01T00:00:00.000Z', count: 3 },
        ],
      },
      density: { unit: 'year', buckets: [] },
      extent: {
        from: '2014-01-01T00:00:00.000Z',
        to: '2019-01-01T00:00:00.000Z',
      },
      generated_at: '2026-01-01T00:00:00.000Z',
    }),
  } as Response
}

function mockList(items: unknown[] = [], next: string | null = null) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      items,
      next_cursor: next,
      scope_fingerprint: 'qs',
    }),
  } as Response
}

function renderFocus(props: Partial<Parameters<typeof FocusMode>[0]> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const onExit = vi.fn()
  const onSetAsScopeDate = vi.fn()
  const onSelectMessage = vi.fn()
  const result = render(
    <QueryClientProvider client={client}>
      <FocusMode
        focus={focus}
        scope={{ mailboxes: ['me@example.com'] }}
        mainUnit="year"
        onExit={onExit}
        onSetAsScopeDate={onSetAsScopeDate}
        onSelectMessage={onSelectMessage}
        {...props}
      />
    </QueryClientProvider>,
  )
  return { ...result, onExit, onSetAsScopeDate, onSelectMessage, client }
}

describe('FocusMode', () => {
  beforeEach(() => {
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('local chronology fetch uses focus viewport and narrow pixel width', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (String(url).includes('/api/chronicle/buckets')) return mockBucketsFiner()
      if (String(url).includes('/api/sources/list')) return mockList()
      throw new Error(`unexpected: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderFocus()

    await waitFor(() => {
      const bucketCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes('/api/chronicle/buckets'),
      )
      expect(bucketCall).toBeTruthy()
      const body = JSON.parse(String((bucketCall![1] as RequestInit).body)) as {
        viewport: { from: string; to: string }
        pixel_width: number
      }
      expect(body.viewport.from.startsWith('2015-01-01')).toBe(true)
      expect(body.viewport.to.startsWith('2016-01-01')).toBe(true)
      // Narrower rail width (~200) quantized to 32px steps → 192
      expect(body.pixel_width).toBeLessThanOrEqual(224)
      expect(body.pixel_width).toBeGreaterThanOrEqual(160)
    })

    expect(await screen.findByTestId('focus-mode')).toBeInTheDocument()
    expect(screen.getByTestId('focus-period-label')).toHaveTextContent(/Jan 2015/)
    // Finer unit than main year view
    expect(screen.getByTestId('focus-totals')).toHaveTextContent(/month/)
  })

  it('sub-period click narrows the source list date_from', async () => {
    const listBodies: { date_from: string; date_to: string }[] = []
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/api/chronicle/buckets')) return mockBucketsFiner()
      if (String(url).includes('/api/sources/list')) {
        const body = JSON.parse(String((init as RequestInit).body)) as {
          date_from: string
          date_to: string
        }
        listBodies.push(body)
        return mockList([
          {
            id: 'msg_1',
            subject: 'Hi',
            sender_name: 'A',
            sender_address: 'a@b.com',
            date: '2015-06-02T00:00:00Z',
            mailbox: 'me@example.com',
            has_attachment: false,
            attachment_count: 0,
            thread_id: null,
          },
        ])
      }
      throw new Error(`unexpected: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderFocus()

    await screen.findByTestId('focus-local-chronology')
    const sub = await screen.findByTestId(
      'focus-subperiod-2015-06-01T00:00:00.000Z',
    )
    fireEvent.click(sub)

    await waitFor(() => {
      expect(listBodies.some((b) => b.date_from.startsWith('2015-06-01'))).toBe(
        true,
      )
    })
  })

  it('Set as scope date writes scope and exits focus', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/chronicle/buckets')) return mockBucketsFiner()
        if (String(url).includes('/api/sources/list')) return mockList()
        throw new Error(`unexpected: ${url}`)
      }),
    )

    // Use store-connected path for applyFocusAsScopeDate semantics
    useWorkingSetStore.getState().setFocus(focus)
    useWorkingSetStore.getState().addMailbox('me@example.com')

    const onSetAsScopeDate = () => {
      useWorkingSetStore.getState().applyFocusAsScopeDate()
    }
    const onExit = vi.fn()

    renderFocus({
      onSetAsScopeDate,
      onExit,
      scope: { mailboxes: ['me@example.com'] },
    })

    fireEvent.click(await screen.findByTestId('focus-set-scope-date'))

    const s = useWorkingSetStore.getState()
    expect(s.focus).toBeNull()
    expect(s.scope.date).toEqual({ from: '2015-01-01', to: '2016-01-01' })
    expect(s.scope.mailboxes).toEqual(['me@example.com'])
  })

  it('Exit calls onExit', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/chronicle/buckets')) return mockBucketsFiner()
        if (String(url).includes('/api/sources/list')) return mockList()
        throw new Error(`unexpected: ${url}`)
      }),
    )
    const { onExit } = renderFocus()
    fireEvent.click(await screen.findByTestId('focus-exit'))
    expect(onExit).toHaveBeenCalledTimes(1)
  })
})
