import { useWorkingSetStore } from '../workingset/store'
import { formatPeriodLabel, UNIT_MS, type Unit } from '../chronicle/timeScale'
import { MessageCard } from './MessageCard'
import { SourceList } from './SourceList'

export interface InspectorPanelProps {
  /** Message count for the selected bucket when known. */
  bucketCount?: number | null
}

function unitMs(unit: string | null | undefined): number {
  if (unit && unit in UNIT_MS) return UNIT_MS[unit as Unit]
  return UNIT_MS.month
}

function bucketLabel(bucketIso: string): string {
  const ms = Date.parse(bucketIso)
  if (!Number.isFinite(ms)) return bucketIso
  return formatPeriodLabel(ms)
}

export function InspectorPanel({ bucketCount }: InspectorPanelProps) {
  const selection = useWorkingSetStore((s) => s.selection)
  const scope = useWorkingSetStore((s) => s.scope)
  const unit = useWorkingSetStore((s) => s.timelineUnit)
  const setSelection = useWorkingSetStore((s) => s.setSelection)

  if (!selection) {
    return (
      <p className="text-text-muted" data-testid="inspector-empty">
        Select a mark to inspect evidence
      </p>
    )
  }

  if (selection.kind === 'message') {
    return (
      <MessageCard
        sid={selection.sid}
        onClose={() => {
          // Spec: Close clears back to the parent bucket selection.
          useWorkingSetStore.getState().clearMessageToBucket()
        }}
      />
    )
  }

  // Bucket selection
  const { bucketIso, lane } = selection
  const dateFrom = bucketIso
  const dateTo = new Date(Date.parse(bucketIso) + unitMs(unit)).toISOString()

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="inspector-bucket">
      <div>
        <h3 className="text-sm font-medium text-text-primary">
          {lane} · {bucketLabel(bucketIso)}
        </h3>
        {bucketCount != null ? (
          <p className="tabular-nums text-[11px] text-text-muted">
            {bucketCount.toLocaleString()} in bucket
          </p>
        ) : null}
      </div>
      <p className="text-[11px] font-medium text-text-muted">Open as list</p>
      <SourceList
        scope={scope}
        dateFrom={dateFrom}
        dateTo={dateTo}
        onSelectMessage={(sid) => setSelection({ kind: 'message', sid })}
      />
    </div>
  )
}
