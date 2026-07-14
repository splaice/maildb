import { beforeEach, describe, expect, it } from 'vitest'

import type { Unit } from '../chronicle/timeScale'
import {
  decodeSelection,
  decodeState,
  DEFAULT_LANES,
  DEFAULT_URL_STATE,
  encodeSelection,
  encodeState,
  isScopePristine,
  LANES_STORAGE_KEY,
  loadSavedLanes,
  parseLanesParam,
  resolveLanes,
  saveLanesAsDefault,
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
      selection: {
        kind: 'bucket',
        lane: 'messages',
        bucketIso: '2014-06-01T00:00:00.000Z',
      },
      lanes: ['top_people', 'messages'],
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
    expect(decoded.selection).toEqual(state.selection)
    expect(decoded.lanes).toEqual(state.lanes)

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

describe('selection codec (sel)', () => {
  it('roundtrips bucket, message, and attachment selections', () => {
    const bucket = {
      kind: 'bucket' as const,
      lane: 'messages',
      bucketIso: '2014-01-01T00:00:00.000Z',
    }
    const msg = { kind: 'message' as const, sid: 'msg_12345' }
    const att = { kind: 'attachment' as const, sid: 'att_99' }
    expect(decodeSelection(encodeSelection(bucket))).toEqual(bucket)
    expect(decodeSelection(encodeSelection(msg))).toEqual(msg)
    expect(decodeSelection(encodeSelection(att))).toEqual(att)
    expect(encodeSelection(null)).toBeNull()
    expect(decodeSelection(null)).toBeNull()
  })

  it('falls back to null on malformed values', () => {
    expect(decodeSelection('')).toBeNull()
    expect(decodeSelection('x:foo')).toBeNull()
    expect(decodeSelection('b:')).toBeNull()
    expect(decodeSelection('b:messages')).toBeNull()
    expect(decodeSelection('b:messages:not-a-date')).toBeNull()
    expect(decodeSelection('m:')).toBeNull()
    expect(decodeSelection('m:bad')).toBeNull()
    expect(decodeSelection('m:att_1')).toBeNull()
    expect(decodeSelection('a:')).toBeNull()
    expect(decodeSelection('a:msg_1')).toBeNull()
  })

  it('encodes into URL params via encodeState', () => {
    const params = encodeState({
      ...DEFAULT_URL_STATE,
      selection: { kind: 'message', sid: 'msg_99' },
    })
    expect(params.get('sel')).toBe('m:msg_99')
    const attParams = encodeState({
      ...DEFAULT_URL_STATE,
      selection: { kind: 'attachment', sid: 'att_7' },
    })
    expect(attParams.get('sel')).toBe('a:att_7')
  })
})

describe('research URL params (q / mode / grp)', () => {
  it('roundtrips query, mode, and grouping', () => {
    const state: UrlWorkingState = {
      ...DEFAULT_URL_STATE,
      query: 'from:alice roof',
      mode: 'exact',
      grouping: 'thread',
    }
    const params = encodeState(state)
    expect(params.get('q')).toBe('from:alice roof')
    expect(params.get('mode')).toBe('exact')
    expect(params.get('grp')).toBe('thread')
    const decoded = decodeState(params)
    expect(decoded.query).toBe('from:alice roof')
    expect(decoded.mode).toBe('exact')
    expect(decoded.grouping).toBe('thread')
  })

  it('omits defaults hybrid / none / empty q', () => {
    const params = encodeState({
      ...DEFAULT_URL_STATE,
      query: '',
      mode: 'hybrid',
      grouping: 'none',
    })
    expect(params.has('q')).toBe(false)
    expect(params.has('mode')).toBe(false)
    expect(params.has('grp')).toBe(false)
  })
})

/** In-memory localStorage for environments where jsdom omits it. */
function installMemoryLocalStorage(): Storage {
  const map = new Map<string, string>()
  const storage: Storage = {
    get length() {
      return map.size
    },
    clear() {
      map.clear()
    },
    getItem(key: string) {
      return map.has(key) ? map.get(key)! : null
    },
    key(index: number) {
      return [...map.keys()][index] ?? null
    },
    removeItem(key: string) {
      map.delete(key)
    },
    setItem(key: string, value: string) {
      map.set(key, String(value))
    },
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
    writable: true,
  })
  return storage
}

describe('lanes codec (ln) and saved lens', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
  })

  it('roundtrips non-default ln CSV', () => {
    const state: UrlWorkingState = {
      ...DEFAULT_URL_STATE,
      lanes: ['people', 'messages'],
    }
    const params = encodeState(state)
    expect(params.get('ln')).toBe('people,messages')
    expect(decodeState(params).lanes).toEqual(['people', 'messages'])
  })

  it('omits ln when lanes equal default', () => {
    const params = encodeState({
      ...DEFAULT_URL_STATE,
      lanes: [...DEFAULT_LANES],
    })
    expect(params.has('ln')).toBe(false)
  })

  it('parseLanesParam drops unknown keys and empties', () => {
    expect(parseLanesParam(null)).toBeNull()
    expect(parseLanesParam('')).toBeNull()
    expect(parseLanesParam('nope,messages,nope')).toEqual(['messages'])
    expect(parseLanesParam('bogus')).toBeNull()
  })

  it('precedence URL > localStorage > default', () => {
    saveLanesAsDefault(['attachments', 'people'])
    expect(loadSavedLanes()).toEqual(['attachments', 'people'])

    // URL present wins
    expect(resolveLanes(['messages'])).toEqual(['messages'])
    // No URL → localStorage
    expect(resolveLanes(null)).toEqual(['attachments', 'people'])
    // No URL, no storage → default
    localStorage.removeItem(LANES_STORAGE_KEY)
    expect(resolveLanes(null)).toEqual([...DEFAULT_LANES])
  })

  it('decode without ln yields null lanes (hydrate resolves)', () => {
    expect(decodeState(new URLSearchParams()).lanes).toBeNull()
  })
})

describe('focus codec (ff/ft)', () => {
  it('roundtrips focus period with second-precision ISO', () => {
    const state: UrlWorkingState = {
      ...DEFAULT_URL_STATE,
      focus: {
        fromMs: Date.UTC(2015, 0, 1, 0, 0, 0),
        toMs: Date.UTC(2016, 5, 15, 12, 30, 0),
      },
    }
    const params = encodeState(state)
    expect(params.get('ff')).toBe('2015-01-01T00:00:00Z')
    expect(params.get('ft')).toBe('2016-06-15T12:30:00Z')
    expect(decodeState(params).focus).toEqual(state.focus)
    expect(decodeState(encodeState(decodeState(params))).focus).toEqual(state.focus)
  })

  it('omits ff/ft when focus is null', () => {
    const params = encodeState(DEFAULT_URL_STATE)
    expect(params.has('ff')).toBe(false)
    expect(params.has('ft')).toBe(false)
    expect(decodeState(params).focus).toBeNull()
  })

  it('rejects inverted or partial focus', () => {
    expect(
      decodeState(
        new URLSearchParams({
          ff: '2016-01-01T00:00:00Z',
          ft: '2015-01-01T00:00:00Z',
        }),
      ).focus,
    ).toBeNull()
    expect(
      decodeState(new URLSearchParams({ ff: '2015-01-01T00:00:00Z' })).focus,
    ).toBeNull()
  })
})
