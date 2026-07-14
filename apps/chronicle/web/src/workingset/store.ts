import { create } from 'zustand'

import type { QueryScope, QueryScopeDate, SearchMode } from '../api/types'
import type { Viewport } from '../chronicle/timeScale'
import {
  type Aggregation,
  DEFAULT_LANES,
  DEFAULT_URL_STATE,
  type ResearchGrouping,
  resolveLanes,
  type Selection,
  type UrlWorkingState,
  type ViewMode,
} from './urlState'

/**
 * History intent set by actions so useUrlSync can apply the URL contract:
 * - transient → debounced replaceState (pan/zoom, selection)
 * - analytical → immediate pushState (scope, brush-apply, view, lanes, …)
 * - silent → no URL write (hydrate, brush drag, result count)
 */
export type HistoryIntent = 'transient' | 'analytical' | 'silent'

export type MoveLaneDir = 'up' | 'down'

export interface WorkingSetState {
  scope: QueryScope
  viewport: Viewport | null
  aggregation: Aggregation
  view: ViewMode
  brush: Viewport | null
  /**
   * Focus-mode period (temporary analytical workspace). URL params `ff`/`ft`.
   * Analytical intent on enter/exit.
   */
  focus: Viewport | null
  /**
   * Compare mode: two date ranges (URL params `ca`/`cb`). Analytical on
   * enter/exit/range update. Null when not comparing.
   */
  compare: { a: Viewport; b: Viewport } | null
  /** Timeline / inspector selection; URL param `sel`; transient intent. */
  selection: Selection
  /**
   * Last bucket selection — used by MessageCard Close to restore bucket view.
   * Not URL-serialised.
   */
  priorBucket: Extract<Selection, { kind: 'bucket' }> | null
  /** Live framing for the scope bar; not URL-serialised. */
  resultCount: number | null
  /** Current timeline aggregation unit (for bucket date_to); not URL-serialised. */
  timelineUnit: string | null
  /** Ordered visible lane keys; URL param `ln`; analytical intent. */
  lanes: string[]
  /** Research Desk free-text query (URL param `q`); analytical when committed. */
  query: string
  /** Research retrieval mode (URL param `mode`); analytical. */
  mode: SearchMode
  /** Research result grouping (URL param `grp`); analytical. */
  grouping: ResearchGrouping
  historyIntent: HistoryIntent

  setViewport: (viewport: Viewport) => void
  setBrush: (brush: Viewport | null) => void
  applyBrushAsViewport: () => void
  /** Enter focus mode on a period; clears brush; analytical. */
  setFocus: (focus: Viewport) => void
  /** Exit focus mode; analytical (URL drops ff/ft; Back is equivalent). */
  exitFocus: () => void
  /**
   * Explicit confirmation: copy focus range into the scope date filter and
   * exit focus (always temporary until this action). Analytical.
   */
  applyFocusAsScopeDate: () => void
  /** Enter or update compare mode with two ranges; clears brush; analytical. */
  setCompare: (compare: { a: Viewport; b: Viewport }) => void
  /** Update one side of an active compare (brush re-fetch); analytical. */
  setCompareSide: (side: 'a' | 'b', range: Viewport) => void
  /** Exit compare mode; analytical (URL drops ca/cb; Back is equivalent). */
  exitCompare: () => void
  setScopeDate: (date: QueryScopeDate | null) => void
  addMailbox: (mailbox: string) => void
  removeMailbox: (mailbox: string) => void
  addSender: (sender: string) => void
  removeSender: (sender: string) => void
  setHasAttachment: (value: boolean | null) => void
  /** Replace scope analytically (research chip edits merge into store). */
  setScope: (scope: QueryScope) => void
  /** Merge a partial scope patch analytically. */
  patchScope: (patch: Partial<QueryScope>) => void
  clearScope: () => void
  setView: (view: ViewMode) => void
  setAggregation: (aggregation: Aggregation) => void
  setSelection: (selection: Selection) => void
  clearMessageToBucket: () => void
  setResultCount: (count: number | null) => void
  setTimelineUnit: (unit: string | null) => void
  toggleLane: (key: string) => void
  moveLane: (key: string, dir: MoveLaneDir) => void
  setQuery: (query: string) => void
  setMode: (mode: SearchMode) => void
  setGrouping: (grouping: ResearchGrouping) => void
  hydrate: (decoded: UrlWorkingState) => void
}

