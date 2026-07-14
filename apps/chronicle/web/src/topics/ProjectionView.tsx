import { useMemo, useState } from 'react'

import type { TopicProjectionResponse } from '../api/types'
import { TOPIC_PURPLE } from '../chronicle/laneModel'
import { OriginBadge } from './originBadge'

export interface ProjectionViewProps {
  data: TopicProjectionResponse | undefined
  loading?: boolean
  selectedId: string | null
  onSelect: (id: string) => void
}

const DISCLAIMER =
  'Exploratory projection — positions change when embeddings or clustering change; not an objective map of meaning'

/**
 * Centroid-level PCA scatter (SVG, not canvas). Topic circles only — never
 * individual sources (TA-003). Zoom not required.
 */
export function ProjectionView({
  data,
  loading,
  selectedId,
  onSelect,
}: ProjectionViewProps) {
  const [asList, setAsList] = useState(false)
  const [hoverId, setHoverId] = useState<string | null>(null)

  const points = data?.points ?? []

  const sized = useMemo(() => {
    const maxCount = points.reduce((m, p) => Math.max(m, p.member_count), 0) || 1
    return points.map((p) => ({
      ...p,
      // sqrt scale for area-like sizing
      r: 4 + 12 * Math.sqrt(p.member_count / maxCount),
    }))
  }, [points])

  if (loading) {
    return (
      <p className="text-[12px] text-text-muted" data-testid="projection-loading">
        Loading projection…
      </p>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="projection-view">
      <p
        className="rounded border border-steel/80 bg-graphite-900 px-2 py-1.5 text-[11px] text-text-muted"
        data-testid="projection-disclaimer"
        role="note"
      >
        {DISCLAIMER}
      </p>
      {data?.note ? (
        <p className="text-[11px] text-text-muted" data-testid="projection-note">
          {data.note}
        </p>
      ) : null}
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary"
          onClick={() => setAsList((v) => !v)}
          data-testid="projection-view-as-list"
        >
          {asList ? 'View as scatter' : 'View as list'}
        </button>
        <span className="text-[11px] text-text-muted">
          {points.length} topic{points.length === 1 ? '' : 's'}
        </span>
      </div>

      {points.length === 0 ? (
        <p className="text-[12px] text-text-muted" data-testid="projection-empty">
          No centroid-backed topics to project
        </p>
      ) : asList ? (
        <div className="overflow-auto" data-testid="projection-list">
          <table className="w-full border-collapse text-left text-[11px]">
            <caption className="sr-only">Topic projection as list</caption>
            <thead className="text-text-muted">
              <tr className="border-b border-steel">
                <th scope="col" className="px-2 py-1">
                  Topic
                </th>
                <th scope="col" className="px-2 py-1">
                  Origin
                </th>
                <th scope="col" className="px-2 py-1">
                  Members
                </th>
                <th scope="col" className="px-2 py-1">
                  x
                </th>
                <th scope="col" className="px-2 py-1">
                  y
                </th>
              </tr>
            </thead>
            <tbody className="tabular-nums font-mono">
              {points.map((p) => (
                <tr
                  key={p.topic_id}
                  className={`cursor-pointer border-b border-steel/50 hover:bg-graphite-800 ${
                    selectedId === p.topic_id ? 'bg-action/10' : ''
                  }`}
                  onClick={() => onSelect(p.topic_id)}
                  data-testid={`projection-list-row-${p.topic_id}`}
                >
                  <th scope="row" className="px-2 py-1 text-left font-sans">
                    {p.label}
                  </th>
                  <td className="px-2 py-1">
                    <OriginBadge origin={p.origin} />
                  </td>
                  <td className="px-2 py-1">{p.member_count}</td>
                  <td className="px-2 py-1">{p.x.toFixed(3)}</td>
                  <td className="px-2 py-1">{p.y.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <svg
          viewBox="0 0 400 320"
          className="h-full min-h-[240px] w-full rounded border border-steel bg-graphite-900"
          role="img"
          aria-label="Topic centroid projection scatter"
          data-testid="projection-scatter"
        >
          {/* axes crosshair */}
          <line x1="200" y1="16" x2="200" y2="304" stroke="#3a3f4b" strokeWidth="1" />
          <line x1="16" y1="160" x2="384" y2="160" stroke="#3a3f4b" strokeWidth="1" />
          {sized.map((p) => {
            const cx = 200 + p.x * 160
            const cy = 160 - p.y * 130
            const selected = p.topic_id === selectedId
            const hovered = p.topic_id === hoverId
            return (
              <g key={p.topic_id}>
                <circle
                  cx={cx}
                  cy={cy}
                  r={p.r}
                  fill={TOPIC_PURPLE}
                  fillOpacity={selected ? 0.95 : 0.7}
                  stroke={selected || hovered ? '#fff' : 'transparent'}
                  strokeWidth={selected ? 2 : 1}
                  tabIndex={0}
                  role="button"
                  aria-label={`${p.label}, ${p.origin}, ${p.member_count} members`}
                  data-testid={`projection-point-${p.topic_id}`}
                  className="cursor-pointer outline-none focus-visible:stroke-white"
                  onClick={() => onSelect(p.topic_id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      onSelect(p.topic_id)
                    }
                  }}
                  onFocus={() => setHoverId(p.topic_id)}
                  onBlur={() => setHoverId((id) => (id === p.topic_id ? null : id))}
                  onMouseEnter={() => setHoverId(p.topic_id)}
                  onMouseLeave={() =>
                    setHoverId((id) => (id === p.topic_id ? null : id))
                  }
                >
                  <title>
                    {p.label} · {p.origin} · {p.member_count}
                  </title>
                </circle>
                {(hovered || selected) && (
                  <text
                    x={cx}
                    y={cy - p.r - 4}
                    textAnchor="middle"
                    className="fill-text-primary text-[10px]"
                    fill="#e8eaed"
                    fontSize={10}
                    data-testid={`projection-label-${p.topic_id}`}
                  >
                    {p.label}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
      )}
    </div>
  )
}
