import { describe, expect, it } from 'vitest'

import type { BucketPoint } from '../api/types'
import { bucketAtX, laneAtY } from './TimelineCanvas'
import type { Viewport } from './timeScale'

const vp: Viewport = {
  fromMs: Date.UTC(2014, 0, 1),
  toMs: Date.UTC(2015, 0, 1),
}

const points: BucketPoint[] = [
  { bucket: '2014-01-01T00:00:00.000Z', count: 10 },
  { bucket: '2014-04-01T00:00:00.000Z', count: 20 },
  { bucket: '2014-07-01T00:00:00.000Z', count: 30 },
]

describe('bucketAtX', () => {
  it('picks the bucket whose bar covers plot x', () => {
    const plotW = 1000
    // July bucket starts at mid-year-ish
    const julyStart = Date.parse('2014-07-01T00:00:00.000Z')
    const span = vp.toMs - vp.fromMs
    const x = ((julyStart - vp.fromMs) / span) * plotW + 2
    const hit = bucketAtX(x, vp, plotW, 'month', points)
    expect(hit).toBe('2014-07-01T00:00:00.000Z')
  })

  it('returns null outside bars / empty', () => {
    expect(bucketAtX(0, vp, 1000, 'month', [])).toBeNull()
    // Far left of first bucket with tiny width may still hit via time fallback
    const miss = bucketAtX(-10, vp, 1000, 'month', points)
    expect(miss).toBeNull()
  })

  it('distinguishes adjacent month buckets', () => {
    const monthPoints: BucketPoint[] = [
      { bucket: '2014-01-01T00:00:00.000Z', count: 1 },
      { bucket: '2014-02-01T00:00:00.000Z', count: 1 },
    ]
    const plotW = 1200
    const jan = Date.parse('2014-01-01T00:00:00.000Z')
    const feb = Date.parse('2014-02-01T00:00:00.000Z')
    const span = vp.toMs - vp.fromMs
    const xJan = ((jan - vp.fromMs) / span) * plotW + 1
    const xFeb = ((feb - vp.fromMs) / span) * plotW + 1
    expect(bucketAtX(xJan, vp, plotW, 'month', monthPoints)).toBe(
      '2014-01-01T00:00:00.000Z',
    )
    expect(bucketAtX(xFeb, vp, plotW, 'month', monthPoints)).toBe(
      '2014-02-01T00:00:00.000Z',
    )
  })
})

describe('laneAtY', () => {
  it('maps y into messages / attachments / null', () => {
    expect(laneAtY(10)).toBeNull() // axis
    expect(laneAtY(40)).toBe('messages')
    expect(laneAtY(120)).toBe('attachments')
  })
})
