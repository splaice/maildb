import { useCallback, useEffect, useMemo, useState } from 'react'

import type { ArchiveSummary, LaneData } from '../api/types'
import { isBucketSeries } from '../api/types'
import { DensityNavigator } from '../chronicle/DensityNavigator'
import { LaneConfigPanel } from '../chronicle/LaneConfigPanel'
import { specsForKeys } from '../chronicle/laneModel'
import { TimelineCanvas } from '../chronicle/TimelineCanvas'
import { TimelineTable } from '../chronicle/TimelineTable'
import { TimelineToolbar } from '../chronicle/TimelineToolbar'
import {
  clampViewport,
  MIN_SPAN_MS,
  type Viewport,
  zoomViewport,
} from '../chronicle/timeScale'
import { useChronicleBuckets } from '../chronicle/useChronicleBuckets'
import { GeneratePanel } from '../events/GeneratePanel'
import { FocusModeConnected } from '../focus/FocusMode'
import { useWorkingSetStore } from '../workingset/store'
import { useArchiveSummary } from './useArchiveSummary'

function SummarySkeleton() {
  return (
    <div
      className="animate-pulse space-y-2 rounded-lg border border-steel bg-graphite-900 p-4"
      data-testid="archive-summary-skeleton"
      aria-busy="true"
      aria-label="Loading archive coverage"
    >
      <div className="h-4 w-40 rounded bg-graphite-800" />
      <div className="h-3 w-full rounded bg-graphite-800" />
      <div className="h-3 w-5/6 rounded bg-graphite-800" />
      <div className="h-3 w-2/3 rounded bg-graphite-800" />
    </div>
  )
}

function formatYear(iso: string | null): string {
  if (!iso) return '—'
  return iso.slice(0, 4)
}

