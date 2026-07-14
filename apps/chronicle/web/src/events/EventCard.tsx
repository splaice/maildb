import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../api/client'
import type { EventType } from '../api/types'
import { useWorkingSetStore } from '../workingset/store'
import { getEvent, patchEvent } from './api'
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

export interface EventCardProps {
  eventId: string
  onClose?: () => void
}

/**
 * Inspector event card: origin badge, time+precision, type, status, evidence,
 * summary, claims, Confirm/Dismiss/Edit, and reconstruction placeholder.
 */
export function EventCard({ eventId, onClose }: EventCardProps) {
  const setSelection = useWorkingSetStore((s) => s.setSelection)
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [conflictBanner, setConflictBanner] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editSummary, setEditSummary] = useState('')
  const [editType, setEditType] = useState<EventType>('communication')
  const [editPrecision, setEditPrecision] = useState<string>('day')
  const [editTimeStart, setEditTimeStart] = useState('')

  const query = useQuery({
    queryKey: ['events', eventId],
    queryFn: ({ signal }) => getEvent(eventId, signal),
    retry: false,
  })

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['events', eventId] })
    void qc.invalidateQueries({ queryKey: ['chronicle', 'buckets'] })
  }

  const statusMutation = useMutation({
    mutationFn: (status: string) => {
      const ver = query.data?.current_version
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
        void query.refetch()
      }
    },
  })

  const editMutation = useMutation({
    mutationFn: () => {
      const ver = query.data?.current_version
      if (ver == null) throw new Error('no version')
      return patchEvent(eventId, {
        current_version: ver,
        title: editTitle.trim(),
        summary: editSummary,
        event_type: editType,
        time_precision: editPrecision,
        time_start: editTimeStart || undefined,
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
        void query.refetch()
      }
    },
  })

  if (query.isLoading) {
    return (
      <div className="space-y-2" data-testid="event-card-skeleton" aria-busy="true">
        <div className="h-4 w-3/4 animate-pulse rounded bg-graphite-800" />
        <div className="h-3 w-full animate-pulse rounded bg-graphite-800" />
      </div>
    )
  }

  if (query.isError || !query.data) {
    return (
      <div role="alert" className="text-conflict" data-testid="event-card-error">
        <p className="mb-2">Failed to load event</p>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
        >
          Retry
        </button>
      </div>
    )
  }

  const evt = query.data
  const summary = evt.summary ?? evt.version?.summary ?? null
  const claims = evt.claims ?? []

  const startEdit = () => {
    setEditTitle(evt.title)
    setEditSummary(summary ?? '')
    setEditType((evt.event_type as EventType) || 'communication')
    setEditPrecision(evt.time_precision || 'day')
    setEditTimeStart(evt.time_start?.slice(0, 16) ?? '')
    setEditing(true)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="event-card">
      <div>
        <h3 className="text-sm font-medium text-text-primary">{evt.title}</h3>
        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
          <span
            className="rounded border border-steel bg-graphite-800 px-1.5 py-0.5 text-text-primary"
            data-testid="event-origin-badge"
            title="Event origin"
          >
            Origin: {originLabel(String(evt.origin))}
          </span>
          <span
            className="rounded border border-steel bg-graphite-800 px-1.5 py-0.5 text-text-muted"
            data-testid="event-status-badge"
          >
            Status: {evt.status}
          </span>
          {evt.evidence_strength ? (
            <span
              className="rounded border border-steel bg-graphite-800 px-1.5 py-0.5 text-text-muted"
              data-testid="event-evidence-strength"
            >
              Evidence: {evt.evidence_strength}
            </span>
          ) : null}
        </p>
        <p className="mt-1 text-[11px] text-text-muted" data-testid="event-time">
          {formatEventTime(evt.time_start, String(evt.time_precision), evt.time_end)}
        </p>
        <p className="text-[11px] text-text-muted" data-testid="event-type">
          Type: {evt.event_type}
        </p>
      </div>

      {conflictBanner ? (
        <div
          role="alert"
          className="rounded border border-conflict bg-graphite-900 p-2 text-conflict"
          data-testid="event-conflict-banner"
        >
          {conflictBanner}
        </div>
      ) : null}

      {editing ? (
        <form
          className="flex flex-col gap-1.5 rounded border border-steel bg-graphite-900 p-2"
          data-testid="event-edit-form"
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
              data-testid="event-edit-title"
            />
          </label>
          <label className="text-[11px] text-text-muted">
            Summary
            <textarea
              value={editSummary}
              onChange={(e) => setEditSummary(e.target.value)}
              rows={3}
              className="mt-0.5 w-full rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
              data-testid="event-edit-summary"
            />
          </label>
          <label className="text-[11px] text-text-muted">
            Type
            <select
              value={editType}
              onChange={(e) => setEditType(e.target.value as EventType)}
              className="mt-0.5 w-full rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
              data-testid="event-edit-type"
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
              className="mt-0.5 w-full rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
              data-testid="event-edit-precision"
            >
              {PRECISIONS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[11px] text-text-muted">
            Time start (ISO)
            <input
              type="text"
              value={editTimeStart}
              onChange={(e) => setEditTimeStart(e.target.value)}
              className="mt-0.5 w-full rounded border border-steel bg-graphite-800 px-2 py-1 font-mono text-sm text-text-primary"
              data-testid="event-edit-time"
            />
          </label>
          <div className="flex gap-1.5">
            <button
              type="submit"
              disabled={editMutation.isPending}
              className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
              data-testid="event-edit-save"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-muted"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          {summary ? (
            <p className="text-sm text-text-primary" data-testid="event-summary">
              {summary}
            </p>
          ) : null}

          {claims.length > 0 ? (
            <div data-testid="event-claims">
              <p className="mb-1 text-[11px] font-medium text-text-muted">Claims</p>
              <ul className="flex flex-col gap-1.5">
                {claims.map((c) => (
                  <li
                    key={c.id}
                    className="rounded border border-steel/60 bg-graphite-900 px-2 py-1.5"
                    data-testid="event-claim"
                  >
                    <p className="text-sm text-text-primary">{c.text}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-text-muted">
                      <span className="rounded bg-graphite-800 px-1 py-0.5">
                        {c.status}
                      </span>
                      <span>
                        {c.citations.length} citation
                        {c.citations.length === 1 ? '' : 's'}
                      </span>
                    </p>
                    {c.citations.length > 0 ? (
                      <ul className="mt-1 space-y-0.5">
                        {c.citations.map((cit) => (
                          <li key={cit.source_id}>
                            <button
                              type="button"
                              className="text-left text-[11px] text-action underline-offset-2 hover:underline"
                              data-testid="event-citation"
                              onClick={() => {
                                const sid = cit.source_id
                                if (sid.startsWith('att_')) {
                                  setSelection({ kind: 'attachment', sid })
                                } else if (sid.startsWith('msg_')) {
                                  setSelection({ kind: 'message', sid })
                                }
                              }}
                            >
                              {cit.subject || cit.source_id}
                              {cit.sender ? ` · ${cit.sender}` : ''}
                              {cit.date ? ` · ${cit.date.slice(0, 10)}` : ''}
                            </button>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      )}

      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="event-confirm"
          disabled={statusMutation.isPending || evt.status === 'confirmed'}
          onClick={() => statusMutation.mutate('confirmed')}
        >
          Confirm
        </button>
        <button
          type="button"
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="event-dismiss"
          disabled={statusMutation.isPending || evt.status === 'dismissed'}
          onClick={() => statusMutation.mutate('dismissed')}
        >
          Dismiss
        </button>
        <button
          type="button"
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="event-edit"
          onClick={startEdit}
        >
          Edit
        </button>
        <button
          type="button"
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-muted disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="event-reconstruction"
          disabled
          title="Reconstruction arrives with the next task"
        >
          Open reconstruction
        </button>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
            data-testid="event-close"
          >
            Close
          </button>
        ) : null}
      </div>
    </div>
  )
}
