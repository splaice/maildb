import type { BucketPoint, LaneData, TopPeopleContact } from '../api/types'
import { isBucketSeries, isTopPeopleLane } from '../api/types'
import type { LaneSpec } from './laneModel'
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
  /** contact_id → count for top_people */
  contacts: Record<string, number>
}

/** Collect contact columns from top_people (stable order from server). */
export function topPeopleContacts(
  laneData: Record<string, LaneData>,
): TopPeopleContact[] {
  const tp = laneData.top_people
  return isTopPeopleLane(tp) ? tp.contacts : []
}

/** Merge all bar lanes + top_people per-contact counts by bucket timestamp. */
export function mergeBucketRows(
  lanes: LaneSpec[],
  laneData: Record<string, LaneData>,
): MergedRow[] {
  const map = new Map<string, MergedRow>()

  const ensure = (bucket: string): MergedRow => {
    let row = map.get(bucket)
    if (!row) {
      row = { bucket, bars: {}, contacts: {} }
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
    } else if (spec.key === 'top_people') {
      const contacts = topPeopleContacts(laneData)
      for (const c of contacts) {
        for (const p of c.buckets) {
          ensure(p.bucket).contacts[c.contact_id] = p.count
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
 * One row per bucket; columns follow configured lanes. For top_people,
 * one column per contact (contact × bucket counts).
 */
export function TimelineTable({
  viewport,
  unit,
  lanes,
  laneData,
}: TimelineTableProps) {
  const rows = mergeBucketRows(lanes, laneData)
  const contacts =
    lanes.some((s) => s.key === 'top_people') ? topPeopleContacts(laneData) : []
  const barLanes = lanes.filter((s) => s.kind === 'bars')
  const colCount = 1 + barLanes.length + contacts.length
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
            {contacts.map((c) => (
              <th
                key={c.contact_id}
                scope="col"
                className="px-3 py-2 font-sans font-medium"
                data-contact-col={c.contact_id}
                title={c.display_name}
              >
                {c.display_name}
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
                {contacts.map((c) => (
                  <td
                    key={c.contact_id}
                    className="px-3 py-1.5"
                    data-contact-cell={c.contact_id}
                  >
                    {(row.contacts[c.contact_id] ?? 0).toLocaleString()}
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
