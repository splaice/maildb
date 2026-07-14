import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { apiPost, ApiError } from '../api/client'
import type {
  DeskMode,
  QueryScope,
  SearchMode,
  SearchRequest,
  SearchResponse,
  SearchResult,
} from '../api/types'
import { AnswerBlock } from '../ask/AnswerBlock'
import { isScopePristine } from '../workingset/urlState'
import type { ResearchGrouping } from '../workingset/urlState'
import { useWorkingSetStore } from '../workingset/store'
import { ConstraintChips } from './ConstraintChips'
import { groupResults } from './grouping'
import { ResultCard } from './ResultCard'
import {
  applyChipEdit,
  chipsFromSearchScope,
  removeChipFromScope,
  type ConstraintChip,
} from './scopeChips'

const MODES: { value: SearchMode; label: string; description: string }[] = [
  {
    value: 'hybrid',
    label: 'Hybrid',
    description: 'Combines exact matches with semantic ranking (default).',
  },
  {
    value: 'exact',
    label: 'Exact',
    description: 'Phrase and metadata matches only; no embedding expansion.',
  },
  {
    value: 'semantic',
    label: 'Semantic',
    description: 'Conceptual similarity when exact wording is unknown.',
  },
]

const GROUPINGS: { value: ResearchGrouping; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'thread', label: 'Thread' },
  { value: 'year', label: 'Year' },
  { value: 'mailbox', label: 'Mailbox' },
]

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

function stripFreeText(scope: QueryScope): QueryScope {
  const next = { ...scope }
  delete next.free_text
  return next
}

function residualQuery(scope: QueryScope, fallback: string): string {
  if (scope.free_text != null && scope.free_text !== '') return scope.free_text
  return fallback
}

