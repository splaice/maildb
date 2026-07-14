import { useLayoutEffect, useEffect } from 'react'

import { useWorkingSetStore } from './store'
import { decodeState, encodeState, type UrlWorkingState } from './urlState'

const REPLACE_DEBOUNCE_MS = 300

/** Module-level sync so multiple useUrlSync mounts share one history writer. */
let syncRefCount = 0
let storeUnsub: (() => void) | null = null
let replaceTimer: ReturnType<typeof setTimeout> | null = null
let lastWritten = ''

function locationSearchParams(): URLSearchParams {
  return new URLSearchParams(window.location.search)
}

function buildSearchUrl(params: URLSearchParams): string {
  const qs = params.toString()
  const { pathname, hash } = window.location
  return qs ? `${pathname}?${qs}${hash}` : `${pathname}${hash}`
}

function currentFullPath(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`
}

function clearHistoryIntent(): void {
  if (useWorkingSetStore.getState().historyIntent !== 'silent') {
    useWorkingSetStore.setState({ historyIntent: 'silent' })
  }
}

function flushReplace(url: string): void {
  if (url === currentFullPath()) {
    lastWritten = url
    return
  }
  window.history.replaceState(window.history.state, '', url)
  lastWritten = url
}

function onStoreChange(): void {
  const state = useWorkingSetStore.getState()
  const intent = state.historyIntent
  if (intent === 'silent') return

  const params = encodeState({
    scope: state.scope,
    viewport: state.viewport,
    aggregation: state.aggregation,
    view: state.view,
    selection: state.selection,
  })
  const url = buildSearchUrl(params)

  if (intent === 'analytical') {
    if (replaceTimer != null) {
      clearTimeout(replaceTimer)
      replaceTimer = null
    }
    // Coalesce: skip push when the encoded URL is already current.
    if (url !== currentFullPath() && url !== lastWritten) {
      window.history.pushState(window.history.state, '', url)
      lastWritten = url
    }
    clearHistoryIntent()
    return
  }

  // transient → debounced replaceState (300ms)
  if (replaceTimer != null) clearTimeout(replaceTimer)
  replaceTimer = setTimeout(() => {
    replaceTimer = null
    flushReplace(url)
    clearHistoryIntent()
  }, REPLACE_DEBOUNCE_MS)
}

function onPopState(): void {
  if (replaceTimer != null) {
    clearTimeout(replaceTimer)
    replaceTimer = null
  }
  const decoded = decodeState(locationSearchParams())
  useWorkingSetStore.getState().hydrate(decoded)
  lastWritten = currentFullPath()
}

function startSync(): void {
  // Idempotent: never stack store subscriptions or popstate handlers.
  stopSync()
  lastWritten = currentFullPath()
  storeUnsub = useWorkingSetStore.subscribe(onStoreChange)
  window.addEventListener('popstate', onPopState)
}

function stopSync(): void {
  storeUnsub?.()
  storeUnsub = null
  window.removeEventListener('popstate', onPopState)
  if (replaceTimer != null) {
    clearTimeout(replaceTimer)
    replaceTimer = null
  }
}

/**
 * URL ↔ working-set sync.
 *
 * Decision: do NOT use react-router's `useSearchParams`. That helper always
 * pushes history entries and re-renders the route tree. Analytical state needs
 * selective push vs debounced replace; react-router only owns the pathname.
 * We write with `window.history` and restore on `popstate`.
 *
 * The writer is process-wide (ref-counted) so remounts / StrictMode do not
 * stack duplicate history entries.
 */
export function useUrlSync(): void {
  // Hydrate from the current URL before paint / first fetch (deep-link entry).
  useLayoutEffect(() => {
    const decoded = decodeState(locationSearchParams())
    useWorkingSetStore.getState().hydrate(decoded)
    lastWritten = currentFullPath()
  }, [])

  useEffect(() => {
    if (syncRefCount === 0) startSync()
    syncRefCount += 1
    return () => {
      syncRefCount -= 1
      if (syncRefCount === 0) stopSync()
    }
  }, [])
}

/** Test helper: apply current store slice to the URL immediately (replace). */
export function writeStoreToUrlNow(): void {
  const s = useWorkingSetStore.getState()
  const slice: UrlWorkingState = {
    scope: s.scope,
    viewport: s.viewport,
    aggregation: s.aggregation,
    view: s.view,
    selection: s.selection,
  }
  const url = buildSearchUrl(encodeState(slice))
  window.history.replaceState(window.history.state, '', url)
  lastWritten = url
}

/** Test helper: tear down module-level sync (for isolation). */
export function resetUrlSyncForTests(): void {
  syncRefCount = 0
  stopSync()
  lastWritten = ''
}
