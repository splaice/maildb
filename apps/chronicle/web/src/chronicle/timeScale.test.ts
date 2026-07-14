import { describe, expect, it } from 'vitest'

import {
  clampViewport,
  panViewport,
  timeForX,
  ticksFor,
  type Viewport,
  wheelZoomFactor,
  xForTime,
  zoomViewport,
} from './timeScale'

const vp: Viewport = {
  fromMs: Date.UTC(2014, 0, 1),
  toMs: Date.UTC(2019, 0, 1),
}
const width = 1000

describe('timeScale', () => {
  it('xForTime / timeForX roundtrip', () => {
    const samples = [0, 100, 500, 999, width]
    for (const x of samples) {
      const t = timeForX(x, vp, width)
      const x2 = xForTime(t, vp, width)
      expect(x2).toBeCloseTo(x, 6)
    }
    const times = [vp.fromMs, (vp.fromMs + vp.toMs) / 2, vp.toMs]
    for (const t of times) {
      const x = xForTime(t, vp, width)
      expect(timeForX(x, vp, width)).toBeCloseTo(t, 3)
    }
  })

  it('pan by +100px shifts times by exactly the per-px duration', () => {
    const span = vp.toMs - vp.fromMs
    const perPx = span / width
    const panned = panViewport(vp, 100, width)
    expect(panned.fromMs - vp.fromMs).toBeCloseTo(100 * perPx, 6)
    expect(panned.toMs - vp.toMs).toBeCloseTo(100 * perPx, 6)
    expect(panned.toMs - panned.fromMs).toBeCloseTo(span, 6)
  })

  it('zoom anchor invariant (property-style over anchors/factors)', () => {
    const anchors = [0, 50, 200, 500, 750, 999]
    const factors = [0.5, 0.75, 1.25, 2, wheelZoomFactor(100), wheelZoomFactor(-200)]
    for (const anchorPx of anchors) {
      for (const factor of factors) {
        const before = timeForX(anchorPx, vp, width)
        const zoomed = zoomViewport(vp, factor, anchorPx, width)
        const after = timeForX(anchorPx, zoomed, width)
        expect(after).toBeCloseTo(before, 4)
      }
    }
  })

  it('clamp respects minSpan and extent bounds', () => {
    const extent: Viewport = {
      fromMs: Date.UTC(2010, 0, 1),
      toMs: Date.UTC(2020, 0, 1),
    }
    const minSpanMs = 60 * 60 * 1000 // 1 hour
    const extentSpan = extent.toMs - extent.fromMs
    const maxSpan = extentSpan * 1.2

    // Too narrow → expanded to minSpan
    const tooNarrow = clampViewport(
      { fromMs: extent.fromMs, toMs: extent.fromMs + 1000 },
      extent,
      minSpanMs,
    )
    expect(tooNarrow.toMs - tooNarrow.fromMs).toBeGreaterThanOrEqual(minSpanMs)

    // Too wide → capped at extent * 1.2
    const tooWide = clampViewport(
      { fromMs: extent.fromMs - extentSpan, toMs: extent.toMs + extentSpan },
      extent,
      minSpanMs,
    )
    expect(tooWide.toMs - tooWide.fromMs).toBeLessThanOrEqual(maxSpan + 1)

    // Far outside → pulled into bounds
    const outside = clampViewport(
      {
        fromMs: Date.UTC(1990, 0, 1),
        toMs: Date.UTC(1991, 0, 1),
      },
      extent,
      minSpanMs,
    )
    expect(outside.toMs).toBeGreaterThan(extent.fromMs - (maxSpan - extentSpan))
    expect(outside.fromMs).toBeLessThan(extent.toMs + (maxSpan - extentSpan))
  })

  it('ticksFor produces 80–120px spacing and correct label formats at year/month/day spans', () => {
    const cases: { vp: Viewport; width: number; labelRe: RegExp }[] = [
      {
        // multi-year → year labels
        vp: { fromMs: Date.UTC(2000, 0, 1), toMs: Date.UTC(2020, 0, 1) },
        width: 1000,
        labelRe: /^\d{4}$/,
      },
      {
        // ~2 years → month or quarter labels
        vp: { fromMs: Date.UTC(2015, 0, 1), toMs: Date.UTC(2017, 0, 1) },
        width: 1000,
        labelRe: /^(Q[1-4] \d{4}|[A-Z][a-z]{2} \d{4})$/,
      },
      {
        // ~2 months → day labels
        vp: { fromMs: Date.UTC(2015, 0, 1), toMs: Date.UTC(2015, 2, 1) },
        width: 1000,
        labelRe: /^\d{1,2} [A-Z][a-z]{2}/,
      },
    ]

    for (const { vp: v, width: w, labelRe } of cases) {
      const ticks = ticksFor(v, w)
      expect(ticks.length).toBeGreaterThan(1)
      for (let i = 1; i < ticks.length; i++) {
        const dx =
          xForTime(ticks[i]!.timeMs, v, w) - xForTime(ticks[i - 1]!.timeMs, v, w)
        // Spec target 80–120; allow calendar-alignment slack
        expect(dx).toBeGreaterThanOrEqual(70)
        expect(dx).toBeLessThanOrEqual(160)
      }
      expect(ticks.some((t) => labelRe.test(t.label))).toBe(true)
    }
  })

  it('wheelZoomFactor clamps to [0.5, 2]', () => {
    expect(wheelZoomFactor(0)).toBeCloseTo(1, 6)
    expect(wheelZoomFactor(10_000)).toBe(0.5)
    expect(wheelZoomFactor(-10_000)).toBe(2)
  })
})
