/**
 * Unit tests for double-click focus entry vs Alt+double-click zoom
 * (canvas handler policy — resolveDoubleClickAction).
 */
import { describe, expect, it } from 'vitest'

import { resolveDoubleClickAction } from './TimelineCanvas'
import type { Viewport } from './timeScale'

const vp: Viewport = {
  fromMs: Date.UTC(2014, 0, 1),
  toMs: Date.UTC(2019, 0, 1),
}

describe('double-click focus vs Alt+zoom', () => {
  it('Alt+double-click retains zoom (does not enter focus)', () => {
    const bucket: Viewport = {
      fromMs: Date.UTC(2015, 0, 1),
      toMs: Date.UTC(2015, 1, 1),
    }
    const result = resolveDoubleClickAction({
      altKey: true,
      bucketHit: bucket,
      viewport: vp,
      plotX: 100,
      plotW: 800,
    })
    expect(result.kind).toBe('zoom')
    if (result.kind === 'zoom') {
      const span = result.viewport.toMs - result.viewport.fromMs
      const origSpan = vp.toMs - vp.fromMs
      expect(span).toBeCloseTo(origSpan * 0.25, -2)
    }
  })

  it('plain double-click with bucket hit enters focus', () => {
    const bucket: Viewport = {
      fromMs: Date.UTC(2015, 0, 1),
      toMs: Date.UTC(2015, 1, 1),
    }
    const result = resolveDoubleClickAction({
      altKey: false,
      bucketHit: bucket,
      viewport: vp,
      plotX: 100,
      plotW: 800,
    })
    expect(result).toEqual({ kind: 'focus', period: bucket })
  })

  it('plain double-click without hit falls back to zoom', () => {
    const result = resolveDoubleClickAction({
      altKey: false,
      bucketHit: null,
      viewport: vp,
      plotX: 100,
      plotW: 800,
    })
    expect(result.kind).toBe('zoom')
  })
})
