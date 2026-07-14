/**
 * Event reconstruction: claim-to-evidence matrix, evidence rail, version history,
 * status flows, chronological evidence chain (wireframe 03 / task 3.3).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router'

import { ApiError } from '../api/client'
import type {
  ClaimStatus,
  EventCitation,
  EventClaim,
  EventType,
  EventVersionDetail,
} from '../api/types'
import { useWorkingSetStore } from '../workingset/store'
import {
  adoptEventVersion,
  getEvent,
  getEventVersions,
  getSourceContext,
  patchEvent,
} from './api'
import { claimStatusText, claimStatusVisual } from './claimStatus'
import { formatDerivationLine } from './derivation'
import { formatEventTime, originLabel } from './format'

const EVENT_TYPES: EventType[] = [
  'decision',
  'meeting',
  'travel',
  'purchase',
  'deadline',
  'transition',
  'document',
  'communication',
  'user_defined',
]

const PRECISIONS = ['year', 'quarter', 'month', 'week', 'day', 'hour'] as const
const CLAIM_STATUSES: ClaimStatus[] = [
  'direct',
  'supported',
  'conflicting',
  'unresolved',
]

interface EditClaimDraft {
  text: string
  status: ClaimStatus
  citations: string[]
}

function citationKey(cit: EventCitation, index: number): string {
  return `${cit.source_id}:${index}`
}

function envelopeLine(cit: EventCitation): string {
  const bits = [
    cit.subject || cit.source_id,
    cit.sender ? `from ${cit.sender}` : null,
    cit.date ? cit.date.slice(0, 10) : null,
  ].filter(Boolean)
  return bits.join(' · ')
}

function locationOffsets(
  location: Record<string, unknown> | null | undefined,
): { start: number; end: number } | null {
  if (!location || typeof location !== 'object') return null
  const start = location.char_start
  const end = location.char_end
  if (typeof start !== 'number' || typeof end !== 'number') return null
  if (start < 0 || end < start) return null
  return { start, end }
}

/** Evidence rail citation row with lazy freshness check on expand. */
function CitationRow({
  cit,
  index,
  dimmed,
  highlighted,
  onOpenSource,
  onOpenFull,
}: {
  cit: EventCitation
  index: number
  dimmed: boolean
  highlighted: boolean
  onOpenSource: (sid: string) => void
  onOpenFull: (sid: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [stale, setStale] = useState<boolean | null>(null)

  useEffect(() => {
    if (!expanded) return
    const hash = cit.excerpt_hash
    const loc = locationOffsets(cit.location ?? null)
    if (!hash || !loc) {
      setStale(null)
      return
    }
    let cancelled = false
    void getSourceContext(cit.source_id, loc.start, loc.end)
      .then((ctx) => {
        if (cancelled) return
        // Stored hashes may be bare sha256 or "sha256:…" prefixed.
        const stored = hash.startsWith('sha256:') ? hash.slice(7) : hash
        const live = ctx.sha256.startsWith('sha256:')
          ? ctx.sha256.slice(7)
          : ctx.sha256
        setStale(stored !== live)
      })
      .catch(() => {
        if (!cancelled) setStale(null)
      })
    return () => {
      cancelled = true
    }
  }, [expanded, cit.excerpt_hash, cit.location, cit.source_id])

  return (
    <li
      className={`rounded border px-2 py-1.5 transition-opacity ${
        highlighted
          ? 'border-action bg-graphite-800'
          : 'border-steel/60 bg-graphite-900'
      } ${dimmed ? 'opacity-40' : 'opacity-100'}`}
      data-testid="recon-citation"
      data-source-id={cit.source_id}
      data-highlighted={highlighted ? 'true' : 'false'}
      data-dimmed={dimmed ? 'true' : 'false'}
    >
      <button
        type="button"
        className="w-full text-left text-[11px] text-text-primary"
        data-testid="recon-citation-toggle"
        onClick={() => setExpanded((v) => !v)}
      >
        {envelopeLine(cit)}
      </button>
      {expanded ? (
        <div className="mt-1 space-y-1" data-testid="recon-citation-body">
          {cit.excerpt ? (
            <blockquote
              className="border-l-2 border-steel pl-2 text-[11px] text-text-muted italic"
              data-testid="recon-citation-excerpt"
            >
              {cit.excerpt}
            </blockquote>
          ) : (
            <p className="text-[11px] text-text-muted">No excerpt stored</p>
          )}
          {stale === true ? (
            <span
              className="inline-block rounded bg-conflict/20 px-1 py-0.5 text-[11px] text-conflict"
              data-testid="recon-citation-stale"
            >
              stale?
            </span>
          ) : null}
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              className="rounded border border-steel bg-graphite-800 px-1.5 py-0.5 text-[11px] text-action"
              data-testid="recon-citation-open"
              onClick={() => onOpenSource(cit.source_id)}
            >
              Open source
            </button>
            <button
              type="button"
              className="rounded border border-steel bg-graphite-800 px-1.5 py-0.5 text-[11px] text-action"
              data-testid="recon-citation-open-full"
              onClick={() => onOpenFull(cit.source_id)}
            >
              Open full source
            </button>
          </div>
        </div>
      ) : null}
      <span className="sr-only">{index}</span>
    </li>
  )
}

function ClaimStatusChip({ status }: { status: string }) {
  const v = claimStatusVisual(status)
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded bg-graphite-800 px-1.5 py-0.5 text-[11px] font-medium ${v.className}`}
      data-testid="claim-status-chip"
      data-status={status}
      title={claimStatusText(status)}
    >
      <span aria-hidden="true">{v.symbol}</span>
      <span>{v.label}</span>
    </span>
  )
}

export function ReconstructionView() {
  const { id: rawId } = useParams<{ id: string }>()
  const eventId = rawId ? decodeURIComponent(rawId) : ''
  const navigate = useNavigate()
  const location = useLocation()
  const setSelection = useWorkingSetStore((s) => s.setSelection)
  const qc = useQueryClient()

  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null)
  const [showVersions, setShowVersions] = useState(false)
  const [selectedVersionNum, setSelectedVersionNum] = useState<number | null>(
    null,
  )
  const [adoptedBanner, setAdoptedBanner] = useState<string | null>(null)
  const [conflictBanner, setConflictBanner] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editSummary, setEditSummary] = useState('')
  const [editType, setEditType] = useState<EventType>('communication')
  const [editPrecision, setEditPrecision] = useState<string>('day')
  const [editTimeStart, setEditTimeStart] = useState('')
  const [editClaims, setEditClaims] = useState<EditClaimDraft[]>([])

  const eventQuery = useQuery({
    queryKey: ['events', eventId],
    queryFn: ({ signal }) => getEvent(eventId, signal),
    enabled: !!eventId,
    retry: false,
  })

  const versionsQuery = useQuery({
    queryKey: ['events', eventId, 'versions'],
    queryFn: ({ signal }) => getEventVersions(eventId, signal),
    enabled: !!eventId && showVersions,
    retry: false,
  })

  const invalidate = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['events', eventId] })
    void qc.invalidateQueries({ queryKey: ['events', eventId, 'versions'] })
    void qc.invalidateQueries({ queryKey: ['chronicle', 'buckets'] })
    void qc.invalidateQueries({ queryKey: ['events', 'list'] })
  }, [qc, eventId])

  const statusMutation = useMutation({
    mutationFn: (status: string) => {
      const ver = eventQuery.data?.current_version
      if (ver == null) throw new Error('no version')
      return patchEvent(eventId, { current_version: ver, status })
    },
    onSuccess: () => {
      setConflictBanner(null)
      invalidate()
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 409) {
        setConflictBanner(
          'This event was updated elsewhere. Refresh and try again.',
        )
        void eventQuery.refetch()
      }
    },
  })

  const editMutation = useMutation({
    mutationFn: () => {
      const ver = eventQuery.data?.current_version
      if (ver == null) throw new Error('no version')
      return patchEvent(eventId, {
        current_version: ver,
        title: editTitle.trim(),
        summary: editSummary,
        event_type: editType,
        time_precision: editPrecision,
        time_start: editTimeStart || undefined,
        claims: editClaims.map((c) => ({
          text: c.text.trim(),
          status: c.status,
          citations: c.citations,
        })),
      })
    },
    onSuccess: () => {
      setConflictBanner(null)
      setEditing(false)
      invalidate()
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 409) {
        setConflictBanner(
          'This event was updated elsewhere. Refresh and try again.',
        )
        void eventQuery.refetch()
      }
    },
  })

  const titlePatchMutation = useMutation({
    mutationFn: (title: string) => {
      const ver = eventQuery.data?.current_version
      if (ver == null) throw new Error('no version')
      return patchEvent(eventId, { current_version: ver, title })
    },
    onSuccess: () => {
      setConflictBanner(null)
      invalidate()
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 409) {
        setConflictBanner(
          'This event was updated elsewhere. Refresh and try again.',
        )
        void eventQuery.refetch()
      }
    },
  })

  const adoptMutation = useMutation({
    mutationFn: (version: number) => {
      const ver = eventQuery.data?.current_version
      if (ver == null) throw new Error('no version')
      return adoptEventVersion(eventId, version, { current_version: ver })
    },
    onSuccess: (data) => {
      setConflictBanner(null)
      setAdoptedBanner(
        `Adopted version ${data.current_version}. Prior versions remain immutable.`,
      )
      invalidate()
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 409) {
        setConflictBanner(
          'This event was updated elsewhere. Refresh and try again.',
        )
        void eventQuery.refetch()
      }
    },
  })

  const evt = eventQuery.data
  const claims = evt?.claims ?? []
  const summary = evt?.summary ?? evt?.version?.summary ?? null
  const derivation = evt?.derivation ?? evt?.version?.derivation ?? {}

  const selectedClaim: EventClaim | null = useMemo(() => {
    if (!selectedClaimId) return null
    return claims.find((c) => c.id === selectedClaimId) ?? null
  }, [claims, selectedClaimId])

  const selectedSourceIds = useMemo(() => {
    if (!selectedClaim) return null
    return new Set(selectedClaim.citations.map((c) => c.source_id))
  }, [selectedClaim])

  /** All citations for current version, ordered; used by evidence rail. */
  const allCitations = useMemo(() => {
    const out: EventCitation[] = []
    const seen = new Set<string>()
    for (const claim of claims) {
      for (const cit of claim.citations) {
        const k = cit.source_id
        if (seen.has(k)) continue
        seen.add(k)
        out.push(cit)
      }
    }
    return out
  }, [claims])

  /** Chronological chain: citations by source date. */
  const chronoChain = useMemo(() => {
    const items = [...allCitations]
    items.sort((a, b) => {
      const da = a.date ? Date.parse(a.date) : 0
      const db = b.date ? Date.parse(b.date) : 0
      return da - db
    })
    return items
  }, [allCitations])

  /** For conflicting claim: split supporting vs conflicting chains. */
  const dualChains = useMemo(() => {
    if (!selectedClaim || selectedClaim.status !== 'conflicting') return null
    const supporting: EventCitation[] = []
    const conflicting: EventCitation[] = []
    // Other claims that share sources or are non-conflicting feed "Supporting"
    const selectedIds = new Set(
      selectedClaim.citations.map((c) => c.source_id),
    )
    for (const claim of claims) {
      for (const cit of claim.citations) {
        if (claim.id === selectedClaim.id) {
          conflicting.push(cit)
        } else if (
          claim.status === 'direct' ||
          claim.status === 'supported' ||
          selectedIds.has(cit.source_id)
        ) {
          supporting.push(cit)
        }
      }
    }
    // Always show the claim's own citations under Conflicting; peer sources under Supporting
    return { supporting, conflicting: selectedClaim.citations }
  }, [claims, selectedClaim])

  const goBack = () => {
    // URL contract: reconstruction carries chronicle search params; restore them.
    void navigate({ pathname: '/', search: location.search })
  }

  const openSource = (sid: string) => {
    if (sid.startsWith('att_')) {
      setSelection({ kind: 'attachment', sid })
    } else if (sid.startsWith('msg_')) {
      setSelection({ kind: 'message', sid })
    }
  }

  const openFullSource = (sid: string) => {
    void navigate(`/source/${encodeURIComponent(sid)}${location.search}`)
  }

  const startEdit = () => {
    if (!evt) return
    setEditTitle(evt.title)
    setEditSummary(summary ?? '')
    setEditType((evt.event_type as EventType) || 'communication')
    setEditPrecision(evt.time_precision || 'day')
    setEditTimeStart(evt.time_start?.slice(0, 16) ?? '')
    setEditClaims(
      claims.map((c) => ({
        text: c.text,
        status: (c.status as ClaimStatus) || 'direct',
        citations: c.citations.map((x) => x.source_id),
      })),
    )
    setEditing(true)
  }

  const cancelEdit = () => {
    setEditing(false)
  }

  if (!eventId) {
    return (
      <div role="alert" className="text-conflict">
        Missing event id
      </div>
    )
  }

  if (eventQuery.isLoading) {
    return (
      <div
        className="space-y-2"
        data-testid="reconstruction-skeleton"
        aria-busy="true"
      >
        <div className="h-6 w-1/2 animate-pulse rounded bg-graphite-800" />
        <div className="h-40 animate-pulse rounded bg-graphite-800" />
      </div>
    )
  }

  if (eventQuery.isError || !evt) {
    return (
      <div role="alert" className="text-conflict" data-testid="reconstruction-error">
        <p className="mb-2">Failed to load event</p>
        <button
          type="button"
          onClick={() => void eventQuery.refetch()}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1"
        >
          Retry
        </button>
        <button
          type="button"
          onClick={goBack}
          className="ml-2 rounded-md border border-steel bg-graphite-800 px-2 py-1"
        >
          Back
        </button>
      </div>
    )
  }

  const derivationLine =
    evt.origin === 'automatic' ? formatDerivationLine(derivation) : null

  const versions: EventVersionDetail[] = versionsQuery.data?.versions ?? []
  const selectedVersion =
    selectedVersionNum != null
      ? (versions.find((v) => v.version === selectedVersionNum) ?? null)
      : null
  const currentVersionDetail =
    versions.find((v) => v.version === evt.current_version) ?? null

  return (
    <div
      className="flex min-h-0 flex-col gap-3"
      data-testid="reconstruction-view"
    >
      {/* Header */}
      <header className="space-y-2 rounded-lg border border-steel bg-graphite-900 p-3">
        <div className="flex flex-wrap items-start gap-2">
          <button
            type="button"
            onClick={goBack}
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
            data-testid="reconstruction-back"
          >
            ← Back
          </button>
          {editing ? null : (
            <h1
              className="min-w-0 flex-1 text-base font-medium text-text-primary"
              data-testid="reconstruction-title"
            >
              <span
                contentEditable
                suppressContentEditableWarning
                className="rounded border border-transparent px-1 hover:border-steel focus:border-action focus:outline-none"
                data-testid="reconstruction-title-edit"
                onBlur={(e) => {
                  const next = e.currentTarget.textContent?.trim() ?? ''
                  if (next && next !== evt.title) {
                    titlePatchMutation.mutate(next)
                  } else {
                    e.currentTarget.textContent = evt.title
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    e.currentTarget.blur()
                  }
                }}
              >
                {evt.title}
              </span>
            </h1>
          )}
        </div>

        <p className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span
            className="rounded border border-steel bg-graphite-800 px-1.5 py-0.5"
            data-testid="recon-origin-badge"
          >
            Origin: {originLabel(String(evt.origin))}
          </span>
          <span
            className="rounded border border-steel bg-graphite-800 px-1.5 py-0.5 text-text-muted"
            data-testid="recon-status-badge"
          >
            Status: {evt.status}
          </span>
          <span className="text-text-muted" data-testid="recon-time">
            {formatEventTime(
              evt.time_start,
              String(evt.time_precision),
              evt.time_end,
            )}
          </span>
          <span className="text-text-muted" data-testid="recon-type">
            Type: {evt.event_type}
          </span>
          {evt.evidence_strength ? (
            <span
              className="rounded border border-steel bg-graphite-800 px-1.5 py-0.5 text-text-muted"
              data-testid="recon-evidence-strength"
            >
              Evidence: {evt.evidence_strength}
            </span>
          ) : null}
        </p>

        {derivationLine ? (
          <p
            className="text-[11px] text-text-muted"
            data-testid="recon-derivation"
          >
            {derivationLine}
          </p>
        ) : null}

        {evt.has_suggestions ? (
          <div
            className="rounded border border-event bg-graphite-800 px-2 py-1.5 text-sm text-event"
            data-testid="recon-suggestion-banner"
            role="status"
          >
            An updated automatic version exists —{' '}
            <button
              type="button"
              className="underline"
              onClick={() => {
                setShowVersions(true)
              }}
            >
              Review
            </button>
          </div>
        ) : null}

        {adoptedBanner ? (
          <div
            className="rounded border border-attachment bg-graphite-800 px-2 py-1.5 text-sm text-attachment"
            data-testid="recon-adopted-banner"
            role="status"
          >
            {adoptedBanner}
          </div>
        ) : null}

        {conflictBanner ? (
          <div
            role="alert"
            className="rounded border border-conflict bg-graphite-800 p-2 text-conflict"
            data-testid="recon-conflict-banner"
          >
            {conflictBanner}
          </div>
        ) : null}

        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
            data-testid="recon-confirm"
            disabled={
              statusMutation.isPending || evt.status === 'confirmed'
            }
            onClick={() => statusMutation.mutate('confirmed')}
          >
            Confirm
          </button>
          <button
            type="button"
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
            data-testid="recon-edit"
            onClick={startEdit}
          >
            Edit
          </button>
          {evt.status === 'dismissed' ? (
            <button
              type="button"
              className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
              data-testid="recon-restore"
              disabled={statusMutation.isPending}
              onClick={() => statusMutation.mutate('unreviewed')}
            >
              Restore
            </button>
          ) : (
            <button
              type="button"
              className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
              data-testid="recon-dismiss"
              disabled={statusMutation.isPending}
              onClick={() => statusMutation.mutate('dismissed')}
            >
              Dismiss
            </button>
          )}
          <button
            type="button"
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
            data-testid="recon-version-toggle"
            aria-pressed={showVersions}
            onClick={() => setShowVersions((v) => !v)}
          >
            Version history
          </button>
        </div>
      </header>

      {editing ? (
        <form
          className="flex flex-col gap-2 rounded-lg border border-steel bg-graphite-900 p-3"
          data-testid="recon-edit-form"
          onSubmit={(e) => {
            e.preventDefault()
            editMutation.mutate()
          }}
        >
          <label className="text-[11px] text-text-muted">
            Title
            <input
              type="text"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              className="mt-0.5 w-full rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
              data-testid="recon-edit-title"
            />
          </label>
          <label className="text-[11px] text-text-muted">
            Summary
            <textarea
              value={editSummary}
              onChange={(e) => setEditSummary(e.target.value)}
              rows={3}
              className="mt-0.5 w-full rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
              data-testid="recon-edit-summary"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <label className="text-[11px] text-text-muted">
              Type
              <select
                value={editType}
                onChange={(e) => setEditType(e.target.value as EventType)}
                className="mt-0.5 block rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
                data-testid="recon-edit-type"
              >
                {EVENT_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-[11px] text-text-muted">
              Precision
              <select
                value={editPrecision}
                onChange={(e) => setEditPrecision(e.target.value)}
                className="mt-0.5 block rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
              >
                {PRECISIONS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-[11px] text-text-muted">
              Time start
              <input
                type="text"
                value={editTimeStart}
                onChange={(e) => setEditTimeStart(e.target.value)}
                className="mt-0.5 block rounded border border-steel bg-graphite-800 px-2 py-1 font-mono text-sm text-text-primary"
              />
            </label>
          </div>
          <div data-testid="recon-edit-claims">
            <p className="mb-1 text-[11px] font-medium text-text-muted">
              Claims
            </p>
            <ul className="flex flex-col gap-2">
              {editClaims.map((c, i) => (
                <li
                  key={i}
                  className="rounded border border-steel/60 p-2"
                  data-testid="recon-edit-claim"
                >
                  <input
                    type="text"
                    value={c.text}
                    onChange={(e) => {
                      const next = [...editClaims]
                      next[i] = { ...c, text: e.target.value }
                      setEditClaims(next)
                    }}
                    className="mb-1 w-full rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
                    data-testid="recon-edit-claim-text"
                  />
                  <select
                    value={c.status}
                    onChange={(e) => {
                      const next = [...editClaims]
                      next[i] = {
                        ...c,
                        status: e.target.value as ClaimStatus,
                      }
                      setEditClaims(next)
                    }}
                    className="rounded border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary"
                    data-testid="recon-edit-claim-status"
                  >
                    {CLAIM_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="ml-2 text-[11px] text-conflict"
                    onClick={() =>
                      setEditClaims(editClaims.filter((_, j) => j !== i))
                    }
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="mt-1 rounded border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary"
              data-testid="recon-edit-claim-add"
              onClick={() =>
                setEditClaims([
                  ...editClaims,
                  { text: '', status: 'direct', citations: [] },
                ])
              }
            >
              Add claim
            </button>
          </div>
          <div className="flex gap-1.5">
            <button
              type="submit"
              disabled={editMutation.isPending}
              className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
              data-testid="recon-edit-save"
            >
              Save
            </button>
            <button
              type="button"
              onClick={cancelEdit}
              className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-muted"
              data-testid="recon-edit-cancel"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      {/* Matrix + evidence rail */}
      <div className="flex min-h-0 flex-1 flex-col gap-2 lg:flex-row">
        <section
          className="min-w-0 flex-1 rounded-lg border border-steel bg-graphite-900 p-3"
          data-testid="claim-matrix"
          aria-label="Claim to evidence matrix"
        >
          <h2 className="mb-2 text-sm font-medium text-text-primary">
            Claims
          </h2>
          {claims.length === 0 ? (
            <p className="text-sm text-text-muted">No claims</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {claims.map((claim) => {
                const selected = selectedClaimId === claim.id
                return (
                  <li key={claim.id}>
                    <button
                      type="button"
                      className={`flex w-full flex-col gap-1 rounded border px-2 py-2 text-left ${
                        selected
                          ? 'border-action bg-graphite-800'
                          : 'border-steel/60 bg-graphite-950 hover:border-steel'
                      }`}
                      data-testid="claim-matrix-row"
                      data-claim-id={claim.id}
                      data-selected={selected ? 'true' : 'false'}
                      onClick={() =>
                        setSelectedClaimId((id) =>
                          id === claim.id ? null : claim.id,
                        )
                      }
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <ClaimStatusChip status={String(claim.status)} />
                        <span className="text-[11px] text-text-muted">
                          {claim.citations.length} citation
                          {claim.citations.length === 1 ? '' : 's'}
                        </span>
                      </div>
                      <p className="text-sm text-text-primary">{claim.text}</p>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          {summary ? (
            <p
              className="mt-3 text-sm text-text-muted"
              data-testid="recon-summary"
            >
              {summary}
            </p>
          ) : null}
        </section>

        {/* Evidence rail — inspector width (~360px) */}
        <aside
          className="w-full shrink-0 rounded-lg border border-steel bg-graphite-900 p-3 lg:w-[360px]"
          data-testid="evidence-rail"
          aria-label="Evidence"
        >
          <h2 className="mb-2 text-sm font-medium text-text-primary">
            Evidence
          </h2>
          {dualChains ? (
            <div className="space-y-3" data-testid="conflict-chains">
              <div>
                <h3
                  className="mb-1 text-[11px] font-medium text-attachment"
                  data-testid="supporting-heading"
                >
                  Supporting
                </h3>
                <ul className="flex flex-col gap-1.5">
                  {dualChains.supporting.length === 0 ? (
                    <li className="text-[11px] text-text-muted">
                      No supporting citations
                    </li>
                  ) : (
                    dualChains.supporting.map((cit, i) => (
                      <CitationRow
                        key={citationKey(cit, i)}
                        cit={cit}
                        index={i}
                        dimmed={false}
                        highlighted
                        onOpenSource={openSource}
                        onOpenFull={openFullSource}
                      />
                    ))
                  )}
                </ul>
              </div>
              <div>
                <h3
                  className="mb-1 text-[11px] font-medium text-conflict"
                  data-testid="conflicting-heading"
                >
                  Conflicting
                </h3>
                <ul className="flex flex-col gap-1.5">
                  {dualChains.conflicting.map((cit, i) => (
                    <CitationRow
                      key={citationKey(cit, i)}
                      cit={cit}
                      index={i}
                      dimmed={false}
                      highlighted
                      onOpenSource={openSource}
                      onOpenFull={openFullSource}
                    />
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {allCitations.length === 0 ? (
                <li className="text-[11px] text-text-muted">No citations</li>
              ) : (
                allCitations.map((cit, i) => {
                  const inSelection =
                    selectedSourceIds == null ||
                    selectedSourceIds.has(cit.source_id)
                  const dimmed =
                    selectedSourceIds != null && !selectedSourceIds.has(cit.source_id)
                  return (
                    <CitationRow
                      key={citationKey(cit, i)}
                      cit={cit}
                      index={i}
                      dimmed={dimmed}
                      highlighted={inSelection && selectedSourceIds != null}
                      onOpenSource={openSource}
                      onOpenFull={openFullSource}
                    />
                  )
                })
              )}
            </ul>
          )}
        </aside>
      </div>

      {/* Version history panel */}
      {showVersions ? (
        <section
          className="rounded-lg border border-steel bg-graphite-900 p-3"
          data-testid="version-history-panel"
          aria-label="Version history"
        >
          <h2 className="mb-2 text-sm font-medium text-text-primary">
            Version history
          </h2>
          <p className="mb-2 text-[11px] text-text-muted">
            Prior versions are read-only (immutable). Suggestions are never
            auto-applied.
          </p>
          {versionsQuery.isLoading ? (
            <p className="text-sm text-text-muted">Loading versions…</p>
          ) : (
            <div className="flex flex-col gap-3 lg:flex-row">
              <ul
                className="flex max-h-48 flex-col gap-1 overflow-auto lg:w-56"
                data-testid="version-list"
              >
                {versions.map((v) => (
                  <li key={v.version}>
                    <button
                      type="button"
                      className={`w-full rounded border px-2 py-1.5 text-left text-[11px] ${
                        selectedVersionNum === v.version
                          ? 'border-action bg-graphite-800'
                          : 'border-steel/60 bg-graphite-950'
                      }`}
                      data-testid="version-list-item"
                      data-version={v.version}
                      data-suggestion={v.is_suggestion ? 'true' : 'false'}
                      onClick={() => setSelectedVersionNum(v.version)}
                    >
                      <span className="font-medium text-text-primary">
                        v{v.version} · {v.author}
                      </span>
                      {v.is_suggestion ? (
                        <span className="ml-1 text-event">suggestion</span>
                      ) : null}
                      {v.version === evt.current_version ? (
                        <span className="ml-1 text-attachment">current</span>
                      ) : null}
                      <span className="block text-text-muted">
                        {v.created_at?.slice(0, 19) ?? ''} · {v.title}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              {selectedVersion ? (
                <div
                  className="min-w-0 flex-1 space-y-2"
                  data-testid="version-diff"
                >
                  <div className="grid grid-cols-2 gap-2 text-[11px]">
                    <div
                      className="rounded border border-steel/60 p-2"
                      data-testid="version-diff-selected"
                    >
                      <p className="mb-1 font-medium text-text-muted">
                        Selected v{selectedVersion.version}
                        {selectedVersion.version !== evt.current_version
                          ? ' (read-only)'
                          : ''}
                      </p>
                      <p className="text-text-primary">
                        <span className="text-text-muted">Title: </span>
                        {selectedVersion.title}
                      </p>
                      <p className="mt-1 text-text-primary">
                        <span className="text-text-muted">Summary: </span>
                        {selectedVersion.summary ?? '—'}
                      </p>
                    </div>
                    <div
                      className="rounded border border-steel/60 p-2"
                      data-testid="version-diff-current"
                    >
                      <p className="mb-1 font-medium text-text-muted">
                        Current v{evt.current_version}
                      </p>
                      <p className="text-text-primary">
                        <span className="text-text-muted">Title: </span>
                        {currentVersionDetail?.title ?? evt.title}
                      </p>
                      <p className="mt-1 text-text-primary">
                        <span className="text-text-muted">Summary: </span>
                        {currentVersionDetail?.summary ?? summary ?? '—'}
                      </p>
                    </div>
                  </div>
                  {selectedVersion.is_suggestion ? (
                    <button
                      type="button"
                      className="rounded-md border border-event bg-graphite-800 px-2 py-1 text-sm text-event"
                      data-testid="version-adopt"
                      disabled={adoptMutation.isPending}
                      onClick={() =>
                        adoptMutation.mutate(selectedVersion.version)
                      }
                    >
                      Adopt this version
                    </button>
                  ) : null}
                </div>
              ) : (
                <p className="text-sm text-text-muted">
                  Select a version to compare
                </p>
              )}
            </div>
          )}
        </section>
      ) : null}

      {/* Chronological evidence chain (bottom strip) */}
      <section
        className="rounded-lg border border-steel bg-graphite-900 p-2"
        data-testid="chrono-evidence-chain"
        aria-label="Chronological evidence chain"
      >
        <h2 className="mb-1 text-[11px] font-medium text-text-muted">
          Evidence chain
        </h2>
        <ul className="flex gap-2 overflow-x-auto pb-1">
          {chronoChain.length === 0 ? (
            <li className="text-[11px] text-text-muted">No sources</li>
          ) : (
            chronoChain.map((cit, i) => (
              <li key={citationKey(cit, i)}>
                <button
                  type="button"
                  className="whitespace-nowrap rounded border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary hover:border-action"
                  data-testid="chrono-evidence-item"
                  onClick={() => openSource(cit.source_id)}
                >
                  {(cit.date ? cit.date.slice(0, 10) : '—') +
                    (cit.sender ? ` · ${cit.sender}` : '')}
                </button>
              </li>
            ))
          )}
        </ul>
      </section>
    </div>
  )
}
