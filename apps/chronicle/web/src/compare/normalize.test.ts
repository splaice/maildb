import { describe, expect, it } from 'vitest'

import { normalizeLaneData, normalizePoints, sumBucketCounts } from './normalize'

describe('normalizePoints', () => {
  it('divides each count by the range total', () => {
    const points = [
      { bucket: 'a', count: 25 },
      { bucket: 'b', count: 75 },
    ]
    const out = normalizePoints(points, 100)
    expect(out[0]!.count).toBeCloseTo(0.25)
    expect(out[1]!.count).toBeCloseTo(0.75)
    expect(out[0]!.bucket).toBe('a')
  })

  it('zeros counts when total is 0', () => {
    const out = normalizePoints([{ bucket: 'a', count: 5 }], 0)
    expect(out[0]!.count).toBe(0)
  })

  it('does not mutate inputs', () => {
    const points = [{ bucket: 'a', count: 10 }]
    normalizePoints(points, 10)
    expect(points[0]!.count).toBe(10)
  })
})

describe('normalizeLaneData', () => {
  it('normalizes bars and top_people per series total', () => {
    const data = normalizeLaneData({
      messages: [
        { bucket: '2015-01-01T00:00:00Z', count: 40 },
        { bucket: '2015-02-01T00:00:00Z', count: 60 },
      ],
      top_people: {
        contacts: [
          {
            contact_id: 'c1',
            display_name: 'Alice',
            buckets: [
              { bucket: '2015-01-01T00:00:00Z', count: 10 },
              { bucket: '2015-02-01T00:00:00Z', count: 30 },
            ],
          },
        ],
      },
      events: { events: [], truncated: false },
    })
    const msgs = data.messages as { count: number }[]
    expect(msgs[0]!.count).toBeCloseTo(0.4)
    expect(msgs[1]!.count).toBeCloseTo(0.6)
    const tp = data.top_people as {
      contacts: { buckets: { count: number }[] }[]
    }
    expect(tp.contacts[0]!.buckets[0]!.count).toBeCloseTo(0.25)
    expect(tp.contacts[0]!.buckets[1]!.count).toBeCloseTo(0.75)
    // events pass through
    expect(data.events).toEqual({ events: [], truncated: false })
  })

  it('sumBucketCounts', () => {
    expect(sumBucketCounts([{ bucket: 'x', count: 1 }, { bucket: 'y', count: 2 }])).toBe(
      3,
    )
    expect(sumBucketCounts(undefined)).toBe(0)
  })
})
