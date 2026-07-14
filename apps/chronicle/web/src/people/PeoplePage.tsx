import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'

import type { ContactKind, ContactSummary, MergeCandidatePair } from '../api/types'
import { listMergeCandidates, listPeople, mergePeople } from './api'

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[12px] text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

const KINDS: { id: '' | ContactKind; label: string }[] = [
  { id: '', label: 'All' },
  { id: 'human', label: 'Human' },
  { id: 'organization', label: 'Organizations' },
  { id: 'automated', label: 'Automated' },
  { id: 'mailing_list', label: 'Mailing list' },
  { id: 'unknown', label: 'Unknown' },
]

function volume(c: ContactSummary): number {
  return (c.messages_from ?? 0) + (c.messages_to ?? 0)
}

function formatProb(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return '—'
  return p.toFixed(2)
}

function formatSpan(first: string | null, last: string | null): string {
  const f = first ? first.slice(0, 10) : '—'
  const l = last ? last.slice(0, 10) : '—'
  return `${f} → ${l}`
}

/**
 * People & Organizations index: search, kind chips, needs-review queue,
 * merge-candidates rail. Organizations tab = kind=organization preset.
 */
export function PeoplePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const pq = searchParams.get('pq') ?? ''
  const kind = searchParams.get('kind') ?? ''
  const needsReview = searchParams.get('review') === '1'
  const [draft, setDraft] = useState(pq)

  const setParam = useCallback(
    (key: string, value: string | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (value == null || value === '') next.delete(key)
          else next.set(key, value)
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const commitSearch = useCallback(() => {
    setParam('pq', draft.trim() || null)
  }, [draft, setParam])

  // Kind counts from a first unfiltered page (same q / needs_review, no kind).
  const countsQuery = useQuery({
    queryKey: ['people', 'counts', pq, needsReview],
    queryFn: ({ signal }) =>
      listPeople(
        {
          q: pq || undefined,
          needs_review: needsReview || undefined,
          limit: 200,
        },
        signal,
      ),
    retry: false,
  })

  const listQuery = useQuery({
    queryKey: ['people', 'list', pq, kind, needsReview],
    queryFn: ({ signal }) =>
      listPeople(
        {
          q: pq || undefined,
          kind: kind || undefined,
          needs_review: needsReview || undefined,
          limit: 50,
        },
        signal,
      ),
    retry: false,
  })

  const candidatesQuery = useQuery({
    queryKey: ['people', 'merge-candidates'],
    queryFn: ({ signal }) => listMergeCandidates(20, signal),
    retry: false,
  })

  const kindCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const item of countsQuery.data?.items ?? []) {
      const k = item.kind || 'unknown'
      counts[k] = (counts[k] ?? 0) + 1
    }
    return counts
  }, [countsQuery.data])

  const mergeMut = useMutation({
    mutationFn: (body: { source_id: string; target_id: string }) =>
      mergePeople(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['people'] })
    },
  })

  const onMerge = useCallback(
    (pair: MergeCandidatePair, direction: 'a_into_b' | 'b_into_a') => {
      const source = direction === 'a_into_b' ? pair.a : pair.b
      const target = direction === 'a_into_b' ? pair.b : pair.a
      const sourceName = source.display_name || source.primary_address || source.contact_id
      const targetName = target.display_name || target.primary_address || target.contact_id
      const ok = window.confirm(
        `Merge ${sourceName} into ${targetName}? This moves addresses to the target contact.`,
      )
      if (!ok) return
      mergeMut.mutate({
        source_id: source.contact_id,
        target_id: target.contact_id,
      })
    },
    [mergeMut],
  )

  const items = listQuery.data?.items ?? []

  return (
    <div
      className="flex h-full min-h-0 flex-col gap-3"
      data-testid="people-page"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="text-sm font-medium text-text-primary">
          People & Organizations
        </h1>
        <p className="text-[11px] text-text-muted">
          Identity profiles over the contacts subsystem. Org domain controls
          use tags/notes (MVP).
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitSearch()
          }}
          placeholder="Search name or address…"
          className="min-w-[12rem] flex-1 rounded-md border border-steel bg-graphite-900 px-2 py-1 text-[12px] text-text-primary"
          data-testid="people-search"
          aria-label="Search people"
        />
        <button
          type="button"
          className={btnClass}
          onClick={commitSearch}
          data-testid="people-search-submit"
        >
          Search
        </button>
        <label className="flex items-center gap-1 text-[12px] text-text-muted">
          <input
            type="checkbox"
            checked={needsReview}
            onChange={(e) =>
              setParam('review', e.target.checked ? '1' : null)
            }
            data-testid="people-needs-review"
          />
          Needs review
        </label>
      </div>

      <div
        className="inline-flex flex-wrap gap-1"
        role="tablist"
        aria-label="Contact kind"
        data-testid="people-kind-chips"
      >
        {KINDS.map((k) => {
          const selected = kind === k.id
          const count =
            k.id === ''
              ? countsQuery.data?.items?.length
              : kindCounts[k.id]
          return (
            <button
              key={k.id || 'all'}
              type="button"
              role="tab"
              aria-selected={selected}
              className={`${btnClass} ${selected ? 'ring-1 ring-action' : ''}`}
              onClick={() => setParam('kind', k.id || null)}
              data-testid={`people-kind-${k.id || 'all'}`}
            >
              {k.label}
              {count != null ? (
                <span className="ml-1 text-text-muted">({count})</span>
              ) : null}
            </button>
          )
        })}
      </div>

      <div className="flex min-h-0 flex-1 gap-3">
        <div className="min-w-0 flex-1 overflow-auto" data-testid="people-list">
          {listQuery.isLoading ? (
            <p className="text-[12px] text-text-muted">Loading…</p>
          ) : listQuery.isError ? (
            <p role="alert" className="text-conflict">
              Failed to load people
            </p>
          ) : items.length === 0 ? (
            <p className="text-[12px] text-text-muted">No contacts match.</p>
          ) : (
            <table className="w-full border-collapse text-left text-[12px]">
              <thead className="sticky top-0 bg-graphite-900 text-text-muted">
                <tr className="border-b border-steel">
                  <th className="px-2 py-1.5 font-medium">Name</th>
                  <th className="px-2 py-1.5 font-medium">Kind</th>
                  <th className="px-2 py-1.5 font-medium">Human p</th>
                  <th className="px-2 py-1.5 font-medium">Volume</th>
                  <th className="px-2 py-1.5 font-medium">Span</th>
                </tr>
              </thead>
              <tbody className="tabular-nums text-text-primary">
                {items.map((c) => (
                  <tr
                    key={c.id}
                    className="cursor-pointer border-b border-steel/60 hover:bg-graphite-900"
                    data-testid={`people-row-${c.id}`}
                    onClick={() => void navigate(`/people/${c.id}`)}
                  >
                    <td className="px-2 py-1 font-sans">
                      {c.display_name || c.addresses[0] || c.id}
                    </td>
                    <td className="px-2 py-1">
                      <span
                        className="rounded border border-steel px-1 text-[10px] text-text-muted"
                        data-testid={`kind-badge-${c.id}`}
                      >
                        {c.kind}
                      </span>
                    </td>
                    <td className="px-2 py-1 text-text-muted">
                      {formatProb(c.human_probability)}
                    </td>
                    <td className="px-2 py-1">{volume(c)}</td>
                    <td className="px-2 py-1 text-text-muted">
                      {formatSpan(c.first_seen, c.last_seen)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {listQuery.data?.total != null ? (
            <p className="mt-2 text-[11px] text-text-muted">
              {listQuery.data.total} matching
            </p>
          ) : null}
        </div>

        <aside
          className="w-64 shrink-0 overflow-auto border-l border-steel pl-3"
          data-testid="merge-candidates-rail"
        >
          <h2 className="mb-2 text-[12px] font-medium text-text-primary">
            Merge candidates
          </h2>
          {candidatesQuery.isLoading ? (
            <p className="text-[11px] text-text-muted">Loading…</p>
          ) : (candidatesQuery.data?.items ?? []).length === 0 ? (
            <p className="text-[11px] text-text-muted">No candidates.</p>
          ) : (
            <ul className="space-y-3">
              {(candidatesQuery.data?.items ?? []).map((pair) => (
                <li
                  key={`${pair.a.contact_id}-${pair.b.contact_id}`}
                  className="rounded border border-steel/60 p-2 text-[11px]"
                  data-testid={`merge-pair-${pair.a.contact_id}`}
                >
                  <div className="mb-1 text-text-muted">
                    shared: {pair.norm_name}
                  </div>
                  <div className="flex flex-col gap-1">
                    <Link
                      to={`/people/${pair.a.contact_id}`}
                      className="text-action underline"
                    >
                      {pair.a.display_name || pair.a.primary_address} (
                      {pair.a.msg_count})
                    </Link>
                    <Link
                      to={`/people/${pair.b.contact_id}`}
                      className="text-action underline"
                    >
                      {pair.b.display_name || pair.b.primary_address} (
                      {pair.b.msg_count})
                    </Link>
                  </div>
                  <div className="mt-2 flex flex-col gap-1">
                    <button
                      type="button"
                      className={btnClass}
                      data-testid={`merge-into-b-${pair.a.contact_id}`}
                      onClick={() => onMerge(pair, 'a_into_b')}
                      disabled={mergeMut.isPending}
                    >
                      Merge into →{' '}
                      {pair.b.display_name || pair.b.primary_address}
                    </button>
                    <button
                      type="button"
                      className={btnClass}
                      data-testid={`merge-into-a-${pair.a.contact_id}`}
                      onClick={() => onMerge(pair, 'b_into_a')}
                      disabled={mergeMut.isPending}
                    >
                      Merge into →{' '}
                      {pair.a.display_name || pair.a.primary_address}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  )
}
