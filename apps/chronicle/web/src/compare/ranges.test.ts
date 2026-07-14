import { describe, expect, it } from 'vitest'

import {
  COMPARE_EVENT_DELTA_MS,
  compareRangesAroundEvent,
  compareRangesFromEntry,
  formatPercentDelta,
  percentDelta,
} from './ranges'

const viewport = {
  fromMs: Date.UTC(2015, 0, 1),
  toMs: Date.UTC(2016, 0, 1),
}

describe('compareRangesFromEntry', () => {
  it('with brush: A = viewport, B = brush', () => {
    const brush = {
      fromMs: Date.UTC(2014, 0, 1),
      toMs: Date.UTC(2014, 6, 1),
    }
    const { a, b } = compareRangesFromEntry(viewport, brush)
    expect(a).toEqual(viewport)
    expect(b).toEqual(brush)
  })

  it('without brush: B = previous period (shifted back by A duration)', () => {
    const { a, b } = compareRangesFromEntry(viewport, null)
    expect(a).toEqual(viewport)
    const duration = viewport.toMs - viewport.fromMs
    expect(b).toEqual({
      fromMs: viewport.fromMs - duration,
      toMs: viewport.fromMs,
    })
  })

  it('ignores invalid brush (to <= from)', () => {
    const { b } = compareRangesFromEntry(viewport, {
      fromMs: 10,
      toMs: 10,
    })
    const duration = viewport.toMs - viewport.fromMs
    expect(b.toMs - b.fromMs).toBe(duration)
  })
})

describe('compareRangesAroundEvent', () => {
  it('anchors A before and B after with ±90 days', () => {
    const start = Date.UTC(2015, 5, 15)
    const { a, b } = compareRangesAroundEvent(start)
    expect(a.toMs).toBe(start)
    expect(a.fromMs).toBe(start - COMPARE_EVENT_DELTA_MS)
    expect(b.fromMs).toBe(start)
    expect(b.toMs).toBe(start + COMPARE_EVENT_DELTA_MS)
    expect(COMPARE_EVENT_DELTA_MS).toBe(90 * 24 * 60 * 60 * 1000)
  })
})

describe('percentDelta', () => {
  it('computes B vs A percent change', () => {
    expect(percentDelta(100, 138)).toBeCloseTo(38)
    expect(percentDelta(100, 50)).toBeCloseTo(-50)
    expect(percentDelta(0, 0)).toBe(0)
    expect(percentDelta(0, 10)).toBeNull()
  })

  it('formatPercentDelta signs', () => {
    expect(formatPercentDelta(100, 138)).toBe('+38%')
    expect(formatPercentDelta(100, 50)).toBe('-50%')
    expect(formatPercentDelta(0, 5)).toBe('n/a')
  })
})
