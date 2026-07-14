/**
 * Config-rail section: dismissed events for the current viewport, with Restore.
 * Spec §4.5: dismiss retains events in a reviewable dismissed list.
 * Fetches only when expanded so default Chronicle load stays free of list traffic.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import type { QueryScope } from '../api/types'
import type { Viewport } from '../chronicle/timeScale'
import { toIsoSeconds } from '../workingset/urlState'
import { listEvents, patchEvent } from './api'
import { formatEventTime } from './format'

export interface DismissedEventsListProps {
  scope: QueryScope
  viewport: Viewport
}

export function DismissedEventsList({
  scope,
  viewport,
}: DismissedEventsListProps) {
  const qc = useQueryClient()
  const [expanded, setExpanded] = useState(false)

  const query = useQuery({
    queryKey: [
      'events',
      'list',
      'dismissed',
      toIsoSeconds(viewport.fromMs),
      toIsoSeconds(viewport.toMs),
      scope,
    ],
    queryFn: ({ signal }) =>
      listEvents(
        {
          scope,
          viewport: {
            from: toIsoSeconds(viewport.fromMs),
            to: toIsoSeconds(viewport.toMs),
          },
          include_dismissed: true,
          limit: 100,
        },
        signal,
      ),
    enabled: expanded,
    retry: false,
  })

  const dismissed = (query.data?.items ?? []).filter(
    (e) => e.status === 'dismissed',
  )

  const restoreMutation = useMutation({
    mutationFn: (args: { id: string; current_version: number }) =>
      patchEvent(args.id, {
        current_version: args.current_version,
        status: 'unreviewed',
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['events', 'list'] })
      void qc.invalidateQueries({ queryKey: ['chronicle', 'buckets'] })
      void qc.invalidateQueries({ queryKey: ['events'] })
    },
  })

  return (
    <section
      className="mt-2 space-y-2 border-t border-steel pt-2"
      data-testid="dismissed-events-list"
      aria-label="Dismissed events"
    >
      <button
        type="button"
        className="w-full text-left text-xs font-medium text-text-primary hover:text-action"
        data-testid="dismissed-events-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded
          ? `Dismissed events (${dismissed.length})`
          : 'Dismissed events'}
      </button>
      {expanded ? (
        <>
          {query.isLoading ? (
            <p className="text-[11px] text-text-muted">Loading…</p>
          ) : null}
          {query.isError ? (
            <p className="text-[11px] text-conflict" role="alert">
              Failed to load dismissed events
            </p>
          ) : null}
          {dismissed.length === 0 && !query.isLoading ? (
            <p
              className="text-[11px] text-text-muted"
              data-testid="dismissed-empty"
            >
              None in this range
            </p>
          ) : (
            <ul className="flex flex-col gap-1.5" role="list">
              {dismissed.map((evt) => (
                <li
                  key={evt.id}
                  className="rounded border border-steel/60 bg-graphite-950 px-1.5 py-1"
                  data-testid="dismissed-event-row"
                  data-event-id={evt.id}
                >
                  <p
                    className="truncate text-[11px] text-text-primary"
                    title={evt.title}
                  >
                    {evt.title}
                  </p>
                  <p className="text-[10px] text-text-muted">
                    {formatEventTime(
                      evt.time_start,
                      String(evt.time_precision),
                      evt.time_end,
                    )}
                  </p>
                  <button
                    type="button"
                    className="mt-0.5 w-full rounded border border-steel bg-graphite-800 px-1 py-0.5 text-[11px] text-text-primary hover:border-action"
                    data-testid="dismissed-restore"
                    disabled={restoreMutation.isPending}
                    onClick={() =>
                      restoreMutation.mutate({
                        id: evt.id,
                        current_version: evt.current_version,
                      })
                    }
                  >
                    Restore
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </section>
  )
}
