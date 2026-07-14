import { create } from 'zustand'

import type { QueryScope, QueryScopeDate } from '../api/types'
import type { Viewport } from '../chronicle/timeScale'
import {
  type Aggregation,
  DEFAULT_LANES,
  DEFAULT_URL_STATE,
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
  historyIntent: HistoryIntent

  setViewport: (viewport: Viewport) => void
  setBrush: (brush: Viewport | null) => void
  applyBrushAsViewport: () => void
  setScopeDate: (date: QueryScopeDate | null) => void
  addMailbox: (mailbox: string) => void
  removeMailbox: (mailbox: string) => void
  addSender: (sender: string) => void
  removeSender: (sender: string) => void
  clearScope: () => void
  setView: (view: ViewMode) => void
  setAggregation: (aggregation: Aggregation) => void
  setSelection: (selection: Selection) => void
  clearMessageToBucket: () => void
  setResultCount: (count: number | null) => void
  setTimelineUnit: (unit: string | null) => void
  toggleLane: (key: string) => void
  moveLane: (key: string, dir: MoveLaneDir) => void
  hydrate: (decoded: UrlWorkingState) => void
}

const emptyScope = (): QueryScope => ({})

export const useWorkingSetStore = create<WorkingSetState>((set, get) => ({
  scope: { ...DEFAULT_URL_STATE.scope },
  viewport: DEFAULT_URL_STATE.viewport,
  aggregation: DEFAULT_URL_STATE.aggregation,
  view: DEFAULT_URL_STATE.view,
  brush: null,
  selection: DEFAULT_URL_STATE.selection,
  priorBucket: null,
  resultCount: null,
  timelineUnit: null,
  lanes: [...DEFAULT_LANES],
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
    selection: null,
    priorBucket: null,
    resultCount: null,
    timelineUnit: null,
    lanes: [...DEFAULT_LANES],
    historyIntent: 'silent',
  })
}
