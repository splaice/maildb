import { act, cleanup, render, renderHook } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore, useWorkingSetStore } from './store'
import { resetUrlSyncForTests, useUrlSync } from './useUrlSync'
import { encodeState } from './urlState'

function clearUrl() {
  window.history.replaceState(null, '', '/')
}

describe('useUrlSync', () => {
  beforeEach(() => {
    clearUrl()
    resetUrlSyncForTests()
    resetWorkingSetStore()
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
    resetUrlSyncForTests()
    clearUrl()
    resetWorkingSetStore()
  })

  it('hydrates from URL on mount (deep link)', () => {
    const params = encodeState({
      scope: { date: { from: '2014-01-01', to: '2018-01-01' } },
      viewport: {
        fromMs: Date.UTC(2014, 0, 1),
        toMs: Date.UTC(2015, 0, 1),
      },
      aggregation: 'auto',
      view: 'table',
      selection: null,
    })
    window.history.replaceState(null, '', `/?${params.toString()}`)

    function App() {
      useUrlSync()
      const view = useWorkingSetStore((s) => s.view)
      return <span data-testid="view">{view}</span>
    }

    const { getByTestId } = render(<App />)
    expect(getByTestId('view').textContent).toBe('table')
    const s = useWorkingSetStore.getState()
    expect(s.scope.date?.from).toBe('2014-01-01')
    expect(s.viewport?.fromMs).toBe(Date.UTC(2014, 0, 1))
    expect(s.viewport?.toMs).toBe(Date.UTC(2015, 0, 1))
  })

  it('mount hydration runs before consumer effects (ordering assertable via mock)', () => {
    const params = encodeState({
      scope: { mailboxes: ['deep@link.com'] },
      viewport: null,
      aggregation: 'auto',
      view: 'table',
      selection: null,
    })
    window.history.replaceState(null, '', `/?${params.toString()}`)
    resetWorkingSetStore()

    const events: string[] = []
    const fetchMock = vi.fn(() => {
      events.push('fetch')
      // Store must already reflect the deep link when "first fetch" would run.
      expect(useWorkingSetStore.getState().view).toBe('table')
      expect(useWorkingSetStore.getState().scope.mailboxes).toEqual(['deep@link.com'])
    })

    function App() {
      useUrlSync()
      useEffect(() => {
        events.push('effect')
        fetchMock()
      }, [])
      return null
    }

    render(<App />)
    expect(events).toEqual(['effect', 'fetch'])
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('transient setViewport debounces to one replaceState', () => {
    const replaceSpy = vi.spyOn(window.history, 'replaceState')
    const pushSpy = vi.spyOn(window.history, 'pushState')

    renderHook(() => useUrlSync())

    act(() => {
      useWorkingSetStore.getState().setViewport({
        fromMs: Date.UTC(2014, 0, 1),
        toMs: Date.UTC(2014, 6, 1),
      })
      useWorkingSetStore.getState().setViewport({
        fromMs: Date.UTC(2014, 0, 1),
        toMs: Date.UTC(2014, 8, 1),
      })
      useWorkingSetStore.getState().setViewport({
        fromMs: Date.UTC(2014, 0, 1),
        toMs: Date.UTC(2014, 11, 1),
      })
    })

    expect(replaceSpy).not.toHaveBeenCalled()
    expect(pushSpy).not.toHaveBeenCalled()

    act(() => {
      vi.advanceTimersByTime(300)
    })

    expect(pushSpy).not.toHaveBeenCalled()
    // Initial clearUrl may have called replaceState; filter to search-param writes.
    const searchWrites = replaceSpy.mock.calls.filter(
      (c) => typeof c[2] === 'string' && String(c[2]).includes('vf='),
    )
    expect(searchWrites.length).toBe(1)
    const written = decodeURIComponent(String(searchWrites[0]![2]))
    expect(written).toContain('vt=2014-12-01T00:00:00Z')
  })

  it('analytical action → pushState', () => {
    const pushSpy = vi.spyOn(window.history, 'pushState')

    renderHook(() => useUrlSync())

    act(() => {
      useWorkingSetStore.getState().setView('table')
    })

    expect(pushSpy).toHaveBeenCalledTimes(1)
    expect(String(pushSpy.mock.calls[0]![2])).toContain('view=table')
  })

  it('coalesces identical analytical pushes', () => {
    const pushSpy = vi.spyOn(window.history, 'pushState')
    renderHook(() => useUrlSync())

    act(() => {
      useWorkingSetStore.getState().setView('table')
    })
    expect(pushSpy).toHaveBeenCalledTimes(1)

    act(() => {
      useWorkingSetStore.getState().setView('table')
    })
    expect(pushSpy).toHaveBeenCalledTimes(1)
  })

  it('popstate hydrates the store', () => {
    renderHook(() => useUrlSync())

    act(() => {
      useWorkingSetStore.getState().setView('table')
      useWorkingSetStore.getState().addMailbox('a@b.com')
    })

    // Simulate prior history entry (canvas, no mailbox) then pop back.
    window.history.replaceState(null, '', '/')
    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate'))
    })

    const s = useWorkingSetStore.getState()
    expect(s.view).toBe('canvas')
    expect(s.scope.mailboxes).toBeUndefined()
  })

  it('applyBrushAsViewport pushes history once', () => {
    const pushSpy = vi.spyOn(window.history, 'pushState')
    renderHook(() => useUrlSync())

    act(() => {
      useWorkingSetStore.getState().setBrush({
        fromMs: Date.UTC(2015, 0, 1),
        toMs: Date.UTC(2016, 0, 1),
      })
      useWorkingSetStore.getState().applyBrushAsViewport()
    })

    expect(pushSpy).toHaveBeenCalledTimes(1)
    expect(String(pushSpy.mock.calls[0]![2])).toContain('vf=')
    expect(useWorkingSetStore.getState().brush).toBeNull()
  })
})
