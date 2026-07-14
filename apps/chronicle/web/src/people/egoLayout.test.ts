import { describe, expect, it } from 'vitest'

import {
  layoutEgoGraph,
  MIN_ANGULAR_SPACING,
  neighborAngularSpacing,
  ringRadius,
  type LayoutNeighbor,
} from './egoLayout'

function neighbors(n: number, sharedBase = 10): LayoutNeighbor[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `n${String(i).padStart(2, '0')}`,
    shared_threads: sharedBase - (i % 5),
  }))
}

describe('egoLayout', () => {
  it('is deterministic for the same input', () => {
    const input = neighbors(12)
    const a = layoutEgoGraph(input)
    const b = layoutEgoGraph(input)
    expect(a).toEqual(b)
  })

  it('places ego at origin and neighbors in [-1,1]', () => {
    const positions = layoutEgoGraph(neighbors(20))
    const ego = positions.find((p) => p.is_ego)
    expect(ego).toMatchObject({ x: 0, y: 0, rank: -1 })
    for (const p of positions) {
      expect(p.x).toBeGreaterThanOrEqual(-1)
      expect(p.x).toBeLessThanOrEqual(1)
      expect(p.y).toBeGreaterThanOrEqual(-1)
      expect(p.y).toBeLessThanOrEqual(1)
    }
  })

  it('sorts neighbors by shared_threads desc then id', () => {
    const input: LayoutNeighbor[] = [
      { id: 'b', shared_threads: 5 },
      { id: 'a', shared_threads: 10 },
      { id: 'c', shared_threads: 10 },
    ]
    const positions = layoutEgoGraph(input)
    const ranks = positions.filter((p) => !p.is_ego).sort((x, y) => x.rank - y.rank)
    expect(ranks.map((p) => p.id)).toEqual(['a', 'c', 'b'])
  })

  it('uses equal angular spacing; no overlaps at ≤ 50 nodes beyond min spacing', () => {
    for (const count of [2, 12, 25, 50]) {
      const spacing = neighborAngularSpacing(count)
      expect(spacing).toBeGreaterThanOrEqual(MIN_ANGULAR_SPACING - 1e-12)
      const positions = layoutEgoGraph(neighbors(count))
      const ring = positions.filter((p) => !p.is_ego)
      // Adjacent ranks: chord length should be positive and match equal angles.
      const r = ringRadius(count)
      const expectedChord = 2 * r * Math.sin(spacing / 2)
      for (let i = 0; i < ring.length; i++) {
        const a = ring.find((p) => p.rank === i)!
        const b = ring.find((p) => p.rank === (i + 1) % ring.length)!
        const dx = a.x - b.x
        const dy = a.y - b.y
        const dist = Math.hypot(dx, dy)
        expect(dist).toBeGreaterThan(0)
        expect(Math.abs(dist - expectedChord)).toBeLessThan(1e-9)
      }
    }
  })

  it('empty neighbors yields only ego', () => {
    const positions = layoutEgoGraph([])
    expect(positions).toHaveLength(1)
    expect(positions[0]?.is_ego).toBe(true)
  })
})