/** Format epoch ms as UTC ISO date (`YYYY-MM-DD`) for scope date filters. */
function toIsoDate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}

const emptyScope = (): QueryScope => ({})

export const useWorkingSetStore = create<WorkingSetState>((set, get) => ({
  scope: { ...DEFAULT_URL_STATE.scope },
  viewport: DEFAULT_URL_STATE.viewport,
  aggregation: DEFAULT_URL_STATE.aggregation,
  view: DEFAULT_URL_STATE.view,
  brush: null,
  focus: null,
  compare: null,
  selection: DEFAULT_URL_STATE.selection,
  priorBucket: null,
  resultCount: null,
  timelineUnit: null,
  lanes: [...DEFAULT_LANES],
  query: DEFAULT_URL_STATE.query ?? '',
  mode: DEFAULT_URL_STATE.mode ?? 'hybrid',
  grouping: DEFAULT_URL_STATE.grouping ?? 'none',
  historyIntent: 'silent',

  setViewport: (viewport) =>
    set({ viewport, historyIntent: 'transient' }),

  setBrush: (brush) => set({ brush, historyIntent: 'silent' }),

  applyBrushAsViewport: () => {
    const { brush } = get()
    if (!brush) return
    set({
      viewport: brush,
      brush: null,
      historyIntent: 'analytical',
    })
  },

  setFocus: (focus) => {
    if (!focus || !(focus.toMs > focus.fromMs)) return
    set({
      focus: { fromMs: focus.fromMs, toMs: focus.toMs },
      brush: null,
      historyIntent: 'analytical',
    })
  },

  exitFocus: () => {
    if (get().focus == null) return
    set({ focus: null, historyIntent: 'analytical' })
  },

  applyFocusAsScopeDate: () => {
    const { focus, scope } = get()
    if (!focus) return
    const nextScope = {
      ...scope,
      date: {
        from: toIsoDate(focus.fromMs),
        to: toIsoDate(focus.toMs),
      },
    }
    set({
      scope: nextScope,
      focus: null,
      historyIntent: 'analytical',
    })
  },

  setCompare: (compare) => {
    if (
      !compare ||
      !(compare.a.toMs > compare.a.fromMs) ||
      !(compare.b.toMs > compare.b.fromMs)
    ) {
      return
    }
    set({
      compare: {
        a: { fromMs: compare.a.fromMs, toMs: compare.a.toMs },
        b: { fromMs: compare.b.fromMs, toMs: compare.b.toMs },
      },
      brush: null,
      historyIntent: 'analytical',
    })
  },

  setCompareSide: (side, range) => {
    const current = get().compare
    if (!current || !(range.toMs > range.fromMs)) return
    set({
      compare: {
        ...current,
        [side]: { fromMs: range.fromMs, toMs: range.toMs },
      },
      brush: null,
      historyIntent: 'analytical',
    })
  },

  exitCompare: () => {
    if (get().compare == null) return
    set({ compare: null, historyIntent: 'analytical' })
  },

  setScopeDate: (date) => {
    const scope = { ...get().scope }
    if (!date || (!date.from && !date.to)) {
      delete scope.date
    } else {
      scope.date = {
        ...(date.from ? { from: date.from } : {}),
        ...(date.to ? { to: date.to } : {}),
      }
    }
    set({ scope, historyIntent: 'analytical' })
  },

  addMailbox: (mailbox) => {
    const trimmed = mailbox.trim()
    if (!trimmed) return
    const current = get().scope.mailboxes ?? []
    if (current.includes(trimmed)) return
    set({
      scope: { ...get().scope, mailboxes: [...current, trimmed] },
      historyIntent: 'analytical',
    })
  },

  removeMailbox: (mailbox) => {
    const current = get().scope.mailboxes ?? []
    const next = current.filter((m) => m !== mailbox)
    const scope = { ...get().scope }
    if (next.length === 0) delete scope.mailboxes
    else scope.mailboxes = next
    set({ scope, historyIntent: 'analytical' })
  },

  addSender: (sender) => {
    const trimmed = sender.trim()
    if (!trimmed) return
    const current = get().scope.senders ?? []
    if (current.includes(trimmed)) return
    set({
      scope: { ...get().scope, senders: [...current, trimmed] },
      historyIntent: 'analytical',
    })
  },

  removeSender: (sender) => {
    const current = get().scope.senders ?? []
    const next = current.filter((s) => s !== sender)
    const scope = { ...get().scope }
    if (next.length === 0) delete scope.senders
    else scope.senders = next
    set({ scope, historyIntent: 'analytical' })
  },

  setHasAttachment: (value) => {
    const scope = { ...get().scope }
    if (value == null) delete scope.has_attachment
    else scope.has_attachment = value
    set({ scope, historyIntent: 'analytical' })
  },

  setScope: (scope) => set({ scope: { ...scope }, historyIntent: 'analytical' }),

  patchScope: (patch) => {
    const next: QueryScope = { ...get().scope, ...patch }
    // Drop nullish / empty arrays so pristine checks stay honest.
    if (patch.date === null) delete next.date
    if (patch.has_attachment === null) delete next.has_attachment
    if (patch.subject_contains === null || patch.subject_contains === '') {
      delete next.subject_contains
    }
    if (patch.free_text === null || patch.free_text === '') delete next.free_text
    for (const key of [
      'mailboxes',
      'senders',
      'recipients',
      'participants',
      'file_types',
      'filenames',
      'source_types',
    ] as const) {
      const v = next[key]
      if (Array.isArray(v) && v.length === 0) delete next[key]
    }
    set({ scope: next, historyIntent: 'analytical' })
  },

  clearScope: () =>
    set({ scope: emptyScope(), historyIntent: 'analytical' }),

  setView: (view) => set({ view, historyIntent: 'analytical' }),

  setAggregation: (aggregation) =>
    set({ aggregation, historyIntent: 'analytical' }),

  setSelection: (selection) => {
    const prev = get().selection
    let priorBucket = get().priorBucket
    if (selection?.kind === 'bucket') {
      priorBucket = selection
    } else if (selection?.kind === 'message' && prev?.kind === 'bucket') {
      priorBucket = prev
    } else if (selection == null) {
      priorBucket = null
    }
    set({ selection, priorBucket, historyIntent: 'transient' })
  },

  clearMessageToBucket: () => {
    const prior = get().priorBucket
    set({
      selection: prior,
      historyIntent: 'transient',
    })
  },

  setResultCount: (count) => set({ resultCount: count, historyIntent: 'silent' }),

  setTimelineUnit: (unit) => set({ timelineUnit: unit, historyIntent: 'silent' }),

  toggleLane: (key) => {
    const current = get().lanes
    const idx = current.indexOf(key)
    let next: string[]
    if (idx >= 0) {
      // Keep at least one lane visible.
      if (current.length <= 1) return
      next = current.filter((k) => k !== key)
    } else {
      next = [...current, key]
    }
    set({ lanes: next, historyIntent: 'analytical' })
  },

  moveLane: (key, dir) => {
    const current = get().lanes
    const idx = current.indexOf(key)
    if (idx < 0) return
    const swapWith = dir === 'up' ? idx - 1 : idx + 1
    if (swapWith < 0 || swapWith >= current.length) return
    const next = [...current]
    ;[next[idx], next[swapWith]] = [next[swapWith]!, next[idx]!]
    set({ lanes: next, historyIntent: 'analytical' })
  },

  setQuery: (query) => set({ query, historyIntent: 'analytical' }),

  setMode: (mode) => set({ mode, historyIntent: 'analytical' }),

  setGrouping: (grouping) => set({ grouping, historyIntent: 'analytical' }),

  hydrate: (decoded) =>
    set({
      scope: decoded.scope ?? emptyScope(),
      viewport: decoded.viewport,
      aggregation: decoded.aggregation,
      view: decoded.view,
      selection: decoded.selection ?? null,
      priorBucket:
        decoded.selection?.kind === 'bucket' ? decoded.selection : get().priorBucket,
      lanes: resolveLanes(decoded.lanes),
      focus: decoded.focus ?? null,
      compare: decoded.compare ?? null,
      query: decoded.query ?? '',
      mode: decoded.mode ?? 'hybrid',
      grouping: decoded.grouping ?? 'none',
      brush: null,
      historyIntent: 'silent',
    }),
}))

/** Reset store to defaults (tests). Does not touch the URL. */
export function resetWorkingSetStore(): void {
  useWorkingSetStore.setState({
    scope: emptyScope(),
    viewport: null,
    aggregation: 'auto',
    view: 'canvas',
    brush: null,
    focus: null,
    compare: null,
    selection: null,
    priorBucket: null,
    resultCount: null,
    timelineUnit: null,
    lanes: [...DEFAULT_LANES],
    query: '',
    mode: 'hybrid',
    grouping: 'none',
    historyIntent: 'silent',
  })
}
