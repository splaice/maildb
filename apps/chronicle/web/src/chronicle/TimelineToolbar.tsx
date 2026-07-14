import type { Viewport } from './timeScale'
import { formatPeriodRange } from './timeScale'

export interface TimelineToolbarProps {
  viewport: Viewport
  unit: string
  brush: Viewport | null
  viewMode: 'canvas' | 'table'
  onZoomIn: () => void
  onZoomOut: () => void
  onFitAll: () => void
  onZoomToSelection: () => void
  onClearSelection: () => void
  onToggleViewMode: () => void
}

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

export function TimelineToolbar({
  viewport,
  unit,
  brush,
  viewMode,
  onZoomIn,
  onZoomOut,
  onFitAll,
  onZoomToSelection,
  onClearSelection,
  onToggleViewMode,
}: TimelineToolbarProps) {
  const period = formatPeriodRange(viewport)
  const unitLabel = unit ? `${unit} buckets` : 'buckets'

  return (
    <div
      className="flex flex-wrap items-center gap-2 text-text-primary"
      data-testid="timeline-toolbar"
    >
      <span className="tabular-nums text-text-muted" data-testid="visible-period">
        {period} · {unitLabel}
      </span>
      <div className="flex flex-wrap items-center gap-1">
        <button type="button" className={btnClass} onClick={onZoomIn} title="Zoom in (])">
          Zoom in <span className="text-text-muted">[&#93;</span>
        </button>
        <button type="button" className={btnClass} onClick={onZoomOut} title="Zoom out ([)">
          Zoom out <span className="text-text-muted">&#91;</span>
        </button>
        <button type="button" className={btnClass} onClick={onFitAll}>
          Fit all
        </button>
        <button
          type="button"
          className={btnClass}
          onClick={onZoomToSelection}
          disabled={brush == null}
        >
          Zoom to selection
        </button>
        <button
          type="button"
          className={btnClass}
          onClick={onClearSelection}
          disabled={brush == null}
        >
          Clear selection
        </button>
        <button type="button" className={btnClass} onClick={onToggleViewMode}>
          {viewMode === 'canvas' ? 'View as table' : 'View as canvas'}
        </button>
      </div>
    </div>
  )
}
