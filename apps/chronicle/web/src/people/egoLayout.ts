/**
 * Pure radial layout for the depth-1 ego graph.
 * Ego at origin; neighbors on a circle sorted by shared_threads (desc),
 * angle deterministic from rank. Positions in [-1, 1].
 */

export interface LayoutNeighbor {
  id: string
  shared_threads: number
}

export interface LayoutPosition {
  id: string
  x: number
  y: number
  /** Rank among neighbors (0 = highest shared_threads). Ego has rank -1. */
  rank: number
  is_ego: boolean
}

/** Minimum angular spacing (radians) expected for ≤ 50 nodes on the ring. */
export const MIN_ANGULAR_SPACING = (2 * Math.PI) / 50

/**
 * Radius of the neighbor ring as a function of node count.
 * Grows slightly with more neighbors but stays inside the unit disk.
 */
export function ringRadius(neighborCount: number): number {
  if (neighborCount <= 0) return 0
  // Keep points inside [-1,1] with a small margin for node radius in render.
  const base = 0.72
  const grow = Math.min(0.18, Math.log2(1 + neighborCount) * 0.04)
  return Math.min(0.9, base + grow)
}

/**
 * Layout ego at (0,0) and neighbors on a circle sorted by shared_threads desc
 * (tie-break: id ASC). Angle from rank is deterministic.
 */
export function layoutEgoGraph(neighbors: LayoutNeighbor[]): LayoutPosition[] {
  const sorted = [...neighbors].sort((a, b) => {
    if (b.shared_threads !== a.shared_threads) {
      return b.shared_threads - a.shared_threads
    }
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
  })

  const positions: LayoutPosition[] = [
    { id: '__ego__', x: 0, y: 0, rank: -1, is_ego: true },
  ]

  const n = sorted.length
  if (n === 0) return positions

  const r = ringRadius(n)
  // Start at top (-π/2) so rank-0 is at 12 o'clock; step clockwise.
  const start = -Math.PI / 2
  for (let i = 0; i < n; i++) {
    const angle = start + (i * 2 * Math.PI) / n
    const x = r * Math.cos(angle)
    const y = r * Math.sin(angle)
    // Clamp to [-1, 1] for safety (should already hold).
    positions.push({
      id: sorted[i]!.id,
      x: Math.max(-1, Math.min(1, x)),
      y: Math.max(-1, Math.min(1, y)),
      rank: i,
      is_ego: false,
    })
  }
  return positions
}

/** Angular spacing between adjacent neighbors (0 when fewer than 2). */
export function neighborAngularSpacing(neighborCount: number): number {
  if (neighborCount < 2) return 0
  return (2 * Math.PI) / neighborCount
}
