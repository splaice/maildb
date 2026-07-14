import { useCallback, useMemo, useState } from 'react'

import type { LaneData, QueryScope } from '../api/types'
import { TimelineCanvas } from '../chronicle/TimelineCanvas'
import { specsForKeys } from '../chronicle/laneModel'
import { formatPeriodRange, type Viewport } from '../chronicle/timeScale'
import { CompareTable } from './CompareTable'
import { formatCompareDurationLabel, formatPercentDelta } from './ranges'
import { normalizeLaneData } from './normalize'
import { useChronicleCompare } from './useChronicleCompare'

export type CompareScaleMode = 'absolute' | 'normalized'

export interface CompareViewProps {
  a: Viewport
  b: Viewport
  scope: QueryScope
  lanes: string[]
  onExit: () => void
  onUpdateSide: (side: 'a' | 'b', range: Viewport) => void
}

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

/**
 * Compare mode canvas region (spec §4.7 Table 16).
 * Replaces the main timeline while active; scope bar / inspector stay mounted.
 *
 * Pan/zoom is DISABLED on both panels (static comparison): onViewportChange is
 * a no-op. Brushing inside a panel updates that range and re-fetches.
 */
export function CompareView({
  a,
  b,
  scope,
  lanes,
  onExit,
  onUpdateSide,
}: CompareViewProps) {
  const [pixelWidth, setPixelWidth] = useState(920)
  const [scaleMode, setScaleMode] = useState<CompareScaleMode>('absolute')
  const [tableView, setTableView] = useState(false)
  const [brushA, setBrushA] = useState<Viewport | null>(null)
  const [brushB, setBrushB] = useState<Viewport | null>(null)

  const compare = useChronicleCompare({
    a,
    b,
    pixelWidth,
    scope,
    lanes,
    enabled: pixelWidth > 0,
  })

  const laneSpecs = useMemo(() => specsForKeys(lanes), [lanes])
  const unit = compare.data?.unit ?? 'month'
  const aligned = compare.data?.aligned ?? false
  const totals = compare.data?.totals

  const rawA = compare.data?.a.lanes
  const rawB = compare.data?.b.lanes

  const laneDataA = useMemo((): Record<string, LaneData> => {
    if (!rawA) return {}
    return scaleMode === 'normalized' ? normalizeLaneData(rawA) : rawA
  }, [rawA, scaleMode])
  const laneDataB = useMemo((): Record<string, LaneData> => {
    if (!rawB) return {}
    return scaleMode === 'normalized' ? normalizeLaneData(rawB) : rawB
  }, [rawB, scaleMode])

  const onWidthChange = useCallback((w: number) => {
    if (w > 0) setPixelWidth(w)
  }, [])

  // Pan/zoom intentionally ignored — static comparison panels.
  const noopViewport = useCallback((_vp: Viewport) => {}, [])

  const onBrushA = useCallback(
    (brush: Viewport | null) => {
      if (brush && brush.toMs > brush.fromMs) {
        setBrushA(null)
        onUpdateSide('a', brush)
      } else {
        setBrushA(brush)
      }
    },
    [onUpdateSide],
  )

  const onBrushB = useCallback(
    (brush: Viewport | null) => {
      if (brush && brush.toMs > brush.fromMs) {
        setBrushB(null)
        onUpdateSide('b', brush)
      } else {
        setBrushB(brush)
      }
    },
    [onUpdateSide],
  )

  const msgDelta =
    totals != null
      ? formatPercentDelta(totals.a.messages, totals.b.messages)
      : '—'
  const attDelta =
    totals != null
      ? formatPercentDelta(totals.a.attachments, totals.b.attachments)
      : '—'

  const panelLabel = (side: 'a' | 'b', vp: Viewport) => {
    if (aligned) {
      return `${side.toUpperCase()}: ${formatPeriodRange(vp)}`
    }
    return `${side.toUpperCase()}: ${formatCompareDurationLabel(vp)}`
  }

  return (
    <div
      className="flex min-h-[20rem] flex-col gap-2 rounded-lg border border-steel bg-graphite-900"
      data-testid="compare-view"
    >
      <header
        className="flex flex-wrap items-center gap-2 border-b border-steel px-3 py-2"
        data-testid="compare-header"
      >
        <h2 className="text-sm font-medium text-text-primary">Compare</h2>
        <span
          className="tabular-nums text-[11px] text-text-muted"
          data-testid="compare-unit"
        >
          {unit} buckets · {aligned ? 'aligned' : 'small multiples'}
        </span>
        <div
          className="flex items-center gap-1"
          role="group"
          aria-label="Scale mode"
          data-testid="compare-scale-toggle"
        >
          <button
            type="button"
            className={`${btnClass} ${scaleMode === 'absolute' ? 'ring-1 ring-action' : ''}`}
            aria-pressed={scaleMode === 'absolute'}
            onClick={() => setScaleMode('absolute')}
            data-testid="compare-scale-absolute"
          >
            Absolute
          </button>
          <button
            type="button"
            className={`${btnClass} ${scaleMode === 'normalized' ? 'ring-1 ring-action' : ''}`}
            aria-pressed={scaleMode === 'normalized'}
            onClick={() => setScaleMode('normalized')}
            data-testid="compare-scale-normalized"
          >
            Normalized
          </button>
        </div>
        <button
          type="button"
          className={btnClass}
          onClick={() => setTableView((v) => !v)}
          data-testid="compare-table-toggle"
        >
          {tableView ? 'View as canvas' : 'View as table'}
        </button>
        <button
          type="button"
          className={`${btnClass} ml-auto`}
          onClick={onExit}
          data-testid="compare-exit"
        >
          Close
        </button>
      </header>

      {/* Explicit legend for scale mode (spec §6.2). */}
      <div
        className="px-3 text-[11px] text-text-muted"
        data-testid="compare-legend"
        role="status"
      >
        Legend: scale mode is{' '}
        <strong className="text-text-primary">
          {scaleMode === 'absolute' ? 'Absolute counts' : 'Normalized (share of range total)'}
        </strong>
        {aligned
          ? ' · panels share x-pixel mapping and unit'
          : ' · independent panels (no forced alignment)'}
        {' · pan/zoom disabled; brush a panel to update that range'}
      </div>

      {totals ? (
        <div
          className="flex flex-wrap gap-4 px-3 text-sm tabular-nums text-text-primary"
          data-testid="compare-totals"
        >
          <span>
            A: {totals.a.messages.toLocaleString()} messages ·{' '}
            {totals.a.attachments.toLocaleString()} attachments
          </span>
          <span>
            B: {totals.b.messages.toLocaleString()} messages ·{' '}
            {totals.b.attachments.toLocaleString()} attachments
          </span>
          <span data-testid="compare-delta">
            B vs A: {msgDelta} messages · {attDelta} attachments
          </span>
        </div>
      ) : null}

      {compare.isError ? (
        <div
          role="alert"
          data-testid="compare-error"
          className="mx-3 rounded-lg border border-conflict p-3 text-conflict"
        >
          Failed to load comparison
          {compare.error instanceof Error ? `: ${compare.error.message}` : ''}
          <button
            type="button"
            className={`${btnClass} ml-2`}
            onClick={() => void compare.refetch()}
          >
            Retry
          </button>
        </div>
      ) : null}

      {tableView ? (
        <div className="px-3 pb-3">
          <CompareTable
            a={a}
            b={b}
            unit={unit}
            lanes={laneSpecs}
            laneDataA={laneDataA}
            laneDataB={laneDataB}
          />
        </div>
      ) : (
        <div
          className="flex flex-col gap-0 px-2 pb-2"
          data-testid={aligned ? 'compare-aligned' : 'compare-multiples'}
        >
          <div data-testid="compare-panel-a" className="min-h-0">
            <p className="px-1 py-1 text-[11px] font-medium text-text-muted">
              {panelLabel('a', a)}
            </p>
            <TimelineCanvas
              viewport={a}
              extent={null}
              unit={unit}
              lanes={laneSpecs}
              laneData={laneDataA}
              isFetching={compare.isFetching}
              brush={brushA}
              onViewportChange={noopViewport}
              onBrushChange={onBrushA}
              onWidthChange={onWidthChange}
            />
          </div>

          <div
            className="flex items-center gap-2 border-y border-steel py-1.5 text-[11px] text-text-muted"
            data-testid="compare-divider"
            role="separator"
          >
            <span className="h-px flex-1 bg-steel" />
            <span className="shrink-0 tabular-nums">
              {formatPeriodRange(a)}  ·  {formatPeriodRange(b)}
            </span>
            <span className="h-px flex-1 bg-steel" />
          </div>

          <div data-testid="compare-panel-b" className="min-h-0">
            <p className="px-1 py-1 text-[11px] font-medium text-text-muted">
              {panelLabel('b', b)}
            </p>
            <TimelineCanvas
              viewport={b}
              extent={null}
              unit={unit}
              lanes={laneSpecs}
              laneData={laneDataB}
              isFetching={compare.isFetching}
              brush={brushB}
              onViewportChange={noopViewport}
              onBrushChange={onBrushB}
              onWidthChange={onWidthChange}
            />
          </div>
        </div>
      )}
    </div>
  )
}
