import type { LaneData } from '../api/types'
import { isBucketSeries } from '../api/types'
import type { LaneSpec } from '../chronicle/laneModel'
import { formatPeriodLabel, type Viewport } from '../chronicle/timeScale'
import { formatCompareDurationLabel } from './ranges'

export interface CompareTableProps {
  a: Viewport
  b: Viewport
  unit: string
  lanes: LaneSpec[]
  laneDataA: Record<string, LaneData>
  laneDataB: Record<string, LaneData>
}

type SideBySideRow = {
  bucketA: string | null
  bucketB: string | null
  countsA: Record<string, number>
  countsB: Record<string, number>
}

function bucketLabel(iso: string | null): string {
  if (!iso) return '—'
  const ms = Date.parse(iso)
  if (!Number.isFinite(ms)) return iso
  return formatPeriodLabel(ms)
}

/**
 * Accessible table alternative: both ranges' buckets side-by-side per lane.
 */
export function CompareTable({
  a,
  b,
  unit,
  lanes,
  laneDataA,
  laneDataB,
}: CompareTableProps) {
  const barLanes = lanes.filter((s) => s.kind === 'bars')

  // Union of bucket timestamps (ordinal index alignment for small multiples).
  const bucketsA = new Set<string>()
  const bucketsB = new Set<string>()
  for (const spec of barLanes) {
    const sa = laneDataA[spec.key]
    const sb = laneDataB[spec.key]
    if (isBucketSeries(sa)) for (const p of sa) bucketsA.add(p.bucket)
    if (isBucketSeries(sb)) for (const p of sb) bucketsB.add(p.bucket)
  }
  const listA = [...bucketsA].sort()
  const listB = [...bucketsB].sort()
  const n = Math.max(listA.length, listB.length)

  const countAt = (
    data: Record<string, LaneData>,
    key: string,
    bucket: string | null,
  ): number => {
    if (!bucket) return 0
    const series = data[key]
    if (!isBucketSeries(series)) return 0
    return series.find((p) => p.bucket === bucket)?.count ?? 0
  }

  const rows: SideBySideRow[] = []
  for (let i = 0; i < n; i++) {
    const bucketA = listA[i] ?? null
    const bucketB = listB[i] ?? null
    const countsA: Record<string, number> = {}
    const countsB: Record<string, number> = {}
    for (const spec of barLanes) {
      countsA[spec.key] = countAt(laneDataA, spec.key, bucketA)
      countsB[spec.key] = countAt(laneDataB, spec.key, bucketB)
    }
    rows.push({ bucketA, bucketB, countsA, countsB })
  }

  const caption = `Comparison: ${formatCompareDurationLabel(a)} vs ${formatCompareDurationLabel(b)} · ${unit} buckets`

  return (
    <div
      className="overflow-auto rounded-lg border border-steel bg-graphite-900"
      data-testid="compare-table"
    >
      <table className="w-full border-collapse text-left text-sm">
        <caption className="border-b border-steel px-3 py-2 text-left text-[11px] text-text-muted">
          {caption}
        </caption>
        <thead>
          <tr className="border-b border-steel text-[11px] text-text-muted">
            <th scope="col" className="px-2 py-1.5 font-medium">
              A bucket
            </th>
            {barLanes.map((spec) => (
              <th key={`a-${spec.key}`} scope="col" className="px-2 py-1.5 font-medium">
                A {spec.label}
              </th>
            ))}
            <th scope="col" className="px-2 py-1.5 font-medium">
              B bucket
            </th>
            {barLanes.map((spec) => (
              <th key={`b-${spec.key}`} scope="col" className="px-2 py-1.5 font-medium">
                B {spec.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular-nums text-text-primary">
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={2 + barLanes.length * 2}
                className="px-2 py-3 text-text-muted"
              >
                No buckets in either range
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={i} className="border-b border-steel/60">
                <th
                  scope="row"
                  className="px-2 py-1 font-sans font-normal text-text-muted"
                >
                  {bucketLabel(row.bucketA)}
                </th>
                {barLanes.map((spec) => (
                  <td key={`a-${spec.key}-${i}`} className="px-2 py-1">
                    {row.countsA[spec.key] ?? 0}
                  </td>
                ))}
                <td className="px-2 py-1 text-text-muted">
                  {bucketLabel(row.bucketB)}
                </td>
                {barLanes.map((spec) => (
                  <td key={`b-${spec.key}-${i}`} className="px-2 py-1">
                    {row.countsB[spec.key] ?? 0}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
