import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { apiPost, ApiError } from '../api/client'
import {
  isEventGenerateUnavailable,
  type EventGenerateResponse,
  type EventGenerateResult,
  type QueryScope,
} from '../api/types'
import type { Viewport } from '../chronicle/timeScale'
import { toIsoSeconds } from '../workingset/urlState'
import { DismissedEventsList } from './DismissedEventsList'

export interface GeneratePanelProps {
  scope: QueryScope
  viewport: Viewport
}

/** Result framing, e.g. "4 created · 1 suggested update · 2 bursts empty". */
export function formatGenerateResultLine(result: EventGenerateResult): string {
  const parts: string[] = []
  if (result.created > 0) {
    parts.push(`${result.created} created`)
  }
  if (result.superseded > 0) {
    parts.push(`${result.superseded} superseded`)
  }
  if (result.suggested > 0) {
    parts.push(
      `${result.suggested} suggested update${result.suggested === 1 ? '' : 's'}`,
    )
  }
  const outcomes = result.created + result.superseded + result.suggested
  if (result.bursts === 0) {
    parts.push('0 bursts')
  } else if (outcomes < result.bursts) {
    const empty = result.bursts - outcomes
    if (empty > 0) parts.push(`${empty} bursts empty`)
  }
  if (parts.length === 0) {
    return `${result.bursts} bursts · nothing new`
  }
  return parts.join(' · ')
}

/**
 * Config-rail control: generate inferred events for the visible viewport.
 * Spec §13.5 tone: inferred events are hypotheses — review before trusting.
 */
export function GeneratePanel({ scope, viewport }: GeneratePanelProps) {
  const qc = useQueryClient()
  const [resultLine, setResultLine] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      apiPost<EventGenerateResponse>('/api/events/generate', {
        scope,
        viewport: {
          from: toIsoSeconds(viewport.fromMs),
          to: toIsoSeconds(viewport.toMs),
        },
      }),
    onSuccess: (body) => {
      setError(null)
      if (isEventGenerateUnavailable(body)) {
        setUnavailable(true)
        setResultLine(null)
        return
      }
      setUnavailable(false)
      setResultLine(formatGenerateResultLine(body))
      void qc.invalidateQueries({ queryKey: ['chronicle', 'buckets'] })
      void qc.invalidateQueries({ queryKey: ['events'] })
    },
    onError: (err: unknown) => {
      setUnavailable(false)
      setResultLine(null)
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError('Generation failed')
      }
    },
  })

  return (
    <>
      <section
        className="mt-2 space-y-2 border-t border-steel pt-2"
        data-testid="generate-events-panel"
        aria-label="Generate inferred events"
      >
        <p className="text-xs text-text-muted" data-testid="generate-events-framing">
          Inferred events are hypotheses — review before trusting
        </p>
        <button
          type="button"
          className="w-full rounded-md border border-steel bg-graphite-800 px-2 py-1.5 text-xs text-text-primary hover:border-action disabled:opacity-50"
          data-testid="generate-events-button"
          disabled={mutation.isPending}
          onClick={() => {
            setResultLine(null)
            setUnavailable(false)
            setError(null)
            mutation.mutate()
          }}
        >
          {mutation.isPending ? 'Generating…' : 'Generate events for visible range'}
        </button>
        {mutation.isPending ? (
          <p className="text-xs text-text-muted" data-testid="generate-events-progress">
            Detecting bursts and extracting events…
          </p>
        ) : null}
        {unavailable ? (
          <p className="text-xs text-text-muted" data-testid="generate-events-unavailable">
            Model unavailable — Chronicle still works without generation
          </p>
        ) : null}
        {resultLine ? (
          <p className="text-xs text-text-primary" data-testid="generate-events-result">
            {resultLine}
          </p>
        ) : null}
        {error ? (
          <p className="text-xs text-conflict" role="alert" data-testid="generate-events-error">
            {error}
          </p>
        ) : null}
      </section>
      <DismissedEventsList scope={scope} viewport={viewport} />
    </>
  )
}
