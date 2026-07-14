import { create } from 'zustand'

import type { QueryScope, QueryScopeDate } from '../api/types'
import type { Viewport } from '../chronicle/timeScale'
import {
  type Aggregation,
  DEFAULT_URL_STATE,
  type UrlWorkingState,
  type ViewMode,
} from './urlState'

/**
 * History intent set by actions so useUrlSync can apply the URL contract:
 * - transient → debounced replaceState (pan/zoom)
 * - analytical → immediate pushState (scope, brush-apply, view, …)
 * - silent → no URL write (hydrate, brush drag, result count)
 */
export type HistoryIntent = 'transient' | 'analytical' | 'silent'

export interface WorkingSetState {
  scope: QueryScope
  viewport: Viewport | null
  aggregation: Aggregation
  view: ViewMode
  brush: Viewport | null
  /** Live framing for the scope bar; not URL-serialised. */
  resultCount: number | null
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
  setResultCount: (count: number | null) => void
  hydrate: (decoded: UrlWorkingState) => void
}

const emptyScope = (): QueryScope => ({})

export const useWorkingSetStore = create<WorkingSetState>((set, get) => ({
  scope: { ...DEFAULT_URL_STATE.scope },
  viewport: DEFAULT_URL_STATE.viewport,
  aggregation: DEFAULT_URL_STATE.aggregation,
  view: DEFAULT_URL_STATE.view,
  brush: null,
  resultCount: null,
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

  setResultCount: (count) => set({ resultCount: count, historyIntent: 'silent' }),

  hydrate: (decoded) =>
    set({
      scope: decoded.scope ?? emptyScope(),
      viewport: decoded.viewport,
      aggregation: decoded.aggregation,
      view: decoded.view,
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
    resultCount: null,
    historyIntent: 'silent',
  })
}
