import { useState } from 'react'

import {
  LANE_CATALOG,
  type LaneSpec,
} from './laneModel'
import {
  saveLanesAsDefault,
} from '../workingset/urlState'
import type { MoveLaneDir } from '../workingset/store'

export interface LaneConfigPanelProps {
  /** Ordered visible lane keys. */
  lanes: string[]
  onToggle: (key: string) => void
  onMove: (key: string, dir: MoveLaneDir) => void
  /** Optional: collapse state controlled externally; default internal. */
  defaultCollapsed?: boolean
}

/**
 * Configuration rail: hide/show and reorder lanes; save lens to localStorage.
 * Placed left of the timeline canvas in ChroniclePage (220px, collapsible).
 */
export function LaneConfigPanel({
  lanes,
  onToggle,
  onMove,
  defaultCollapsed = false,
}: LaneConfigPanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)

  if (collapsed) {
    return (
      <div
        className="flex shrink-0 flex-col items-center border border-steel bg-graphite-900"
        data-testid="lane-config-panel"
        data-collapsed="true"
      >
        <button
          type="button"
          className="px-2 py-3 text-text-muted hover:text-text-primary"
          aria-expanded={false}
          aria-controls="lane-config-body"
          onClick={() => setCollapsed(false)}
        >
          Lanes
        </button>
      </div>
    )
  }

  // Visible lanes in store order, then hidden catalog entries.
  const visibleSet = new Set(lanes)
  const visibleSpecs: LaneSpec[] = []
  for (const key of lanes) {
    const spec = LANE_CATALOG.find((s) => s.key === key)
    if (spec) visibleSpecs.push(spec)
  }
  const hiddenSpecs = LANE_CATALOG.filter((s) => !visibleSet.has(s.key))
  const rows = [...visibleSpecs, ...hiddenSpecs]

  return (
    <aside
      className="flex w-[220px] shrink-0 flex-col gap-2 rounded-lg border border-steel bg-graphite-900 p-3"
      data-testid="lane-config-panel"
      data-collapsed="false"
      aria-label="Lane configuration"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-text-primary">Lanes</h2>
        <button
          type="button"
          className="text-xs text-text-muted hover:text-text-primary"
          aria-expanded={true}
          aria-controls="lane-config-body"
          onClick={() => setCollapsed(true)}
        >
          Collapse
        </button>
      </div>

      <ul id="lane-config-body" className="flex flex-col gap-1.5" role="list">
        {rows.map((spec) => {
          const visible = visibleSet.has(spec.key)
          const orderIdx = lanes.indexOf(spec.key)
          const canUp = visible && orderIdx > 0
          const canDown = visible && orderIdx >= 0 && orderIdx < lanes.length - 1
          return (
            <li
              key={spec.key}
              className="flex items-center gap-1 rounded border border-steel/60 px-1.5 py-1"
              data-lane-key={spec.key}
            >
              <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-sm text-text-primary">
                <input
                  type="checkbox"
                  checked={visible}
                  onChange={() => onToggle(spec.key)}
                  aria-label={`Show ${spec.label}`}
                />
                <span className="truncate">{spec.label}</span>
              </label>
              <div className="flex shrink-0 flex-col gap-0.5">
                <button
                  type="button"
                  className="rounded px-1.5 py-0.5 text-xs text-text-muted enabled:hover:bg-graphite-800 enabled:hover:text-text-primary disabled:opacity-30"
                  disabled={!canUp}
                  aria-label={`Move ${spec.label} up`}
                  onClick={() => onMove(spec.key, 'up')}
                >
                  Up
                </button>
                <button
                  type="button"
                  className="rounded px-1.5 py-0.5 text-xs text-text-muted enabled:hover:bg-graphite-800 enabled:hover:text-text-primary disabled:opacity-30"
                  disabled={!canDown}
                  aria-label={`Move ${spec.label} down`}
                  onClick={() => onMove(spec.key, 'down')}
                >
                  Down
                </button>
              </div>
            </li>
          )
        })}
      </ul>

      <button
        type="button"
        className="mt-1 rounded-md border border-steel bg-graphite-800 px-2 py-1.5 text-xs text-text-primary hover:border-action"
        onClick={() => saveLanesAsDefault(lanes)}
        data-testid="save-default-lanes"
      >
        Save as default lanes
      </button>
    </aside>
  )
}
