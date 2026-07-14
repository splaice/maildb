import { beforeEach, describe, expect, it } from 'vitest'

import { resetWorkingSetStore, useWorkingSetStore } from './store'

describe('working set store', () => {
  beforeEach(() => {
    resetWorkingSetStore()
  })

  it('setViewport is transient and does not clear brush', () => {
    const brush = { fromMs: 1, toMs: 2 }
    useWorkingSetStore.getState().setBrush(brush)
    useWorkingSetStore.getState().setViewport({ fromMs: 10, toMs: 20 })
    const s = useWorkingSetStore.getState()
    expect(s.viewport).toEqual({ fromMs: 10, toMs: 20 })
    expect(s.brush).toEqual(brush)
    expect(s.historyIntent).toBe('transient')
  })

  it('applyBrushAsViewport sets viewport, clears brush, analytical', () => {
    useWorkingSetStore.getState().setBrush({ fromMs: 100, toMs: 200 })
    useWorkingSetStore.getState().applyBrushAsViewport()
    const s = useWorkingSetStore.getState()
    expect(s.viewport).toEqual({ fromMs: 100, toMs: 200 })
    expect(s.brush).toBeNull()
    expect(s.historyIntent).toBe('analytical')
  })

  it('applyBrushAsViewport is a no-op without brush', () => {
    useWorkingSetStore.getState().setViewport({ fromMs: 1, toMs: 2 })
    useWorkingSetStore.getState().applyBrushAsViewport()
    expect(useWorkingSetStore.getState().viewport).toEqual({ fromMs: 1, toMs: 2 })
  })

  it('scope date / mailbox / sender mutations are analytical', () => {
    useWorkingSetStore.getState().setScopeDate({ from: '2014-01-01', to: '2018-01-01' })
    expect(useWorkingSetStore.getState().scope.date).toEqual({
      from: '2014-01-01',
      to: '2018-01-01',
    })
    expect(useWorkingSetStore.getState().historyIntent).toBe('analytical')

    useWorkingSetStore.getState().addMailbox('a@b.com')
    useWorkingSetStore.getState().addMailbox('a@b.com') // dedupe
    expect(useWorkingSetStore.getState().scope.mailboxes).toEqual(['a@b.com'])

    useWorkingSetStore.getState().addSender('s@t.com')
    expect(useWorkingSetStore.getState().scope.senders).toEqual(['s@t.com'])

    useWorkingSetStore.getState().removeMailbox('a@b.com')
    expect(useWorkingSetStore.getState().scope.mailboxes).toBeUndefined()

    useWorkingSetStore.getState().removeSender('s@t.com')
    expect(useWorkingSetStore.getState().scope.senders).toBeUndefined()
  })

  it('clearScope empties constraints', () => {
    useWorkingSetStore.getState().setScopeDate({ from: '2014-01-01' })
    useWorkingSetStore.getState().addMailbox('x@y.com')
    useWorkingSetStore.getState().clearScope()
    expect(useWorkingSetStore.getState().scope).toEqual({})
    expect(useWorkingSetStore.getState().historyIntent).toBe('analytical')
  })

  it('setView and setAggregation are analytical', () => {
    useWorkingSetStore.getState().setView('table')
    expect(useWorkingSetStore.getState().view).toBe('table')
    expect(useWorkingSetStore.getState().historyIntent).toBe('analytical')

    useWorkingSetStore.getState().setAggregation('week')
    expect(useWorkingSetStore.getState().aggregation).toBe('week')
  })

  it('hydrate restores decoded fields and clears brush silently', () => {
    useWorkingSetStore.getState().setBrush({ fromMs: 1, toMs: 2 })
    useWorkingSetStore.getState().hydrate({
      scope: { mailboxes: ['m@x.com'] },
      viewport: { fromMs: 10, toMs: 20 },
      aggregation: 'year',
      view: 'table',
    })
    const s = useWorkingSetStore.getState()
    expect(s.scope).toEqual({ mailboxes: ['m@x.com'] })
    expect(s.viewport).toEqual({ fromMs: 10, toMs: 20 })
    expect(s.aggregation).toBe('year')
    expect(s.view).toBe('table')
    expect(s.brush).toBeNull()
    expect(s.historyIntent).toBe('silent')
  })
})
