/**
 * Depth-1 ego co-participation graph (spec §7.3).
 * Edge selection drills to exact shared threads (PE-003).
 * No relationship-quality labels (PE-004).
 */

import { useQueries, useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { apiGet } from '../api/client'
import type {
  ContactCard,
  EgoGraphEdge,
  EgoGraphNode,
  EgoGraphResponse,
  ThreadResponse,
} from '../api/types'
import { PEOPLE_CYAN } from '../chronicle/laneModel'
import { layoutEgoGraph } from './egoLayout'

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[12px] text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

const MAX_NODE_OPTIONS = [10, 25, 50] as const
const LABEL_ALWAYS_THRESHOLD = 12
const ACCENT = '#5aa7ff'
const EGO_RING = '#e8ecf4'

export interface EgoGraphProps {
  contactId: string
  card: ContactCard
}

function yearOf(iso: string | null | undefined): string {
  if (!iso) return '?'
  return iso.slice(0, 4)
}

function dateInputValue(iso: string | null | undefined): string {
  if (!iso) return ''
  return iso.slice(0, 10)
}

function sharedForNode(
  nodeId: string,
  edges: EgoGraphEdge[],
  egoId: string,
): number {
  if (nodeId === egoId) return 0
  const e = edges.find((x) => x.target === nodeId || x.source === nodeId)
  return e?.shared_threads ?? 0
}

function edgeWidth(shared: number, maxShared: number): number {
  if (maxShared <= 0) return 1
  const t = Math.sqrt(shared / maxShared)
  return 1 + t * 3 // 1–4px
}

function nodeRadius(shared: number, maxShared: number, isEgo: boolean): number {
  if (isEgo) return 14
  if (maxShared <= 0) return 6
  return 5 + 10 * Math.sqrt(shared / maxShared)
}

export function EgoGraph({ contactId, card }: EgoGraphProps) {
  const navigate = useNavigate()
  const [maxNodes, setMaxNodes] = useState<number>(25)
  const [dateFrom, setDateFrom] = useState(() => dateInputValue(card.first_seen))
  const [dateTo, setDateTo] = useState(() => dateInputValue(card.last_seen))
  const [asTable, setAsTable] = useState(false)
  const [selectedEdge, setSelectedEdge] = useState<EgoGraphEdge | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)

  const graphQuery = useQuery({
    queryKey: ['people', contactId, 'graph', maxNodes, dateFrom, dateTo],
    queryFn: ({ signal }) => {
      const sp = new URLSearchParams()
      sp.set('depth', '1')
      sp.set('max_nodes', String(maxNodes))
      if (dateFrom) sp.set('date_from', dateFrom)
      if (dateTo) sp.set('date_to', dateTo)
      return apiGet<EgoGraphResponse>(
        `/api/people/${encodeURIComponent(contactId)}/graph?${sp}`,
        signal,
      )
    },
    enabled: !!contactId,
    retry: false,
  })

  const data = graphQuery.data
  const edges = data?.edges ?? []
  const nodes = data?.nodes ?? []
  const egoNode = nodes.find((n) => n.is_ego)
  const egoId = egoNode?.id ?? contactId
  const neighbors = nodes.filter((n) => !n.is_ego)

  const maxShared = useMemo(
    () => edges.reduce((m, e) => Math.max(m, e.shared_threads), 0) || 1,
    [edges],
  )

  const layout = useMemo(() => {
    const neighborInputs = neighbors.map((n) => ({
      id: n.id,
      shared_threads: sharedForNode(n.id, edges, egoId),
    }))
    return layoutEgoGraph(neighborInputs)
  }, [neighbors, edges, egoId])

  const posById = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>()
    for (const p of layout) {
      if (p.is_ego) m.set(egoId, { x: p.x, y: p.y })
      else m.set(p.id, { x: p.x, y: p.y })
    }
    return m
  }, [layout, egoId])

  const evidenceIds = selectedEdge?.evidence?.thread_ids?.slice(0, 20) ?? []

  const threadQueries = useQueries({
    queries: evidenceIds.map((tid) => ({
      queryKey: ['threads', tid],
      queryFn: ({ signal }: { signal?: AbortSignal }) =>
        apiGet<ThreadResponse>(
          `/api/threads/${encodeURIComponent(tid)}`,
          signal,
        ),
      enabled: !!selectedEdge && evidenceIds.length > 0,
      retry: false as const,
    })),
  })

  const nodeById = useMemo(() => {
    const m = new Map<string, EgoGraphNode>()
    for (const n of nodes) m.set(n.id, n)
    return m
  }, [nodes])

  const onEdgeSelect = useCallback(
    (edge: EgoGraphEdge) => {
      setSelectedEdge(edge)
      setSelectedNodeId(edge.target === egoId ? edge.source : edge.target)
    },
    [egoId],
  )

  /** Neighbor click: evidence panel; contact nodes also go to profile. */
  const onNeighborActivate = useCallback(
    (node: EgoGraphNode) => {
      if (node.is_ego) return
      setSelectedNodeId(node.id)
      const edge = edges.find(
        (e) => e.target === node.id || e.source === node.id,
      )
      if (edge) setSelectedEdge(edge)
      if (!node.id.startsWith('addr:')) {
        void navigate(`/people/${encodeURIComponent(node.id)}`)
      }
    },
    [edges, navigate],
  )

  const selectedNeighbor = selectedNodeId
    ? nodeById.get(selectedNodeId)
    : null

  const showAlwaysLabels = neighbors.length <= LABEL_ALWAYS_THRESHOLD

  const toSvg = (x: number, y: number, size = 360) => ({
    cx: size / 2 + x * (size / 2 - 28),
    cy: size / 2 + y * (size / 2 - 28),
  })

  const size = 360

  return (
    <section
      className="space-y-2"
      data-testid="ego-graph"
      aria-label="Co-participation graph"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium text-text-primary">
          Co-participants
        </h2>
        <span className="text-[11px] text-text-muted">
          Thread co-participation · depth 1
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <label className="flex items-center gap-1 text-text-muted">
          Max nodes
          <select
            value={maxNodes}
            onChange={(e) => setMaxNodes(Number(e.target.value))}
            className="rounded-md border border-steel bg-graphite-900 px-1 py-0.5 text-text-primary"
            data-testid="ego-max-nodes"
          >
            {MAX_NODE_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 text-text-muted">
          From
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="rounded-md border border-steel bg-graphite-900 px-1 py-0.5 text-text-primary"
            data-testid="ego-date-from"
          />
        </label>
        <label className="flex items-center gap-1 text-text-muted">
          To
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="rounded-md border border-steel bg-graphite-900 px-1 py-0.5 text-text-primary"
            data-testid="ego-date-to"
          />
        </label>
        <button
          type="button"
          className={btnClass}
          onClick={() => setAsTable((v) => !v)}
          data-testid="ego-view-toggle"
        >
          {asTable ? 'View as graph' : 'View as table'}
        </button>
      </div>

      {graphQuery.isLoading ? (
        <p className="text-[12px] text-text-muted" data-testid="ego-loading">
          Loading co-participants…
        </p>
      ) : null}

      {graphQuery.isError ? (
        <div role="alert" className="text-conflict" data-testid="ego-error">
          Failed to load graph
          <button
            type="button"
            className={`${btnClass} ml-2`}
            onClick={() => void graphQuery.refetch()}
          >
            Retry
          </button>
        </div>
      ) : null}

      {data?.truncated ? (
        <p
          className="text-[11px] text-event"
          data-testid="ego-truncated"
          role="status"
        >
          Showing {neighbors.length} of {data.total_coparticipants}{' '}
          co-participants (capped at {maxNodes})
        </p>
      ) : data ? (
        <p className="text-[11px] text-text-muted" data-testid="ego-count">
          {data.total_coparticipants} co-participant
          {data.total_coparticipants === 1 ? '' : 's'}
        </p>
      ) : null}

      {data && neighbors.length === 0 && !graphQuery.isLoading ? (
        <p className="text-[12px] text-text-muted" data-testid="ego-empty">
          No co-participants in this span
        </p>
      ) : null}

      {data && asTable ? (
        <div className="overflow-auto" data-testid="ego-table">
          <table className="w-full border-collapse text-left text-[11px]">
            <caption className="sr-only">
              Co-participants by shared thread count
            </caption>
            <thead className="sticky top-0 bg-graphite-900 text-text-muted">
              <tr className="border-b border-steel">
                <th scope="col" className="px-2 py-1">
                  Name
                </th>
                <th scope="col" className="px-2 py-1">
                  Shared threads
                </th>
                <th scope="col" className="px-2 py-1">
                  First
                </th>
                <th scope="col" className="px-2 py-1">
                  Last
                </th>
              </tr>
            </thead>
            <tbody>
              {edges.map((edge) => {
                const nid = edge.target === egoId ? edge.source : edge.target
                const node = nodeById.get(nid)
                const label = node?.label ?? nid
                return (
                  <tr
                    key={`${edge.source}-${edge.target}`}
                    className={`cursor-pointer border-b border-steel/50 hover:bg-graphite-800 ${
                      selectedEdge === edge || selectedNodeId === nid
                        ? 'bg-action/10'
                        : ''
                    }`}
                    onClick={() => {
                      setSelectedEdge(edge)
                      setSelectedNodeId(nid)
                    }}
                    data-testid={`ego-table-row-${nid}`}
                  >
                    <th scope="row" className="px-2 py-1 text-left font-sans">
                      {label}
                    </th>
                    <td className="px-2 py-1 tabular-nums">
                      {edge.shared_threads}
                    </td>
                    <td className="px-2 py-1 tabular-nums font-mono">
                      {edge.first ?? '—'}
                    </td>
                    <td className="px-2 py-1 tabular-nums font-mono">
                      {edge.last ?? '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {data && !asTable && neighbors.length > 0 ? (
        <svg
          viewBox={`0 0 ${size} ${size}`}
          className="h-auto w-full max-w-md rounded border border-steel bg-graphite-900"
          role="img"
          aria-label="Co-participation graph"
          data-testid="ego-svg"
        >
          {/* edges under nodes */}
          {edges.map((edge) => {
            const a = posById.get(edge.source)
            const b = posById.get(edge.target)
            if (!a || !b) return null
            const pa = toSvg(a.x, a.y, size)
            const pb = toSvg(b.x, b.y, size)
            const w = edgeWidth(edge.shared_threads, maxShared)
            const targetId =
              edge.target === egoId ? edge.source : edge.target
            const targetNode = nodeById.get(targetId)
            const label = targetNode?.label ?? targetId
            const selected =
              selectedEdge?.target === edge.target &&
              selectedEdge?.source === edge.source
            return (
              <line
                key={`e-${edge.source}-${edge.target}`}
                x1={pa.cx}
                y1={pa.cy}
                x2={pb.cx}
                y2={pb.cy}
                stroke={selected ? ACCENT : '#5a6270'}
                strokeWidth={w}
                strokeOpacity={selected ? 0.95 : 0.65}
                tabIndex={0}
                role="button"
                aria-label={`${label} — ${edge.shared_threads} shared threads`}
                data-testid={`ego-edge-${targetId}`}
                data-shared={edge.shared_threads}
                data-width={w}
                className="cursor-pointer outline-none focus-visible:stroke-white"
                onClick={() => onEdgeSelect(edge)}
                onKeyDown={(ev) => {
                  if (ev.key === 'Enter' || ev.key === ' ') {
                    ev.preventDefault()
                    onEdgeSelect(edge)
                  }
                }}
              />
            )
          })}

          {nodes.map((node) => {
            const pos = posById.get(node.id)
            if (!pos) return null
            const { cx, cy } = toSvg(pos.x, pos.y, size)
            const shared = sharedForNode(node.id, edges, egoId)
            const r = nodeRadius(shared, maxShared, node.is_ego)
            const focused =
              hoverId === node.id || selectedNodeId === node.id
            const showLabel =
              showAlwaysLabels || focused || node.is_ego
            const ariaShared = node.is_ego
              ? `${node.label} — ego`
              : `${node.label} — ${shared} shared threads`
            return (
              <g key={node.id}>
                {node.is_ego ? (
                  <circle
                    cx={cx}
                    cy={cy}
                    r={r + 3}
                    fill="none"
                    stroke={EGO_RING}
                    strokeWidth={2}
                    data-testid="ego-node-ring"
                  />
                ) : null}
                <circle
                  cx={cx}
                  cy={cy}
                  r={r}
                  fill={node.is_ego ? ACCENT : PEOPLE_CYAN}
                  fillOpacity={node.is_ego ? 0.95 : 0.85}
                  stroke={focused ? '#fff' : 'transparent'}
                  strokeWidth={focused ? 2 : 1}
                  tabIndex={0}
                  role="button"
                  aria-label={ariaShared}
                  data-testid={
                    node.is_ego ? 'ego-node-ego' : `ego-node-${node.id}`
                  }
                  data-shared={shared}
                  data-radius={r}
                  className="cursor-pointer outline-none focus-visible:stroke-white"
                  onClick={() => {
                    if (node.is_ego) return
                    onNeighborActivate(node)
                  }}
                  onKeyDown={(ev) => {
                    if (ev.key === 'Enter' || ev.key === ' ') {
                      ev.preventDefault()
                      if (node.is_ego) return
                      onNeighborActivate(node)
                    }
                  }}
                  onFocus={() => setHoverId(node.id)}
                  onBlur={() =>
                    setHoverId((id) => (id === node.id ? null : id))
                  }
                  onMouseEnter={() => setHoverId(node.id)}
                  onMouseLeave={() =>
                    setHoverId((id) => (id === node.id ? null : id))
                  }
                >
                  <title>{ariaShared}</title>
                </circle>
                {showLabel ? (
                  <text
                    x={cx}
                    y={cy + r + 12}
                    textAnchor="middle"
                    className="fill-text-primary"
                    style={{ fontSize: 10, fill: '#c5cad3' }}
                    data-testid={`ego-label-${node.id}`}
                  >
                    {node.label.length > 22
                      ? `${node.label.slice(0, 20)}…`
                      : node.label}
                  </text>
                ) : null}
              </g>
            )
          })}
        </svg>
      ) : null}

      {/* Address-only node note when selected without profile */}
      {selectedNeighbor?.id.startsWith('addr:') ? (
        <p
          className="text-[12px] text-text-muted"
          data-testid="ego-address-note"
        >
          {selectedNeighbor.label} — not yet a contact
        </p>
      ) : null}

      {selectedEdge ? (
        <div
          className="space-y-2 rounded border border-steel bg-graphite-900 p-2"
          data-testid="ego-evidence"
        >
          <p className="text-[12px] text-text-primary">
            {selectedEdge.shared_threads} shared thread
            {selectedEdge.shared_threads === 1 ? '' : 's'} with{' '}
            {selectedNeighbor?.label ??
              (selectedEdge.target === egoId
                ? selectedEdge.source
                : selectedEdge.target)}{' '}
            ({yearOf(selectedEdge.first)}–{yearOf(selectedEdge.last)})
          </p>
          <ul className="max-h-48 space-y-1 overflow-auto" data-testid="ego-evidence-list">
            {evidenceIds.map((tid, i) => {
              const q = threadQueries[i]
              const t = q?.data
              const subject = t?.subject ?? (q?.isLoading ? 'Loading…' : tid)
              const date = t?.date_range?.from ?? t?.messages?.[0]?.date ?? null
              const sender =
                t?.participants?.[0]?.name ||
                t?.participants?.[0]?.address ||
                t?.messages?.[0]?.sender_name ||
                t?.messages?.[0]?.sender_address ||
                ''
              const firstMsg = t?.messages?.[0]?.id
              return (
                <li key={tid}>
                  {firstMsg ? (
                    <Link
                      to={`/source/${encodeURIComponent(firstMsg)}?thread=1`}
                      className="block w-full truncate rounded px-1.5 py-1 text-left text-[11px] text-text-primary hover:bg-graphite-800"
                      data-testid={`ego-evidence-row-${tid}`}
                    >
                      <span className="font-medium">{subject || '(no subject)'}</span>
                      <span className="ml-1 text-text-muted">
                        {date ? date.slice(0, 10) : ''}
                        {sender ? ` · ${sender}` : ''}
                      </span>
                    </Link>
                  ) : (
                    <div
                      className="truncate rounded px-1.5 py-1 text-[11px] text-text-muted"
                      data-testid={`ego-evidence-row-${tid}`}
                    >
                      {q?.isError ? `Failed to load ${tid}` : subject}
                    </div>
                  )}
                </li>
              )
            })}
            {evidenceIds.length === 0 ? (
              <li className="text-[11px] text-text-muted">No thread ids</li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </section>
  )
}
