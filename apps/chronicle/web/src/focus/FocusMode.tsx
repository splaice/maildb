import { useMemo, useState } from 'react'

import type { QueryScope } from '../api/types'
import { isBucketSeries } from '../api/types'
import { useChronicleBuckets } from '../chronicle/useChronicleBuckets'
import {
  formatPeriodLabel,
  formatPeriodRange,
  type Viewport,
} from '../chronicle/timeScale'
import { SourceList } from '../inspector/SourceList'
import { useWorkingSetStore } from '../workingset/store'

export interface FocusModeProps {
  focus: Viewport
  scope: QueryScope
  /** Main timeline unit — local chronology uses a finer auto aggregation. */
  mainUnit: string
  onExit: () => void
  onSetAsScopeDate: () => void
  onSelectMessage: (sid: string) => void
}

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

function sumCounts(points: { count: number }[] | undefined): number {
  if (!points) return 0
  return points.reduce((acc, p) => acc + (p.count ?? 0), 0)
}

function toIso(ms: number): string {
  return new Date(ms).toISOString()
}

/**
 * Bounded analytical workspace over a selected period (spec §4.6).
 * Replaces the canvas region; scope bar and inspector stay mounted.
 */
export function FocusMode({
  focus,
  scope,
  mainUnit,
  onExit,
  onSetAsScopeDate,
  onSelectMessage,
}: FocusModeProps) {
  // List range starts as the full focus period; sub-period clicks narrow it.
  const [listFromMs, setListFromMs] = useState(focus.fromMs)
  const [listToMs, setListToMs] = useState(focus.toMs)

  // Local chronology: focus viewport + narrower pixel width → finer buckets.
  // ~200px rail width as specified.
  const localBuckets = useChronicleBuckets({
    viewport: focus,
    pixelWidth: 200,
    scope,
    lanes: ['messages', 'attachments'],
    enabled: true,
  })

  const localUnit = localBuckets.data?.unit ?? localBuckets.data?.aggregation ?? mainUnit
  const messagePoints = isBucketSeries(localBuckets.data?.lanes?.messages)
    ? localBuckets.data!.lanes.messages
    : []
  const attachmentPoints = isBucketSeries(localBuckets.data?.lanes?.attachments)
    ? localBuckets.data!.lanes.attachments
    : []
  const msgTotal = sumCounts(messagePoints)
  const attTotal = sumCounts(attachmentPoints)

  const periodLabel = formatPeriodRange(focus)

  const listDateFrom = useMemo(() => toIso(listFromMs), [listFromMs])
  const listDateTo = useMemo(() => toIso(listToMs), [listToMs])

  const onSubPeriodClick = (bucketIso: string) => {
    const fromMs = Date.parse(bucketIso)
    if (!Number.isFinite(fromMs)) return
    // Narrow the source sequence to this sub-period (sets list date_from / date_to).
    // Span is the local unit width when known; otherwise open-ended to focus end.
    const unitMsGuess = (() => {
      const u = localUnit
      if (u === 'hour') return 60 * 60 * 1000
      if (u === 'day') return 24 * 60 * 60 * 1000
      if (u === 'week') return 7 * 24 * 60 * 60 * 1000
      if (u === 'month') return 30 * 24 * 60 * 60 * 1000
      if (u === 'quarter') return 91 * 24 * 60 * 60 * 1000
      if (u === 'year') return 365 * 24 * 60 * 60 * 1000
      return Math.max(focus.toMs - fromMs, 60 * 60 * 1000)
    })()
    const toMs = Math.min(fromMs + unitMsGuess, focus.toMs)
    setListFromMs(fromMs)
    setListToMs(toMs > fromMs ? toMs : focus.toMs)
  }

  return (
    <div
      className="flex min-h-[20rem] flex-col gap-2 rounded-lg border border-steel bg-graphite-900"
      data-testid="focus-mode"
    >
      <header
        className="flex flex-wrap items-center gap-2 border-b border-steel px-3 py-2"
        data-testid="focus-header"
      >
        <h2 className="text-sm font-medium text-text-primary">
          Focus: <span data-testid="focus-period-label">{periodLabel}</span>
        </h2>
        <span
          className="tabular-nums text-[11px] text-text-muted"
          data-testid="focus-totals"
        >
          {msgTotal.toLocaleString()} messages · {attTotal.toLocaleString()} attachments
          {localUnit ? ` · ${localUnit}` : ''}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-1">
          <button
            type="button"
            className={btnClass}
            onClick={onSetAsScopeDate}
            data-testid="focus-set-scope-date"
            title="Copy focus range into the scope date filter and exit focus"
          >
            Set as scope date
          </button>
          <button
            type="button"
            className={btnClass}
            onClick={onExit}
            data-testid="focus-exit"
          >
            Exit
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 gap-0">
        {/* Left rail: local chronology */}
        <aside
          className="w-[200px] shrink-0 overflow-auto border-r border-steel p-2"
          data-testid="focus-local-chronology"
          aria-label="Local chronology"
        >
          <p className="mb-1 text-[11px] font-medium text-text-muted">
            Local chronology
            {localUnit ? ` · ${localUnit}` : ''}
          </p>
          {localBuckets.isLoading ? (
            <div className="space-y-1" aria-busy="true">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-6 animate-pulse rounded bg-graphite-800" />
              ))}
            </div>
          ) : null}
          {localBuckets.isError ? (
            <p className="text-[11px] text-conflict">Failed to load chronology</p>
          ) : null}
          <ul className="space-y-0.5">
            {messagePoints.map((pt) => {
              const ms = Date.parse(pt.bucket)
              const label = Number.isFinite(ms)
                ? formatPeriodLabel(ms)
                : pt.bucket
              const att =
                attachmentPoints.find((a) => a.bucket === pt.bucket)?.count ?? 0
              return (
                <li key={pt.bucket}>
                  <button
                    type="button"
                    className="flex w-full flex-col rounded px-1.5 py-1 text-left hover:bg-graphite-800"
                    onClick={() => onSubPeriodClick(pt.bucket)}
                    data-testid={`focus-subperiod-${pt.bucket}`}
                  >
                    <span className="text-[12px] text-text-primary">{label}</span>
                    <span className="tabular-nums text-[10px] text-text-muted">
                      {pt.count.toLocaleString()} msg
                      {att > 0 ? ` · ${att} att` : ''}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
          {!localBuckets.isLoading && messagePoints.length === 0 ? (
            <p className="text-[11px] text-text-muted">No buckets in period</p>
          ) : null}
        </aside>

        {/* Center: chronological source sequence */}
        <div
          className="flex min-h-0 min-w-0 flex-1 flex-col p-2"
          data-testid="focus-source-sequence"
        >
          <p className="mb-1 text-[11px] font-medium text-text-muted">
            Sources
            {listFromMs !== focus.fromMs || listToMs !== focus.toMs
              ? ` · narrowed to ${formatPeriodLabel(listFromMs)}`
              : ''}
          </p>
          <SourceList
            scope={scope}
            dateFrom={listDateFrom}
            dateTo={listDateTo}
            onSelectMessage={onSelectMessage}
          />
        </div>
      </div>
    </div>
  )
}

/** Convenience: read focus from store and wire default actions. */
export function FocusModeConnected() {
  const focus = useWorkingSetStore((s) => s.focus)
  const scope = useWorkingSetStore((s) => s.scope)
  const mainUnit = useWorkingSetStore((s) => s.timelineUnit) ?? 'month'
  const exitFocus = useWorkingSetStore((s) => s.exitFocus)
  const applyFocusAsScopeDate = useWorkingSetStore((s) => s.applyFocusAsScopeDate)
  const setSelection = useWorkingSetStore((s) => s.setSelection)

  if (!focus) return null

  return (
    <FocusMode
      focus={focus}
      scope={scope}
      mainUnit={mainUnit}
      onExit={exitFocus}
      onSetAsScopeDate={applyFocusAsScopeDate}
      onSelectMessage={(sid) => setSelection({ kind: 'message', sid })}
    />
  )
}
