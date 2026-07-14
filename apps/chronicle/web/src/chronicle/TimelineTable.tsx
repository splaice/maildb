import type { BucketPoint } from '../api/types'
import { formatPeriodLabel, formatPeriodRange, type Viewport } from './timeScale'

export interface TimelineTableProps {
  viewport: Viewport
  unit: string
  messages: BucketPoint[]
  attachments: BucketPoint[]
}

/** Merge message/attachment buckets by timestamp for table rows. */
export function mergeBucketRows(
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

function bucketLabel(iso: string): string {
  const ms = Date.parse(iso)
  if (!Number.isFinite(ms)) return iso
  return formatPeriodLabel(ms)
}

/**
 * Accessible table alternative for the timeline canvas (A11Y-002).
 * One row per bucket in the current viewport: label, messages, attachments.
 */
export function TimelineTable({
  viewport,
  unit,
  messages,
  attachments,
}: TimelineTableProps) {
  const rows = mergeBucketRows(messages, attachments)
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
            <th scope="col" className="px-3 py-2 font-sans font-medium">
              Messages
            </th>
            <th scope="col" className="px-3 py-2 font-sans font-medium">
              Attachments
            </th>
          </tr>
        </thead>
        <tbody className="tabular-nums font-mono text-text-primary">
          {rows.length === 0 ? (
            <tr>
              <td colSpan={3} className="px-3 py-4 text-center font-sans text-text-muted">
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
                <td className="px-3 py-1.5">{row.messages.toLocaleString()}</td>
                <td className="px-3 py-1.5">{row.attachments.toLocaleString()}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
