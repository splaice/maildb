import type { BucketPoint, LaneData } from '../api/types'
import { isBucketSeries, isTopPeopleLane } from '../api/types'

/**
 * Divide each point's count by *total* (range total for that lane).
 * When total ≤ 0, counts become 0. Pure helper for Absolute | Normalized.
 */
export function normalizePoints(
  points: BucketPoint[],
  total: number,
): BucketPoint[] {
  if (total <= 0) {
    return points.map((p) => ({ bucket: p.bucket, count: 0 }))
  }
  return points.map((p) => ({
    bucket: p.bucket,
    count: p.count / total,
  }))
}

/** Sum of bucket counts. */
export function sumBucketCounts(points: BucketPoint[] | undefined): number {
  if (!points) return 0
  return points.reduce((acc, p) => acc + (p.count ?? 0), 0)
}

/**
 * Client-side transform: normalize each bars / top_people series by that
 * lane's range total before passing points to the canvas.
 */
export function normalizeLaneData(
  laneData: Record<string, LaneData>,
): Record<string, LaneData> {
  const out: Record<string, LaneData> = {}
  for (const [key, data] of Object.entries(laneData)) {
    if (isBucketSeries(data)) {
      out[key] = normalizePoints(data, sumBucketCounts(data))
    } else if (isTopPeopleLane(data)) {
      out[key] = {
        contacts: data.contacts.map((c) => {
          const total = sumBucketCounts(c.buckets)
          return {
            ...c,
            buckets: normalizePoints(c.buckets, total),
          }
        }),
      }
    } else {
      // events / unknown: pass through
      out[key] = data
    }
  }
  return out
}
