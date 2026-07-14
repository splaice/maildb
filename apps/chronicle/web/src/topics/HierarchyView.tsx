import { useCallback, useMemo, useRef, useState, type KeyboardEvent } from 'react'

import type { TopicTreeNode } from '../api/types'
import { OriginBadge } from './originBadge'

export interface HierarchyViewProps {
  topics: TopicTreeNode[]
  selectedId: string | null
  onSelect: (id: string) => void
}

type FlatNode = {
  id: string
  label: string
  origin: string
  member_count: number
  hidden: boolean
  depth: number
  hasChildren: boolean
  /** Synthetic group for hidden topics. */
  isHiddenGroup?: boolean
}

function flattenVisible(
  nodes: TopicTreeNode[],
  expanded: Set<string>,
  depth: number,
): FlatNode[] {
  const out: FlatNode[] = []
  for (const n of nodes) {
    const hasChildren = (n.children?.length ?? 0) > 0
    out.push({
      id: n.id,
      label: n.label,
      origin: n.origin,
      member_count: n.member_count,
      hidden: n.hidden,
      depth,
      hasChildren,
    })
    if (hasChildren && expanded.has(n.id)) {
      out.push(...flattenVisible(n.children, expanded, depth + 1))
    }
  }
  return out
}

function filterTree(nodes: TopicTreeNode[], q: string): TopicTreeNode[] {
  if (!q) return nodes
  const lower = q.toLowerCase()
  const walk = (list: TopicTreeNode[]): TopicTreeNode[] => {
    const out: TopicTreeNode[] = []
    for (const n of list) {
      const kids = walk(n.children ?? [])
      const selfMatch =
        n.label.toLowerCase().includes(lower) ||
        (n.top_terms ?? []).some((t) => t.toLowerCase().includes(lower))
      if (selfMatch || kids.length > 0) {
        out.push({ ...n, children: kids })
      }
    }
    return out
  }
  return walk(nodes)
}

/**
 * Keyboard-navigable topic hierarchy (roles tree/treeitem; WAI-ARIA arrows).
 * Hidden topics under a collapsed "Hidden (N)" group. TA-001 default view.
 */
