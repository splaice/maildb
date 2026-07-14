import { describe, expect, it } from 'vitest'

import type { Unit } from '../chronicle/timeScale'
import {
  decodeState,
  DEFAULT_URL_STATE,
  encodeState,
  isScopePristine,
  toIsoSeconds,
  type UrlWorkingState,
} from './urlState'

describe('encodeState / decodeState', () => {
  it('pristine state encodes to empty query string', () => {
    const params = encodeState(DEFAULT_URL_STATE)
    expect(params.toString()).toBe('')
  })

  it('roundtrips full state including unicode addresses', () => {
    const state: UrlWorkingState = {
      scope: {
        date: { from: '2014-01-01', to: '2018-12-31' },
        mailboxes: ['personal@exämple.com', 'work@foo.com'],
        senders: ['alice@x.com', '佐藤@例.jp'],
      },
      viewport: {
        fromMs: Date.UTC(2014, 0, 1, 0, 0, 0),
        toMs: Date.UTC(2015, 5, 15, 12, 30, 45),
      },
      aggregation: 'month',
      view: 'table',
    }

    const encoded = encodeState(state)
    const decoded = decodeState(encoded)

    expect(decoded.scope.date).toEqual(state.scope.date)
    expect(decoded.scope.mailboxes).toEqual(state.scope.mailboxes)
    expect(decoded.scope.senders).toEqual(state.scope.senders)
    expect(decoded.viewport?.fromMs).toBe(state.viewport!.fromMs)
    expect(decoded.viewport?.toMs).toBe(state.viewport!.toMs)
    expect(decoded.aggregation).toBe('month')
    expect(decoded.view).toBe('table')

    // Second roundtrip is stable
    expect(decodeState(encodeState(decoded))).toEqual(decoded)
  })

  it('uses second-precision viewport ISO (no milliseconds)', () => {
    const state: UrlWorkingState = {
      ...DEFAULT_URL_STATE,
      viewport: {
        fromMs: Date.UTC(2014, 0, 1, 0, 0, 0),
        toMs: Date.UTC(2014, 0, 2, 0, 0, 0),
      },
    }
    const params = encodeState(state)
    expect(params.get('vf')).toBe('2014-01-01T00:00:00Z')
    expect(params.get('vt')).toBe('2014-01-02T00:00:00Z')
    expect(params.get('vf')).not.toMatch(/\.\d{3}/)
  })

  it('omits canvas view and auto aggregation', () => {
    const params = encodeState({
      ...DEFAULT_URL_STATE,
      scope: { mailboxes: ['a@b.com'] },
      aggregation: 'auto',
      view: 'canvas',
    })
    expect(params.has('view')).toBe(false)
    expect(params.has('agg')).toBe(false)
    expect(params.get('mb')).toBe('a@b.com')
  })

  it('bad params fall back to defaults without throwing', () => {
    const params = new URLSearchParams({
      df: 'not-a-date',
      dt: '99-99-99',
      vf: 'garbage',
      vt: 'also-bad',
      agg: 'fortnight',
      view: 'map',
      mb: '',
      sd: ',,,',
    })
    const decoded = decodeState(params)
    expect(decoded).toEqual(DEFAULT_URL_STATE)
  })

  it('rejects inverted or partial viewport', () => {
    const inverted = decodeState(
      new URLSearchParams({
        vf: '2015-01-01T00:00:00Z',
        vt: '2014-01-01T00:00:00Z',
      }),
    )
    expect(inverted.viewport).toBeNull()

    const partial = decodeState(
      new URLSearchParams({ vf: '2014-01-01T00:00:00Z' }),
    )
    expect(partial.viewport).toBeNull()
  })

  it('accepts known aggregation units', () => {
    const units: Unit[] = ['hour', 'day', 'week', 'month', 'quarter', 'year']
    for (const u of units) {
      expect(decodeState(new URLSearchParams({ agg: u })).aggregation).toBe(u)
    }
  })

  it('toIsoSeconds strips milliseconds', () => {
    expect(toIsoSeconds(Date.UTC(2014, 0, 1, 0, 0, 0, 123))).toBe(
      '2014-01-01T00:00:00Z',
    )
  })
})

describe('isScopePristine', () => {
  it('is true for empty scope', () => {
    expect(isScopePristine({})).toBe(true)
  })

  it('is false when any constraint is set', () => {
    expect(isScopePristine({ date: { from: '2014-01-01' } })).toBe(false)
    expect(isScopePristine({ mailboxes: ['a@b.com'] })).toBe(false)
    expect(isScopePristine({ senders: ['x@y.com'] })).toBe(false)
  })
})
