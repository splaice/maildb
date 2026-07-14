import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import type { ContactCard, ContactKind, ContactSummary } from '../api/types'
import { encodeState, toIsoSeconds } from '../workingset/urlState'
import { useWorkingSetStore } from '../workingset/store'
import {
  getPerson,
  listPeople,
  mergePeople,
  patchPerson,
  unmergePeople,
} from './api'
import { ActivityBars } from './ActivityBars'
import { EgoGraph } from './EgoGraph'

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[12px] text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

const KINDS: ContactKind[] = [
  'human',
  'organization',
  'automated',
  'mailing_list',
  'unknown',
]

/**
 * Person / organization profile (spec Table 23 sections).
 * Org domain controls covered by tags/notes MVP.
 */
export function PersonProfilePage() {
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const setScope = useWorkingSetStore((s) => s.setScope)
  const setMode = useWorkingSetStore((s) => s.setMode)
  const setQuery = useWorkingSetStore((s) => s.setQuery)
  const setGrouping = useWorkingSetStore((s) => s.setGrouping)

  const [nameDraft, setNameDraft] = useState('')
  const [notesDraft, setNotesDraft] = useState('')
  const [tagDraft, setTagDraft] = useState('')
  const [mergeSearch, setMergeSearch] = useState('')
  const [mergeTarget, setMergeTarget] = useState<ContactSummary | null>(null)

  const query = useQuery({
    queryKey: ['people', id],
    queryFn: ({ signal }) => getPerson(id, signal),
    enabled: !!id,
    retry: false,
  })

  const card = query.data

  useEffect(() => {
    if (card) {
      setNameDraft(card.display_name ?? '')
      setNotesDraft(card.notes ?? '')
    }
  }, [card])

  const patchMut = useMutation({
    mutationFn: (body: {
      kind?: ContactKind
      tags?: string[]
      notes?: string | null
      display_name?: string | null
    }) => patchPerson(id, body),
    onSuccess: (data) => {
      void qc.setQueryData(['people', id], data)
      void qc.invalidateQueries({ queryKey: ['people', 'list'] })
    },
  })

  const mergeMut = useMutation({
    mutationFn: (body: { source_id: string; target_id: string }) =>
      mergePeople(body),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ['people'] })
      void navigate(`/people/${data.id}`)
    },
  })

  const unmergeMut = useMutation({
    mutationFn: (mergeId: string) => unmergePeople({ merge_id: mergeId }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['people'] })
      void query.refetch()
    },
  })

  const mergeSearchQuery = useQuery({
    queryKey: ['people', 'merge-search', mergeSearch],
    queryFn: ({ signal }) =>
      listPeople({ q: mergeSearch, limit: 8 }, signal),
    enabled: mergeSearch.trim().length >= 2,
    retry: false,
  })

  const signalRows = useMemo(() => {
    const signals = card?.classification_signals
    if (!signals || typeof signals !== 'object') return []
    return Object.entries(signals).map(([signal, weight]) => ({
      signal,
      weight: typeof weight === 'number' ? weight : Number(weight),
    }))
  }, [card?.classification_signals])

  const primaryAddress = useMemo(() => {
    if (!card) return null
    const details = card.address_details ?? []
    if (details.length > 0) {
      const sorted = [...details].sort(
        (a, b) =>
          b.messages_from + b.messages_to - (a.messages_from + a.messages_to),
      )
      return sorted[0]?.address ?? card.addresses[0] ?? null
    }
    return card.addresses[0] ?? null
  }, [card])

  const openInChronicle = useCallback(
    (c: ContactCard) => {
      const addr = primaryAddress
      if (!addr) return
      let fromMs: number
      let toMs: number
      if (c.first_seen && c.last_seen) {
        fromMs = Date.parse(c.first_seen)
        toMs = Date.parse(c.last_seen)
        if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) {
          toMs = fromMs + 24 * 3600 * 1000
        }
      } else if (c.activity?.length) {
        const times = c.activity
          .map((a) => Date.parse(a.bucket))
          .filter((t) => Number.isFinite(t))
        fromMs = Math.min(...times)
        toMs = Math.max(...times) + 30 * 24 * 3600 * 1000
      } else {
        const now = Date.now()
        fromMs = now - 365 * 24 * 3600 * 1000
        toMs = now
      }
      const params = encodeState({
        scope: { senders: [addr] },
        viewport: { fromMs, toMs },
        aggregation: 'auto',
        view: 'canvas',
        selection: null,
        lanes: ['messages', 'top_people'],
      })
      if (!params.get('vf')) params.set('vf', toIsoSeconds(fromMs))
      if (!params.get('vt')) params.set('vt', toIsoSeconds(toMs))
      const qs = params.toString()
      void navigate({ pathname: '/', search: qs ? `?${qs}` : '' })
    },
    [navigate, primaryAddress],
  )

  const openInResearch = useCallback(
    (c: ContactCard) => {
      const addrs = c.addresses ?? []
      if (!addrs.length) return
      setScope({ senders: [...addrs] })
      setQuery('')
      setMode('exact')
      setGrouping('thread')
      void navigate('/research')
    },
    [navigate, setGrouping, setMode, setQuery, setScope],
  )

  const openTopic = useCallback(
    (topicId: string) => {
      void navigate(`/topics?tsel=${encodeURIComponent(topicId)}`)
    },
    [navigate],
  )

  const saveName = useCallback(() => {
    if (!card) return
    const next = nameDraft.trim()
    if (next === (card.display_name ?? '')) return
    patchMut.mutate({ display_name: next || null })
  }, [card, nameDraft, patchMut])

  const saveNotes = useCallback(() => {
    if (!card) return
    if (notesDraft === (card.notes ?? '')) return
    patchMut.mutate({ notes: notesDraft })
  }, [card, notesDraft, patchMut])

  const addTag = useCallback(() => {
    if (!card) return
    const t = tagDraft.trim()
    if (!t) return
    const tags = card.tags ?? []
    if (tags.includes(t)) {
      setTagDraft('')
      return
    }
    patchMut.mutate({ tags: [...tags, t] })
    setTagDraft('')
  }, [card, tagDraft, patchMut])

  const removeTag = useCallback(
    (tag: string) => {
      if (!card) return
      patchMut.mutate({ tags: (card.tags ?? []).filter((t) => t !== tag) })
    },
    [card, patchMut],
  )

  const confirmMergeInto = useCallback(() => {
    if (!mergeTarget || !id) return
    const sourceName =
      card?.display_name || card?.addresses[0] || id
    const targetName =
      mergeTarget.display_name || mergeTarget.addresses[0] || mergeTarget.id
    const ok = window.confirm(
      `Merge ${sourceName} into ${targetName}? Addresses move to the target.`,
    )
    if (!ok) return
    mergeMut.mutate({ source_id: id, target_id: mergeTarget.id })
  }, [mergeTarget, id, card, mergeMut])

  if (query.isLoading) {
    return (
      <div className="p-3 text-[12px] text-text-muted" data-testid="person-loading">
        Loading profile…
      </div>
    )
  }

  if (query.isError || !card) {
    return (
      <div role="alert" className="p-3 text-conflict" data-testid="person-error">
        Failed to load contact
        <button
          type="button"
          className={`${btnClass} ml-2`}
          onClick={() => void query.refetch()}
        >
          Retry
        </button>
      </div>
    )
  }

  const kindLocked = card.kind_source === 'manual'

  return (
    <div
      className="flex h-full min-h-0 flex-col gap-4 overflow-auto p-1"
      data-testid="person-profile"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Link
          to="/people"
          className="text-[12px] text-action underline"
          data-testid="person-back"
        >
          ← People
        </Link>
        {card.kind === 'organization' ? (
          <span className="text-[11px] text-text-muted">Organization profile</span>
        ) : null}
      </div>

      {/* Identity */}
      <section data-testid="person-identity" className="space-y-2">
        <h2 className="text-sm font-medium text-text-primary">Identity</h2>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            onBlur={saveName}
            onKeyDown={(e) => {
              if (e.key === 'Enter') saveName()
            }}
            className="min-w-[12rem] rounded-md border border-steel bg-graphite-900 px-2 py-1 text-sm text-text-primary"
            data-testid="person-display-name"
            aria-label="Display name"
          />
          <label className="flex items-center gap-1 text-[12px] text-text-muted">
            Kind
            <select
              value={card.kind}
              onChange={(e) =>
                patchMut.mutate({ kind: e.target.value as ContactKind })
              }
              className="rounded-md border border-steel bg-graphite-900 px-1 py-0.5 text-text-primary"
              data-testid="person-kind-select"
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <span
            className="text-[11px] text-text-muted"
            data-testid="person-kind-source"
            title={
              kindLocked
                ? 'Manual kind is locked against automated reclassification'
                : 'Kind not yet set manually; classifier may suggest but never writes kind'
            }
          >
            kind_source: {card.kind_source ?? '—'}
            {kindLocked ? ' (manual — locked against machinery)' : ''}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1" data-testid="person-tags">
          {(card.tags ?? []).map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 rounded border border-steel px-1.5 py-0.5 text-[11px]"
            >
              {tag}
              <button
                type="button"
                className="text-text-muted hover:text-conflict"
                aria-label={`Remove tag ${tag}`}
                data-testid={`remove-tag-${tag}`}
                onClick={() => removeTag(tag)}
              >
                ×
              </button>
            </span>
          ))}
          <input
            type="text"
            value={tagDraft}
            onChange={(e) => setTagDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') addTag()
            }}
            placeholder="Add tag"
            className="w-24 rounded border border-steel bg-graphite-900 px-1 text-[11px]"
            data-testid="person-tag-input"
          />
          <button type="button" className={btnClass} onClick={addTag}>
            Add
          </button>
        </div>

        <textarea
          value={notesDraft}
          onChange={(e) => setNotesDraft(e.target.value)}
          onBlur={saveNotes}
          rows={3}
          placeholder="Analyst notes"
          className="w-full max-w-xl rounded-md border border-steel bg-graphite-900 px-2 py-1 text-[12px] text-text-primary"
          data-testid="person-notes"
        />

        <ul className="space-y-1" data-testid="person-addresses">
          {(card.address_details?.length
            ? card.address_details
            : (card.addresses ?? []).map((a) => ({
                address: a,
                is_user: card.address_classes?.[a] === 'owner',
                messages_from: 0,
                messages_to: 0,
                first_seen: null,
                last_seen: null,
              }))
          ).map((d) => {
            const cls =
              card.address_classes?.[d.address] ??
              (d.is_user ? 'owner' : 'external')
            return (
              <li
                key={d.address}
                className="flex flex-wrap items-center gap-2 text-[12px]"
                data-testid={`address-${d.address}`}
              >
                <span className="font-mono">{d.address}</span>
                <span
                  className="rounded border border-steel px-1 text-[10px] text-text-muted"
                  data-testid={`address-class-${d.address}`}
                >
                  {cls}
                </span>
                <span className="text-text-muted">
                  from {d.messages_from} / to {d.messages_to}
                </span>
              </li>
            )
          })}
        </ul>

        <details className="text-[12px]" data-testid="person-signals">
          <summary className="cursor-pointer text-text-muted">
            Classification signals
          </summary>
          {signalRows.length === 0 ? (
            <p className="text-text-muted">No signals</p>
          ) : (
            <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
              {signalRows.map((r) => (
                <li key={r.signal}>
                  {r.signal}: {r.weight}
                </li>
              ))}
            </ul>
          )}
        </details>
      </section>

      {/* Archive span */}
      <section data-testid="person-span" className="space-y-1 text-[12px]">
        <h2 className="text-sm font-medium text-text-primary">Archive span</h2>
        <p>
          First seen:{' '}
          <span className="text-text-muted">
            {card.first_seen?.slice(0, 10) ?? '—'}
          </span>
          {' · '}
          Last seen:{' '}
          <span className="text-text-muted">
            {card.last_seen?.slice(0, 10) ?? '—'}
          </span>
        </p>
        <p>
          Messages from: {card.messages_from} · to: {card.messages_to} · threads:{' '}
          {card.thread_count ?? '—'}
        </p>
      </section>

      {/* Activity */}
      <section data-testid="person-activity" className="space-y-2">
        <h2 className="text-sm font-medium text-text-primary">Activity</h2>
        <ActivityBars buckets={card.activity ?? []} />
        <button
          type="button"
          className={btnClass}
          data-testid="open-in-chronicle"
          onClick={() => openInChronicle(card)}
        >
          Open in Chronicle
        </button>
      </section>

      {/* Topics */}
      <section data-testid="person-topics" className="space-y-1">
        <h2 className="text-sm font-medium text-text-primary">Topics</h2>
        {(card.topics ?? []).length === 0 ? (
          <p className="text-[12px] text-text-muted">No topics (empty or not generated)</p>
        ) : (
          <ul className="space-y-1">
            {card.topics.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  className="text-[12px] text-action underline"
                  data-testid={`topic-link-${t.id}`}
                  onClick={() => openTopic(t.id)}
                >
                  {t.label} ({t.count})
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Co-participation ego graph */}
      <EgoGraph contactId={id} card={card} />

      {/* Sources */}
      <section data-testid="person-sources" className="space-y-1">
        <h2 className="text-sm font-medium text-text-primary">Sources</h2>
        <button
          type="button"
          className={btnClass}
          data-testid="view-correspondence"
          onClick={() => openInResearch(card)}
        >
          View correspondence
        </button>
      </section>

      {/* Merge / unmerge */}
      <section data-testid="person-merge" className="space-y-2">
        <h2 className="text-sm font-medium text-text-primary">
          Merge into another contact
        </h2>
        <input
          type="search"
          value={mergeSearch}
          onChange={(e) => {
            setMergeSearch(e.target.value)
            setMergeTarget(null)
          }}
          placeholder="Search target contact…"
          className="w-full max-w-md rounded-md border border-steel bg-graphite-900 px-2 py-1 text-[12px]"
          data-testid="merge-target-search"
        />
        {(mergeSearchQuery.data?.items ?? [])
          .filter((c) => c.id !== id)
          .map((c) => (
            <button
              key={c.id}
              type="button"
              className={`block w-full max-w-md text-left ${btnClass} ${
                mergeTarget?.id === c.id ? 'ring-1 ring-action' : ''
              }`}
              onClick={() => setMergeTarget(c)}
              data-testid={`merge-target-${c.id}`}
            >
              {c.display_name || c.addresses[0]} ({c.kind})
            </button>
          ))}
        <button
          type="button"
          className={btnClass}
          disabled={!mergeTarget || mergeMut.isPending}
          onClick={confirmMergeInto}
          data-testid="merge-confirm"
        >
          Merge into selected
        </button>
      </section>

      {card.merges && card.merges.length > 0 ? (
        <section data-testid="person-merge-history" className="space-y-2">
          <h2 className="text-sm font-medium text-text-primary">Merge history</h2>
          <ul className="space-y-1 text-[12px]">
            {card.merges.map((m) => (
              <li
                key={m.id}
                className="flex flex-wrap items-center gap-2"
                data-testid={`merge-history-${m.id}`}
              >
                <span className="font-mono text-[11px] text-text-muted">
                  {m.id.slice(0, 8)}… from {m.source_id.slice(0, 8)}…
                  {m.merged_at ? ` @ ${m.merged_at.slice(0, 10)}` : ''}
                </span>
                <button
                  type="button"
                  className={btnClass}
                  data-testid={`unmerge-${m.id}`}
                  disabled={unmergeMut.isPending}
                  onClick={() => {
                    const ok = window.confirm(
                      `Unmerge ${m.id}? Restores the absorbed contact entity.`,
                    )
                    if (ok) unmergeMut.mutate(m.id)
                  }}
                >
                  Unmerge
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}