export function HierarchyView({ topics, selectedId, onSelect }: HierarchyViewProps) {
  const [filter, setFilter] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [hiddenOpen, setHiddenOpen] = useState(false)
  const [focusId, setFocusId] = useState<string | null>(null)
  const treeRef = useRef<HTMLDivElement>(null)

  const { visible, hidden } = useMemo(() => {
    const filtered = filterTree(topics, filter.trim())
    const vis: TopicTreeNode[] = []
    const hid: TopicTreeNode[] = []
    const split = (list: TopicTreeNode[]) => {
      for (const n of list) {
        if (n.hidden) hid.push(n)
        else vis.push({ ...n, children: (n.children ?? []).filter((c) => !c.hidden) })
      }
    }
    split(filtered)
    // Also collect nested hidden from original filtered tree.
    const collectHidden = (list: TopicTreeNode[]) => {
      for (const n of list) {
        if (n.hidden && !hid.some((h) => h.id === n.id)) hid.push(n)
        collectHidden(n.children ?? [])
      }
    }
    collectHidden(filtered)
    return { visible: vis, hidden: hid }
  }, [topics, filter])

  const flat = useMemo(() => {
    const rows = flattenVisible(visible, expanded, 0)
    if (hidden.length > 0) {
      const groupId = '__hidden__'
      rows.push({
        id: groupId,
        label: `Hidden (${hidden.length})`,
        origin: '',
        member_count: hidden.length,
        hidden: true,
        depth: 0,
        hasChildren: true,
        isHiddenGroup: true,
      })
      if (hiddenOpen) {
        for (const h of hidden) {
          rows.push({
            id: h.id,
            label: h.label,
            origin: h.origin,
            member_count: h.member_count,
            hidden: true,
            depth: 1,
            hasChildren: false,
          })
        }
      }
    }
    return rows
  }, [visible, hidden, expanded, hiddenOpen])

  const toggleExpand = useCallback((id: string, isHiddenGroup?: boolean) => {
    if (isHiddenGroup) {
      setHiddenOpen((v) => !v)
      return
    }
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const focusIndex = flat.findIndex((r) => r.id === (focusId ?? selectedId))
  const safeFocus = focusIndex >= 0 ? focusIndex : 0

  const onKeyDown = (e: KeyboardEvent) => {
    if (flat.length === 0) return
    const current = flat[safeFocus]
    if (!current) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      const next = flat[Math.min(flat.length - 1, safeFocus + 1)]
      if (next) setFocusId(next.id)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      const prev = flat[Math.max(0, safeFocus - 1)]
      if (prev) setFocusId(prev.id)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      if (current.hasChildren) {
        if (current.isHiddenGroup) {
          if (!hiddenOpen) setHiddenOpen(true)
          else {
            const child = flat[safeFocus + 1]
            if (child) setFocusId(child.id)
          }
        } else if (!expanded.has(current.id)) {
          setExpanded((prev) => new Set(prev).add(current.id))
        } else {
          const child = flat[safeFocus + 1]
          if (child && child.depth > current.depth) setFocusId(child.id)
        }
      }
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      if (current.isHiddenGroup && hiddenOpen) {
        setHiddenOpen(false)
      } else if (current.hasChildren && expanded.has(current.id)) {
        setExpanded((prev) => {
          const next = new Set(prev)
          next.delete(current.id)
          return next
        })
      } else if (current.depth > 0) {
        // Move to parent: previous row with smaller depth.
        for (let i = safeFocus - 1; i >= 0; i--) {
          if (flat[i]!.depth < current.depth) {
            setFocusId(flat[i]!.id)
            break
          }
        }
      }
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (current.isHiddenGroup) {
        setHiddenOpen((v) => !v)
      } else {
        onSelect(current.id)
      }
    } else if (e.key === 'Home') {
      e.preventDefault()
      if (flat[0]) setFocusId(flat[0].id)
    } else if (e.key === 'End') {
      e.preventDefault()
      const last = flat[flat.length - 1]
      if (last) setFocusId(last.id)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="hierarchy-view">
      <label className="flex items-center gap-2 text-[12px] text-text-muted">
        <span className="sr-only">Filter topics</span>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter topics…"
          className="w-full max-w-sm rounded-md border border-steel bg-graphite-900 px-2 py-1 text-text-primary"
          data-testid="topic-tree-filter"
        />
      </label>
      <div
        ref={treeRef}
        role="tree"
        aria-label="Topic hierarchy"
        tabIndex={0}
        className="min-h-0 flex-1 overflow-auto rounded border border-steel bg-graphite-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
        onKeyDown={onKeyDown}
        data-testid="topic-tree"
      >
        {flat.length === 0 ? (
          <p className="p-3 text-[12px] text-text-muted">No topics</p>
        ) : (
          <ul className="list-none p-1" role="presentation">
            {flat.map((row) => {
              const isExpanded = row.isHiddenGroup
                ? hiddenOpen
                : expanded.has(row.id)
              const isSelected = !row.isHiddenGroup && row.id === selectedId
              const isFocused = row.id === (focusId ?? selectedId ?? flat[0]?.id)
              return (
                <li
                  key={row.id}
                  role="treeitem"
                  aria-expanded={row.hasChildren ? isExpanded : undefined}
                  aria-selected={isSelected}
                  aria-level={row.depth + 1}
                  tabIndex={isFocused ? 0 : -1}
                  className={`flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-[12px] ${
                    isSelected
                      ? 'bg-action/20 text-text-primary'
                      : 'text-text-primary hover:bg-graphite-800'
                  } ${isFocused ? 'ring-1 ring-action/60' : ''}`}
                  style={{ paddingLeft: `${8 + row.depth * 16}px` }}
                  data-testid={
                    row.isHiddenGroup
                      ? 'topic-hidden-group'
                      : `topic-treeitem-${row.id}`
                  }
                  onClick={() => {
                    setFocusId(row.id)
                    if (row.isHiddenGroup) toggleExpand(row.id, true)
                    else onSelect(row.id)
                  }}
                  onDoubleClick={() => {
                    if (row.hasChildren) toggleExpand(row.id, row.isHiddenGroup)
                  }}
                >
                  {row.hasChildren ? (
                    <button
                      type="button"
                      aria-label={isExpanded ? 'Collapse' : 'Expand'}
                      className="w-4 shrink-0 text-text-muted"
                      onClick={(e) => {
                        e.stopPropagation()
                        toggleExpand(row.id, row.isHiddenGroup)
                      }}
                      data-testid={`topic-expand-${row.id}`}
                    >
                      {isExpanded ? '▾' : '▸'}
                    </button>
                  ) : (
                    <span className="inline-block w-4 shrink-0" />
                  )}
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {row.label}
                  </span>
                  {!row.isHiddenGroup && row.origin ? (
                    <OriginBadge origin={row.origin} />
                  ) : null}
                  <span
                    className="shrink-0 font-mono tabular-nums text-text-muted"
                    data-testid="topic-member-count"
                  >
                    {row.member_count.toLocaleString()}
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
