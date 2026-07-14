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
      selection: { kind: 'message', sid: 'msg_1' },
      lanes: ['people', 'messages'],
    })
    const s = useWorkingSetStore.getState()
    expect(s.scope).toEqual({ mailboxes: ['m@x.com'] })
    expect(s.viewport).toEqual({ fromMs: 10, toMs: 20 })
    expect(s.aggregation).toBe('year')
    expect(s.view).toBe('table')
    expect(s.selection).toEqual({ kind: 'message', sid: 'msg_1' })
    expect(s.lanes).toEqual(['people', 'messages'])
    expect(s.brush).toBeNull()
    expect(s.historyIntent).toBe('silent')
  })

  it('setSelection is transient', () => {
    useWorkingSetStore.getState().setSelection({
      kind: 'bucket',
      lane: 'messages',
      bucketIso: '2014-01-01T00:00:00.000Z',
    })
    const s = useWorkingSetStore.getState()
    expect(s.selection?.kind).toBe('bucket')
    expect(s.historyIntent).toBe('transient')

    useWorkingSetStore.getState().setSelection(null)
    expect(useWorkingSetStore.getState().selection).toBeNull()
  })

  it('toggleLane and moveLane are analytical', () => {
    // default: messages, attachments, top_people
    useWorkingSetStore.getState().toggleLane('people')
    expect(useWorkingSetStore.getState().lanes).toContain('people')
    expect(useWorkingSetStore.getState().historyIntent).toBe('analytical')

    useWorkingSetStore.getState().moveLane('people', 'up')
    const afterUp = useWorkingSetStore.getState().lanes
    expect(afterUp.indexOf('people')).toBeLessThan(afterUp.length - 1)

    useWorkingSetStore.getState().toggleLane('people')
    expect(useWorkingSetStore.getState().lanes).not.toContain('people')
  })

  it('toggleLane refuses to hide the last lane', () => {
    useWorkingSetStore.setState({ lanes: ['messages'] })
    useWorkingSetStore.getState().toggleLane('messages')
    expect(useWorkingSetStore.getState().lanes).toEqual(['messages'])
  })

  it('setFocus is analytical and clears brush', () => {
    useWorkingSetStore.getState().setBrush({ fromMs: 1, toMs: 2 })
    useWorkingSetStore.getState().setFocus({
      fromMs: Date.UTC(2015, 0, 1),
      toMs: Date.UTC(2016, 0, 1),
    })
    const s = useWorkingSetStore.getState()
    expect(s.focus).toEqual({
      fromMs: Date.UTC(2015, 0, 1),
      toMs: Date.UTC(2016, 0, 1),
    })
    expect(s.brush).toBeNull()
    expect(s.historyIntent).toBe('analytical')
  })

  it('exitFocus clears focus analytically', () => {
    useWorkingSetStore.getState().setFocus({
      fromMs: Date.UTC(2015, 0, 1),
      toMs: Date.UTC(2016, 0, 1),
    })
    useWorkingSetStore.getState().exitFocus()
    expect(useWorkingSetStore.getState().focus).toBeNull()
    expect(useWorkingSetStore.getState().historyIntent).toBe('analytical')
  })

  it('exitFocus is a no-op when not in focus', () => {
    useWorkingSetStore.getState().setView('table')
    useWorkingSetStore.getState().exitFocus()
    expect(useWorkingSetStore.getState().focus).toBeNull()
    expect(useWorkingSetStore.getState().view).toBe('table')
  })

  it('applyFocusAsScopeDate writes scope date and exits focus', () => {
    useWorkingSetStore.getState().addMailbox('me@x.com')
    useWorkingSetStore.getState().setFocus({
      fromMs: Date.UTC(2014, 5, 15, 12, 0, 0),
      toMs: Date.UTC(2015, 2, 1, 0, 0, 0),
    })
    useWorkingSetStore.getState().applyFocusAsScopeDate()
    const s = useWorkingSetStore.getState()
    expect(s.focus).toBeNull()
    expect(s.scope.date).toEqual({ from: '2014-06-15', to: '2015-03-01' })
    expect(s.scope.mailboxes).toEqual(['me@x.com'])
    expect(s.historyIntent).toBe('analytical')
  })

  it('hydrate restores focus silently', () => {
    useWorkingSetStore.getState().hydrate({
      scope: {},
      viewport: null,
      aggregation: 'auto',
      view: 'canvas',
      selection: null,
      lanes: null,
      focus: { fromMs: 10, toMs: 20 },
    })
    expect(useWorkingSetStore.getState().focus).toEqual({ fromMs: 10, toMs: 20 })
    expect(useWorkingSetStore.getState().historyIntent).toBe('silent')
  })
})