export function ResearchDeskPage() {
  const navigate = useNavigate()

  const storeQuery = useWorkingSetStore((s) => s.query)
  const mode = useWorkingSetStore((s) => s.mode)
  const grouping = useWorkingSetStore((s) => s.grouping)
  const scope = useWorkingSetStore((s) => s.scope)
  const selection = useWorkingSetStore((s) => s.selection)
  const setQuery = useWorkingSetStore((s) => s.setQuery)
  const setMode = useWorkingSetStore((s) => s.setMode)
  const setGrouping = useWorkingSetStore((s) => s.setGrouping)
  const setScope = useWorkingSetStore((s) => s.setScope)
  const setSelection = useWorkingSetStore((s) => s.setSelection)
  const setViewport = useWorkingSetStore((s) => s.setViewport)
  const addMailbox = useWorkingSetStore((s) => s.addMailbox)
  const setScopeDate = useWorkingSetStore((s) => s.setScopeDate)
  const setHasAttachment = useWorkingSetStore((s) => s.setHasAttachment)

  const [inputValue, setInputValue] = useState(storeQuery)
  const [deskMode, setDeskMode] = useState<DeskMode>('search')
  const [results, setResults] = useState<SearchResult[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [unsupported, setUnsupported] = useState<string[]>([])
  const [responseScope, setResponseScope] = useState<QueryScope | null>(null)
  const [facets, setFacets] = useState<SearchResponse['facets']>(null)
  const [degraded, setDegraded] = useState<Record<string, string> | null>(null)
  const [duplicatesSuppressed, setDuplicatesSuppressed] = useState(0)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasSearched, setHasSearched] = useState(false)
  const [askQuestion, setAskQuestion] = useState('')
  const [askRunId, setAskRunId] = useState(0)
  const [askScope, setAskScope] = useState<QueryScope>({})
  const abortRef = useRef<AbortController | null>(null)
  const freeTextRef = useRef(storeQuery)

  // Keep local input in sync when URL/store hydrates query.
  useEffect(() => {
    setInputValue(storeQuery)
    freeTextRef.current = storeQuery
  }, [storeQuery])

  const runSearch = useCallback(
    async (opts: {
      query: string
      mode: SearchMode
      scope: QueryScope
      cursor?: string | null
      append?: boolean
    }) => {
      abortRef.current?.abort()
      const ac = new AbortController()
      abortRef.current = ac

      if (opts.append) setLoadingMore(true)
      else setLoading(true)
      setError(null)

      const body: SearchRequest = {
        query: opts.query,
        mode: opts.mode,
        scope: stripFreeText(opts.scope),
        limit: 25,
        cursor: opts.cursor ?? null,
        include_facets: !opts.append,
      }

      try {
        const res = await apiPost<SearchResponse>('/api/search', body, ac.signal)
        if (ac.signal.aborted) return

        const residual = residualQuery(res.scope, opts.query)
        freeTextRef.current = residual

        // After parse: residual free text becomes query; constraints live in store.
        const scopeWithoutFt = stripFreeText(res.scope)
        setScope(scopeWithoutFt)
        setQuery(residual)
        setInputValue(residual)

        setResponseScope(res.scope)
        setUnsupported(res.unsupported ?? [])
        setDegraded(res.degraded ?? null)
        setDuplicatesSuppressed(res.duplicates_suppressed ?? 0)
        setNextCursor(res.next_cursor)
        if (!opts.append && res.facets) setFacets(res.facets)
        if (opts.append) {
          setResults((prev) => [...prev, ...res.results])
        } else {
          setResults(res.results)
        }
        setHasSearched(true)
      } catch (err) {
        if (ac.signal.aborted) return
        if (err instanceof DOMException && err.name === 'AbortError') return
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : 'Search failed'
        setError(msg)
        if (!opts.append) setHasSearched(true)
      } finally {
        if (!ac.signal.aborted) {
          setLoading(false)
          setLoadingMore(false)
        }
      }
    },
    [setQuery, setScope],
  )

  // Initial search when arriving with q or non-empty scope.
  const bootRef = useRef(false)
  useEffect(() => {
    if (bootRef.current) return
    bootRef.current = true
    const q = useWorkingSetStore.getState().query
    const sc = useWorkingSetStore.getState().scope
    const m = useWorkingSetStore.getState().mode
    if (q.trim() || !isScopePristine(sc)) {
      void runSearch({ query: q, mode: m, scope: sc })
    }
  }, [runSearch])

  const onSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    const q = inputValue
    setQuery(q)
    freeTextRef.current = q
    const sc = useWorkingSetStore.getState().scope
    if (deskMode === 'ask') {
      // Ask streams grounded answer; also run search so ranked sources stay visible (RD-004).
      setAskQuestion(q)
      setAskScope(stripFreeText(sc))
      setAskRunId((n) => n + 1)
      void runSearch({ query: q, mode: 'hybrid', scope: sc })
      return
    }
    void runSearch({
      query: q,
      mode,
      scope: sc,
    })
  }

  const onSelectAskSource = useCallback(
    (sourceId: string, sourceType: string) => {
      if (sourceType === 'attachment') {
        setSelection({ kind: 'attachment', sid: sourceId })
      } else {
        setSelection({ kind: 'message', sid: sourceId })
      }
    },
    [setSelection],
  )

  const reRunWithScope = (nextScope: QueryScope) => {
    setScope(nextScope)
    const q = freeTextRef.current
    void runSearch({ query: q, mode, scope: nextScope })
  }

  const onEditChip = (chip: ConstraintChip, newValue: string) => {
    const base = responseScope ?? scope
    const next = applyChipEdit(stripFreeText(base), chip, newValue)
    reRunWithScope(next)
  }

  const onRemoveChip = (chip: ConstraintChip) => {
    const base = responseScope ?? scope
    const next = removeChipFromScope(stripFreeText(base), chip)
    reRunWithScope(next)
  }

  const onRemoveUnsupported = (token: string) => {
    setUnsupported((prev) => prev.filter((t) => t !== token))
    // Re-run without the unsupported token (already absent from residual free text).
    void runSearch({
      query: freeTextRef.current,
      mode,
      scope: useWorkingSetStore.getState().scope,
    })
  }

  const onModeChange = (next: SearchMode) => {
    setMode(next)
    if (hasSearched || inputValue.trim() || !isScopePristine(scope)) {
      void runSearch({
        query: freeTextRef.current || inputValue,
        mode: next,
        scope: useWorkingSetStore.getState().scope,
      })
    }
  }

  const onFacetMailbox = (value: string) => {
    addMailbox(value)
    const next = {
      ...useWorkingSetStore.getState().scope,
      mailboxes: [...(useWorkingSetStore.getState().scope.mailboxes ?? []), value].filter(
        (v, i, a) => a.indexOf(v) === i,
      ),
    }
    void runSearch({ query: freeTextRef.current, mode, scope: next })
  }

  const onFacetYear = (year: number | string) => {
    const y = String(year)
    setScopeDate({ from: `${y}-01-01`, to: `${y}-12-31` })
    const next = {
      ...useWorkingSetStore.getState().scope,
      date: { from: `${y}-01-01`, to: `${y}-12-31` },
    }
    void runSearch({ query: freeTextRef.current, mode, scope: next })
  }

  const onFacetAttachment = (value: boolean) => {
    setHasAttachment(value)
    const next = { ...useWorkingSetStore.getState().scope, has_attachment: value }
    void runSearch({ query: freeTextRef.current, mode, scope: next })
  }

  const selectResult = useCallback(
    (r: SearchResult) => {
      if (r.result_type === 'message') {
        setSelection({ kind: 'message', sid: r.id })
      } else {
        setSelection({ kind: 'attachment', sid: r.id })
      }
    },
    [setSelection],
  )

  const flatResults = results
  const groups = useMemo(
    () => groupResults(flatResults, grouping),
    [flatResults, grouping],
  )

  // J/K/Enter keyboard navigation over loaded list (DOM order).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return
      }
      if (flatResults.length === 0) return

      const selectedId =
        selection?.kind === 'message' || selection?.kind === 'attachment'
          ? selection.sid
          : null
      const idx = selectedId
        ? flatResults.findIndex((r) => r.id === selectedId)
        : -1

      if (e.key === 'j' || e.key === 'J') {
        e.preventDefault()
        const next = Math.min(flatResults.length - 1, Math.max(0, idx + 1))
        selectResult(flatResults[next]!)
      } else if (e.key === 'k' || e.key === 'K') {
        e.preventDefault()
        const next = Math.max(0, idx <= 0 ? 0 : idx - 1)
        selectResult(flatResults[next]!)
      } else if (e.key === 'Enter' && selectedId) {
        e.preventDefault()
        navigate(`/source/${encodeURIComponent(selectedId)}`)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [flatResults, navigate, selectResult, selection])

  const onViewInChronicle = () => {
    const sc = useWorkingSetStore.getState().scope
    if (sc.date?.from || sc.date?.to) {
      const fromStr = sc.date.from ?? sc.date.to!
      const toStr = sc.date.to ?? sc.date.from!
      const fromMs = Date.parse(
        fromStr.length === 10 ? `${fromStr}T00:00:00Z` : fromStr,
      )
      let toMs = Date.parse(toStr.length === 10 ? `${toStr}T23:59:59Z` : toStr)
      if (Number.isFinite(fromMs) && Number.isFinite(toMs)) {
        if (toMs <= fromMs) toMs = fromMs + 24 * 60 * 60 * 1000
        setViewport({ fromMs, toMs })
      }
    }
    navigate('/')
  }

  const chips = chipsFromSearchScope(responseScope ?? scope)
  const freeText = freeTextRef.current
  const emptyHint = !hasSearched && isScopePristine(scope) && !inputValue.trim()
  const showEmptyScopeHint =
    hasSearched && results.length === 0 && !loading && !error

  return (
    <div className="flex h-full min-h-0 gap-3" data-testid="research-desk">
      {/* Left configuration rail */}
      <aside
        className="shrink-0 space-y-4 overflow-y-auto border-r border-steel pr-3"
        style={{ width: 240 }}
        aria-label="Research configuration"
        data-testid="research-config-rail"
      >
        <div>
          <h2 className="mb-2 text-sm font-medium text-text-primary">Mode</h2>
          <div
            role="radiogroup"
            aria-label="Desk mode"
            className="mb-3 flex gap-1"
            data-testid="desk-mode-row"
          >
            {(['search', 'ask'] as const).map((m) => (
              <button
                key={m}
                type="button"
                className={[
                  btnClass,
                  deskMode === m ? 'border-action text-action' : '',
                ].join(' ')}
                aria-pressed={deskMode === m}
                onClick={() => setDeskMode(m)}
                data-testid={`desk-mode-${m}`}
              >
                {m === 'search' ? 'Search' : 'Ask'}
              </button>
            ))}
          </div>
          {deskMode === 'search' ? (
            <div role="radiogroup" aria-label="Retrieval mode" className="space-y-2">
              {MODES.map((m) => (
                <label
                  key={m.value}
                  className="flex cursor-pointer gap-2 rounded-md border border-steel bg-graphite-900 p-2"
                  data-testid={`mode-${m.value}`}
                >
                  <input
                    type="radio"
                    name="search-mode"
                    value={m.value}
                    checked={mode === m.value}
                    onChange={() => onModeChange(m.value)}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="block text-sm text-text-primary">{m.label}</span>
                    <span className="block text-[11px] text-text-muted">{m.description}</span>
                  </span>
                </label>
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-text-muted" data-testid="ask-mode-hint">
              Answer from the current working set with citations. Search remains available if the
              model is offline.
            </p>
          )}
        </div>

        <div>
          <h2 className="mb-2 text-sm font-medium text-text-primary">Group by</h2>
          <div className="flex flex-wrap gap-1" data-testid="grouping-controls">
            {GROUPINGS.map((g) => (
              <button
                key={g.value}
                type="button"
                className={[
                  btnClass,
                  grouping === g.value ? 'border-action text-action' : '',
                ].join(' ')}
                aria-pressed={grouping === g.value}
                onClick={() => setGrouping(g.value)}
                data-testid={`grp-${g.value}`}
              >
                {g.label}
              </button>
            ))}
          </div>
        </div>

        {facets ? (
          <div className="space-y-3" data-testid="facet-lists">
            {facets.mailbox && facets.mailbox.length > 0 ? (
              <div>
                <h3 className="mb-1 text-[11px] font-medium uppercase text-text-muted">
                  Mailbox
                </h3>
                <ul className="space-y-0.5">
                  {facets.mailbox.map((f) => (
                    <li key={String(f.value)}>
                      <button
                        type="button"
                        className="flex w-full items-center justify-between rounded px-1 py-0.5 text-left text-[12px] text-text-primary hover:bg-graphite-800"
                        onClick={() => onFacetMailbox(String(f.value))}
                        data-testid={`facet-mailbox-${f.value}`}
                      >
                        <span className="truncate">{String(f.value)}</span>
                        <span className="tabular-nums text-text-muted">{f.count}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {facets.year && facets.year.length > 0 ? (
              <div>
                <h3 className="mb-1 text-[11px] font-medium uppercase text-text-muted">
                  Year
                </h3>
                <ul className="space-y-0.5">
                  {facets.year.map((f) => (
                    <li key={String(f.value)}>
                      <button
                        type="button"
                        className="flex w-full items-center justify-between rounded px-1 py-0.5 text-left text-[12px] text-text-primary hover:bg-graphite-800"
                        onClick={() => onFacetYear(f.value as number | string)}
                        data-testid={`facet-year-${f.value}`}
                      >
                        <span>{String(f.value)}</span>
                        <span className="tabular-nums text-text-muted">{f.count}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {facets.has_attachment && facets.has_attachment.length > 0 ? (
              <div>
                <h3 className="mb-1 text-[11px] font-medium uppercase text-text-muted">
                  Has attachment
                </h3>
                <ul className="space-y-0.5">
                  {facets.has_attachment.map((f) => (
                    <li key={String(f.value)}>
                      <button
                        type="button"
                        className="flex w-full items-center justify-between rounded px-1 py-0.5 text-left text-[12px] text-text-primary hover:bg-graphite-800"
                        onClick={() => onFacetAttachment(Boolean(f.value))}
                        data-testid={`facet-has-attachment-${f.value}`}
                      >
                        <span>{f.value ? 'Yes' : 'No'}</span>
                        <span className="tabular-nums text-text-muted">{f.count}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </aside>

      {/* Center: query + chips + results */}
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <h1 className="text-base font-medium text-text-primary">Research Desk</h1>
          <button
            type="button"
            className={btnClass}
            onClick={onViewInChronicle}
            data-testid="view-in-chronicle"
          >
            View in Chronicle
          </button>
        </div>

        <form onSubmit={onSubmit} className="flex gap-2" data-testid="query-row">
          <input
            type="search"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="Search archive — from:alice filetype:pdf …"
            aria-label="Research query"
            className="min-w-0 flex-1 rounded-md border border-steel bg-graphite-800 px-3 py-1.5 text-text-primary placeholder:text-text-muted"
            data-testid="research-query-input"
          />
          <span className="self-center text-[11px] text-text-muted" data-testid="mode-badge">
            {deskMode === 'ask' ? 'ask' : mode}
          </span>
          <button type="submit" className={btnClass} data-testid="research-submit">
            {deskMode === 'ask' ? 'Ask' : 'Search'}
          </button>
        </form>

        <ConstraintChips
          chips={chips}
          unsupported={unsupported}
          onEdit={onEditChip}
          onRemove={onRemoveChip}
          onRemoveUnsupported={onRemoveUnsupported}
        />

        {deskMode === 'ask' && askRunId > 0 ? (
          <AnswerBlock
            question={askQuestion}
            scope={askScope}
            runId={askRunId}
            onSelectSource={onSelectAskSource}
          />
        ) : null}

        {degraded?.semantic ? (
          <div
            role="status"
            className="rounded-md border border-steel bg-graphite-900 px-3 py-2 text-sm"
            data-testid="degraded-banner"
          >
            <span className="text-conflict">degraded:</span>{' '}
            <span className="text-text-primary">
              Semantic ranking unavailable — showing exact matches
            </span>
          </div>
        ) : null}

        {error ? (
          <div
            role="alert"
            className="rounded-md border border-conflict bg-graphite-900 p-3 text-conflict"
            data-testid="research-error"
          >
            <p className="mb-2">{error}</p>
            <button
              type="button"
              className={btnClass}
              onClick={() =>
                void runSearch({
                  query: freeTextRef.current || inputValue,
                  mode,
                  scope: useWorkingSetStore.getState().scope,
                })
              }
            >
              Retry
            </button>
          </div>
        ) : null}

        {emptyHint ? (
          <p className="text-text-muted" data-testid="research-hint">
            Enter a query or add scope filters to search. Facets appear after a search.
          </p>
        ) : null}

        {/* Results header */}
        {hasSearched ? (
          <div
            className="flex flex-wrap items-center gap-2 text-[11px] text-text-muted"
            data-testid="results-header"
          >
            <span className="tabular-nums">
              {results.length} result{results.length === 1 ? '' : 's'} loaded
            </span>
            {duplicatesSuppressed > 0 ? (
              <span data-testid="duplicates-note">
                {duplicatesSuppressed} duplicate
                {duplicatesSuppressed === 1 ? '' : 's'} suppressed
                <span className="sr-only">show duplicates</span>
              </span>
            ) : null}
            {grouping !== 'none' && nextCursor != null ? (
              <span data-testid="grouped-window-note">
                Grouped view is over loaded results
              </span>
            ) : null}
          </div>
        ) : null}

        <div
          className={[
            'min-h-0 flex-1 space-y-2 overflow-y-auto',
            loading && results.length > 0 ? 'opacity-50' : '',
          ].join(' ')}
          data-testid="results-list"
          aria-busy={loading}
        >
          {loading && results.length === 0 ? (
            <div className="space-y-2" data-testid="results-skeleton" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-20 animate-pulse rounded-md border border-steel bg-graphite-800"
                />
              ))}
            </div>
          ) : null}

          {groups.map((g) => (
            <div key={g.key} data-testid={`result-group-${g.key}`}>
              {grouping !== 'none' && g.label ? (
                <h3
                  className="sticky top-0 z-10 bg-graphite-950 py-1 text-[11px] font-medium uppercase text-text-muted"
                  data-testid="group-header"
                >
                  {g.label}{' '}
                  <span className="tabular-nums">({g.items.length})</span>
                </h3>
              ) : null}
              <div className="space-y-2">
                {g.items.map((r) => {
                  const selected =
                    (selection?.kind === 'message' || selection?.kind === 'attachment') &&
                    selection.sid === r.id
                  return (
                    <ResultCard
                      key={r.id}
                      result={r}
                      freeText={freeText}
                      selected={selected}
                      onSelect={() => selectResult(r)}
                    />
                  )
                })}
              </div>
            </div>
          ))}

          {showEmptyScopeHint ? (
            <p className="text-text-muted" data-testid="no-results-hint">
              No results. Adjust the query or scope filters. Facets above refine the
              working set.
            </p>
          ) : null}
        </div>

        {nextCursor ? (
          <button
            type="button"
            className={btnClass}
            disabled={loadingMore}
            onClick={() =>
              void runSearch({
                query: freeTextRef.current,
                mode,
                scope: useWorkingSetStore.getState().scope,
                cursor: nextCursor,
                append: true,
              })
            }
            data-testid="load-more"
          >
            {loadingMore ? 'Loading…' : 'Load more'}
          </button>
        ) : null}
      </div>
    </div>
  )
}
