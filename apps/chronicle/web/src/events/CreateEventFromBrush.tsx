import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import type { EventType } from '../api/types'
import { ApiError } from '../api/client'
import type { Viewport } from '../chronicle/timeScale'
import { useWorkingSetStore } from '../workingset/store'
import { toIsoSeconds } from '../workingset/urlState'
import { createEvent } from './api'

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

export interface CreateEventFromBrushProps {
  brush: Viewport
  onCreated?: () => void
}

/**
 * Toolbar/brush affordance: when a brush exists, "Create event from selection"
 * opens a form (title, type, precision) → POST with time_start/end from brush.
 */
export function CreateEventFromBrush({ brush, onCreated }: CreateEventFromBrushProps) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [eventType, setEventType] = useState<EventType>('communication')
  const [precision, setPrecision] = useState<(typeof PRECISIONS)[number]>('day')
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()

  const mutation = useMutation({
    mutationFn: () =>
      createEvent({
        title: title.trim(),
        time_start: toIsoSeconds(brush.fromMs),
        time_end: toIsoSeconds(brush.toMs),
        time_precision: precision,
        event_type: eventType,
      }),
    onSuccess: (evt) => {
      setError(null)
      setOpen(false)
      setTitle('')
      void qc.invalidateQueries({ queryKey: ['chronicle', 'buckets'] })
      void qc.invalidateQueries({ queryKey: ['events'] })
      onCreated?.()
      useWorkingSetStore.getState().setSelection({ kind: 'event', eventId: evt.id })
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError('Failed to create event')
      }
    },
  })

  if (!open) {
    return (
      <div className="flex items-center gap-2" data-testid="create-event-affordance">
        <button
          type="button"
          className="rounded-md border border-event bg-graphite-800 px-2 py-1 text-text-primary hover:border-event"
          style={{ borderColor: '#E0A84A' }}
          onClick={() => setOpen(true)}
          data-testid="create-event-from-selection"
        >
          Create event from selection
        </button>
      </div>
    )
  }

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-lg border border-steel bg-graphite-900 p-2"
      data-testid="create-event-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (!title.trim()) {
          setError('Title is required')
          return
        }
        mutation.mutate()
      }}
    >
      <label className="flex flex-col gap-0.5 text-[11px] text-text-muted">
        Title
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
          data-testid="create-event-title"
          required
        />
      </label>
      <label className="flex flex-col gap-0.5 text-[11px] text-text-muted">
        Type
        <select
          value={eventType}
          onChange={(e) => setEventType(e.target.value as EventType)}
          className="rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
          data-testid="create-event-type"
        >
          {EVENT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-0.5 text-[11px] text-text-muted">
        Precision
        <select
          value={precision}
          onChange={(e) =>
            setPrecision(e.target.value as (typeof PRECISIONS)[number])
          }
          className="rounded border border-steel bg-graphite-800 px-2 py-1 text-sm text-text-primary"
          data-testid="create-event-precision"
        >
          {PRECISIONS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        disabled={mutation.isPending}
        className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
        data-testid="create-event-submit"
      >
        {mutation.isPending ? 'Creating…' : 'Create'}
      </button>
      <button
        type="button"
        className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-muted"
        onClick={() => {
          setOpen(false)
          setError(null)
        }}
      >
        Cancel
      </button>
      {error ? (
        <p role="alert" className="w-full text-conflict" data-testid="create-event-error">
          {error}
        </p>
      ) : null}
    </form>
  )
}
