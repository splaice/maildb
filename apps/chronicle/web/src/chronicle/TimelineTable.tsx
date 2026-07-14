import type { BucketPoint, LaneData } from '../api/types'
import { isBucketSeries } from '../api/types'
import type { LaneSpec, MultirowSeries } from './laneModel'
import { multirowSeriesForLane } from './laneModel'
import { formatPeriodLabel, formatPeriodRange, type Viewport } from './timeScale'

export interface TimelineTableProps {
  viewport: Viewport
  unit: string
  /** Ordered visible lane specs (store order). */
  lanes: LaneSpec[]
  laneData: Record<string, LaneData>
}

type MergedRow = {
  bucket: string
  /** bars lane key → count */
  bars: Record<string, number>
  /** multirow series id → count (top_people contacts / topics) */
  multi: Record<string, number>
}

/** Collect multirow columns from all multirow lanes in catalog order. */
export function multirowColumns(
  lanes: LaneSpec[],
  laneData: Record<string, LaneData>,
): { laneKey: string; series: MultirowSeries }[] {
  const out: { laneKey: string; series: MultirowSeries }[] = []
  for (const spec of lanes) {
    if (spec.kind !== 'multirow') continue
    for (const s of multirowSeriesForLane(spec, laneData)) {
      out.push({ laneKey: spec.key, series: s })
    }
  }
  return out
}

/** Collect contact columns from top_people (stable order from server). */
export function topPeopleContacts(
  laneData: Record<string, LaneData>,
): MultirowSeries[] {
  return multirowSeriesForLane(
    { key: 'top_people', label: 'Top people', kind: 'multirow' },
    laneData,
  )
}

/** Merge all bar lanes + multirow per-series counts by bucket timestamp. */
export function mergeBucketRows(
  lanes: LaneSpec[],
  laneData: Record<string, LaneData>,
): MergedRow[] {
  const map = new Map<string, MergedRow>()

  const ensure = (bucket: string): MergedRow => {
    let row = map.get(bucket)
    if (!row) {
      row = { bucket, bars: {}, multi: {} }
      map.set(bucket, row)
    }
    return row
  }

  for (const spec of lanes) {
    if (spec.kind === 'bars') {
      const points = laneData[spec.key]
      if (!isBucketSeries(points)) continue
      for (const p of points) {
        ensure(p.bucket).bars[spec.key] = p.count
      }
    } else if (spec.kind === 'multirow') {
      const series = multirowSeriesForLane(spec, laneData)
      for (const s of series) {
        for (const p of s.buckets) {
          // Prefix id with lane key to avoid contact/topic id collisions.
          ensure(p.bucket).multi[`${spec.key}:${s.id}`] = p.count
        }
      }
    }
  }

  return [...map.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, row]) => row)
}

function bucketLabel(iso: string): string {
  const ms = Date.parse(iso)
  if (!Number.isFinite(ms)) return iso
  return formatPeriodLabel(ms)
}

/**
 * Accessible table alternative for the timeline canvas (A11Y-002).
 * One row per bucket; columns follow configured lanes. For multirow lanes
 * (top_people / topics), one column per series (series × bucket counts).
 */
export function TimelineTable({
  viewport,
  unit,
  lanes,
  laneData,
}: TimelineTableProps) {
  const rows = mergeBucketRows(lanes, laneData)
  const multiCols = multirowColumns(lanes, laneData)
  const barLanes = lanes.filter((s) => s.kind === 'bars')
  const colCount = 1 + barLanes.length + multiCols.length
  const caption = `Timeline buckets (${unit}), ${formatPeriodRange(viewport)}`

  return (
    <div className="max-h-96 overflow-auto rounded-lg border border-steel bg-graphite-900">
      <table className="w-full border-collapse text-left" data-testid="timeline-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-steel text-text-muted">
            <th scope="col" className="px-3 py-2 font-sans font-medium">
              Bucket
            </th>
            {barLanes.map((spec) => (
              <th
                key={spec.key}
                scope="col"
                className="px-3 py-2 font-sans font-medium"
                data-lane-col={spec.key}
              >
                {spec.label}
              </th>
            ))}
            {multiCols.map(({ laneKey, series: s }) => (
              <th
                key={`${laneKey}:${s.id}`}
                scope="col"
                className="px-3 py-2 font-sans font-medium"
                data-contact-col={laneKey === 'top_people' ? s.id : undefined}
                data-topic-col={laneKey === 'topics' ? s.id : undefined}
                title={s.label}
              >
                {s.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular-nums font-mono text-text-primary">
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={Math.max(1, colCount)}
                className="px-3 py-4 text-center font-sans text-text-muted"
              >
                No activity in range
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={row.bucket} className="border-b border-steel">
                <th
                  scope="row"
                  className="px-3 py-1.5 text-left font-sans font-normal text-text-muted"
                >
                  {bucketLabel(row.bucket)}
                </th>
                {barLanes.map((spec) => (
                  <td key={spec.key} className="px-3 py-1.5" data-lane-cell={spec.key}>
                    {(row.bars[spec.key] ?? 0).toLocaleString()}
                  </td>
                ))}
                {multiCols.map(({ laneKey, series: s }) => (
                  <td
                    key={`${laneKey}:${s.id}`}
                    className="px-3 py-1.5"
                    data-contact-cell={laneKey === 'top_people' ? s.id : undefined}
                    data-topic-cell={laneKey === 'topics' ? s.id : undefined}
                  >
                    {(row.multi[`${laneKey}:${s.id}`] ?? 0).toLocaleString()}
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

/** Back-compat helper used by older tests that pass messages/attachments arrays. */
export function mergeMessagesAttachments(
  messages: BucketPoint[],
  attachments: BucketPoint[],
): { bucket: string; messages: number; attachments: number }[] {
  const map = new Map<string, { messages: number; attachments: number }>()
  for (const b of messages) {
    const row = map.get(b.bucket) ?? { messages: 0, attachments: 0 }
    row.messages = b.count
    map.set(b.bucket, row)
  }
  for (const b of attachments) {
    const row = map.get(b.bucket) ?? { messages: 0, attachments: 0 }
    row.attachments = b.count
    map.set(b.bucket, row)
  }
  return [...map.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([bucket, counts]) => ({ bucket, ...counts }))
}