function CoverageTable({ data }: { data: ArchiveSummary }) {
  const { counts, extraction, embedding, date_range } = data
  return (
    <div>
      <table className="w-full border-collapse text-left">
        <tbody className="tabular-nums font-mono text-text-primary">
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Messages
            </th>
            <td className="py-1.5">{counts.messages.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Threads
            </th>
            <td className="py-1.5">{counts.threads.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Attachments
            </th>
            <td className="py-1.5">{counts.attachments.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Contacts
            </th>
            <td className="py-1.5">{counts.contacts.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Date range
            </th>
            <td className="py-1.5">
              {formatYear(date_range.from)}–{formatYear(date_range.to)}
            </td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Extraction
            </th>
            <td className="py-1.5">
              {extraction.extracted} extracted / {extraction.failed} failed /{' '}
              {extraction.skipped} skipped / {extraction.pending} pending
            </td>
          </tr>
          <tr>
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Embedding
            </th>
            <td className="py-1.5">
              {embedding.embedded} embedded / {embedding.missing} missing
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

/** Sentinel full-range viewport used only for the bootstrap extent fetch. */
const BOOTSTRAP_VIEWPORT: Viewport = {
  fromMs: Date.parse('1970-01-01T00:00:00.000Z'),
  toMs: Date.parse('2100-01-01T00:00:00.000Z'),
}

function extentFromResponse(
  from: string | null | undefined,
  to: string | null | undefined,
): Viewport | null {
  if (!from || !to) return null
  const fromMs = Date.parse(from)
  const toMs = Date.parse(to)
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) {
    return null
  }
  return { fromMs, toMs }
}

function sumLaneCounts(points: { count: number }[] | undefined): number {
  if (!points) return 0
  return points.reduce((acc, p) => acc + (p.count ?? 0), 0)
}

export function ChroniclePage() {
  const archive = useArchiveSummary()

  // Viewport / scope / view / brush come from the shared working-set store.
  // Bootstrap: if URL had no viewport, fetch with sentinel full-range
  // 1970-01-01..2100-01-01, then set viewport := extent (transient → replace-state).
  const viewport = useWorkingSetStore((s) => s.viewport)
  const brush = useWorkingSetStore((s) => s.brush)
  const focus = useWorkingSetStore((s) => s.focus)
  const viewMode = useWorkingSetStore((s) => s.view)
  const scope = useWorkingSetStore((s) => s.scope)
  const setViewport = useWorkingSetStore((s) => s.setViewport)
  const setBrush = useWorkingSetStore((s) => s.setBrush)
  const setFocus = useWorkingSetStore((s) => s.setFocus)
  const applyBrushAsViewport = useWorkingSetStore((s) => s.applyBrushAsViewport)
  const setView = useWorkingSetStore((s) => s.setView)
  const setResultCount = useWorkingSetStore((s) => s.setResultCount)
  const setTimelineUnit = useWorkingSetStore((s) => s.setTimelineUnit)
  const selection = useWorkingSetStore((s) => s.selection)
  const setSelection = useWorkingSetStore((s) => s.setSelection)
  const lanes = useWorkingSetStore((s) => s.lanes)
  const toggleLane = useWorkingSetStore((s) => s.toggleLane)
  const moveLane = useWorkingSetStore((s) => s.moveLane)

  const [pixelWidth, setPixelWidth] = useState(920)
  // False until URL hydrate or extent bootstrap resolves (layout hydrate may set
  // viewport after this component's first render).
  const [bootstrapped, setBootstrapped] = useState(false)

  const activeViewport = viewport ?? BOOTSTRAP_VIEWPORT
  const laneSpecs = useMemo(() => specsForKeys(lanes), [lanes])

  const buckets = useChronicleBuckets({
    viewport: activeViewport,
    pixelWidth,
    scope,
    lanes,
    enabled: pixelWidth > 0,
  })

  const extent = useMemo(() => {
    if (buckets.data?.extent) {
      return extentFromResponse(buckets.data.extent.from, buckets.data.extent.to)
    }
    return null
  }, [buckets.data?.extent])

  // Apply bootstrap: if store/URL already has a viewport, mark done; otherwise
  // first response with extent sets viewport := extent (transient → replace-state).
  useEffect(() => {
    if (bootstrapped) return
    if (viewport != null) {
      setBootstrapped(true)
      return
    }
    if (!buckets.data?.extent) return
    const ext = extentFromResponse(buckets.data.extent.from, buckets.data.extent.to)
    if (!ext) {
      setBootstrapped(true)
      return
    }
    setViewport(ext)
    setBootstrapped(true)
  }, [bootstrapped, buckets.data?.extent, setViewport, viewport])

  // Live result framing for the scope bar + unit for inspector bucket ranges.
  useEffect(() => {
    if (!buckets.data) return
    const messages = buckets.data.lanes.messages
    setResultCount(sumLaneCounts(isBucketSeries(messages) ? messages : undefined))
    setTimelineUnit(buckets.data.unit ?? buckets.data.aggregation ?? null)
  }, [buckets.data, setResultCount, setTimelineUnit])

  const applyViewport = useCallback(
    (next: Viewport) => {
      if (extent) {
        setViewport(clampViewport(next, extent, MIN_SPAN_MS))
      } else {
        setViewport(next)
      }
    },
    [extent, setViewport],
  )

  const onWidthChange = useCallback((w: number) => {
    if (w > 0) setPixelWidth(w)
  }, [])

  const zoomAroundCenter = useCallback(
    (factor: number) => {
      if (!viewport) return
      const centerPx = pixelWidth / 2
      applyViewport(zoomViewport(viewport, factor, centerPx, Math.max(1, pixelWidth)))
    },
    [applyViewport, pixelWidth, viewport],
  )

  // Keyboard shortcuts [ / ] zoom, Enter → focus period when brush exists —
  // page-scoped, cleaned up on unmount.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable)
      ) {
        return
      }
      if (e.key === '[') {
        e.preventDefault()
        zoomAroundCenter(2) // zoom out
      } else if (e.key === ']') {
        e.preventDefault()
        zoomAroundCenter(0.5) // zoom in
      } else if (e.key === 'Escape') {
        setBrush(null)
      } else if (e.key === 'Enter') {
        // Focus period when a brush exists (spec §4.6 entry).
        const b = useWorkingSetStore.getState().brush
        if (b && b.toMs > b.fromMs) {
          e.preventDefault()
          setFocus(b)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setBrush, setFocus, zoomAroundCenter])

  const unit = buckets.data?.unit ?? buckets.data?.aggregation ?? 'month'
  const densityBuckets = buckets.data?.density.buckets ?? []
  const laneData: Record<string, LaneData> = buckets.data?.lanes ?? {}

  const showTimeline = viewport != null
  const inFocus = focus != null

  return (
    <div className="space-y-4">
      <h1 className="text-base font-medium text-text-primary">Chronicle</h1>

      {/* Timeline region — replaced by FocusMode while focus is active. */}
      <section className="space-y-2" aria-label="Timeline">
        {inFocus ? (
          <FocusModeConnected />
        ) : (
          <>
            {showTimeline ? (
              <TimelineToolbar
                viewport={viewport}
                unit={unit}
                brush={brush}
                viewMode={viewMode}
                onZoomIn={() => zoomAroundCenter(0.5)}
                onZoomOut={() => zoomAroundCenter(2)}
                onFitAll={() => {
                  if (extent) applyViewport(extent)
                }}
                onZoomToSelection={() => {
                  applyBrushAsViewport()
                }}
                onClearSelection={() => setBrush(null)}
                onToggleViewMode={() =>
                  setView(viewMode === 'canvas' ? 'table' : 'canvas')
                }
                onFocusPeriod={() => {
                  if (brush) setFocus(brush)
                }}
              />
            ) : null}

            {buckets.isError ? (
              <div
                role="alert"
                data-testid="timeline-error"
                className="rounded-lg border border-conflict bg-graphite-900 p-4 text-conflict"
              >
                <p className="mb-2">
                  Failed to load timeline
                  {buckets.error instanceof Error
                    ? `: ${buckets.error.message}`
                    : ''}
                </p>
                <button
                  type="button"
                  onClick={() => void buckets.refetch()}
                  disabled={buckets.isFetching}
                  className="rounded-md border border-steel bg-graphite-800 px-3 py-1.5 text-text-primary"
                >
                  Retry
                </button>
              </div>
            ) : null}

            {/* Config rail (left) + canvas/table — shell has no config slot yet. */}
            {showTimeline && (buckets.data || !buckets.isError) ? (
              <div className="flex gap-2" data-testid="timeline-with-config">
                <div className="flex w-[220px] shrink-0 flex-col gap-2">
                  <LaneConfigPanel
                    lanes={lanes}
                    onToggle={toggleLane}
                    onMove={moveLane}
                  />
                  <GeneratePanel scope={scope} viewport={viewport} />
                </div>
                <div className="min-w-0 flex-1">
                  {viewMode === 'table' ? (
                    <TimelineTable
                      viewport={viewport}
                      unit={unit}
                      lanes={laneSpecs}
                      laneData={laneData}
                    />
                  ) : (
                    <TimelineCanvas
                      viewport={viewport}
                      extent={extent}
                      unit={unit}
                      lanes={laneSpecs}
                      laneData={laneData}
                      isFetching={buckets.isFetching}
                      brush={brush}
                      selectedBucket={
                        selection?.kind === 'bucket'
                          ? {
                              bucketIso: selection.bucketIso,
                              lane: selection.lane,
                            }
                          : null
                      }
                      onViewportChange={applyViewport}
                      onBrushChange={setBrush}
                      onWidthChange={onWidthChange}
                      onSelectBucket={(bucketIso, laneName) =>
                        setSelection({
                          kind: 'bucket',
                          bucketIso,
                          lane: laneName,
                        })
                      }
                      onFocusBucket={(period) => setFocus(period)}
                    />
                  )}
                </div>
              </div>
            ) : null}

            {/* Bootstrap: measure width even before viewport is set */}
            {!showTimeline && !buckets.isError ? (
              <div
                className="h-40 w-full rounded-lg border border-steel bg-graphite-900"
                ref={(el) => {
                  if (el) {
                    const w = el.clientWidth
                    if (w > 0 && w !== pixelWidth) setPixelWidth(w)
                  }
                }}
                data-testid="timeline-bootstrap"
                aria-busy={buckets.isLoading || buckets.isFetching}
                aria-label="Loading timeline"
              >
                <div className="h-0.5 w-full animate-pulse bg-action" />
              </div>
            ) : null}

            {showTimeline && extent ? (
              <DensityNavigator
                extent={extent}
                viewport={viewport}
                densityBuckets={densityBuckets}
                onViewportChange={applyViewport}
              />
            ) : null}
          </>
        )}
      </section>

      {/* Archive coverage stays available behind a collapsed details element */}
      <details className="rounded-lg border border-steel bg-graphite-900">
        <summary className="cursor-pointer px-4 py-2 text-sm font-medium text-text-primary">
          Archive coverage
        </summary>
        <div className="space-y-2 border-t border-steel p-4">
          {archive.isLoading ? <SummarySkeleton /> : null}
          {archive.isError ? (
            <div
              role="alert"
              className="rounded-lg border border-conflict bg-graphite-900 p-4 text-conflict"
            >
              <p className="mb-2">
                Failed to load archive coverage
                {archive.error instanceof Error ? `: ${archive.error.message}` : ''}
              </p>
              <button
                type="button"
                onClick={() => void archive.refetch()}
                disabled={archive.isFetching}
                className="rounded-md border border-steel bg-graphite-800 px-3 py-1.5 text-text-primary"
              >
                Retry
              </button>
            </div>
          ) : null}
          {archive.data ? <CoverageTable data={archive.data} /> : null}
        </div>
      </details>
    </div>
  )
}
