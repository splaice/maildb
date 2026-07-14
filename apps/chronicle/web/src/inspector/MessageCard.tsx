import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { apiGet } from '../api/client'
import type { MessageSource, SourceResponse } from '../api/types'
import { collapsePlainQuotedText, wrapBlockquotesInDetails } from './quotedText'

export interface MessageCardProps {
  sid: string
  onClose: () => void
}

function isMessage(src: SourceResponse): src is MessageSource {
  return src.kind === 'msg'
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return iso
  // Exact timestamp with timezone text (ISO keeps offset / Z)
  return iso
}

function recipientsSummary(recipients: unknown): string {
  if (!recipients || typeof recipients !== 'object') return '—'
  const r = recipients as Record<string, unknown>
  const parts: string[] = []
  for (const key of ['to', 'cc', 'bcc'] as const) {
    const v = r[key]
    if (Array.isArray(v) && v.length > 0) {
      parts.push(`${key}: ${v.map(String).join(', ')}`)
    }
  }
  return parts.length > 0 ? parts.join(' · ') : '—'
}

export function MessageCard({ sid, onClose }: MessageCardProps) {
  const query = useQuery({
    queryKey: ['sources', sid],
    queryFn: ({ signal }) => apiGet<SourceResponse>(`/api/sources/${sid}`, signal),
    retry: false,
  })

  if (query.isLoading) {
    return (
      <div className="space-y-2" data-testid="message-card-skeleton" aria-busy="true">
        <div className="h-4 w-3/4 animate-pulse rounded bg-graphite-800" />
        <div className="h-3 w-full animate-pulse rounded bg-graphite-800" />
        <div className="h-24 animate-pulse rounded bg-graphite-800" />
      </div>
    )
  }

  if (query.isError || !query.data) {
    return (
      <div role="alert" className="text-conflict">
        <p className="mb-2">Failed to load message</p>
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

  if (!isMessage(query.data)) {
    return <p className="text-text-muted">Not a message source</p>
  }

  const { envelope, body } = query.data
  const remote = body.remote_resources_blocked ?? 0

  let bodyPreview: ReactNode
  if (body.html) {
    const wrapped = wrapBlockquotesInDetails(body.html)
    bodyPreview = (
      <div
        className="prose-invert max-h-[1200px] overflow-auto text-text-primary [&_a]:text-action"
        // Server-sanitized HTML only
        dangerouslySetInnerHTML={{ __html: wrapped }}
        data-testid="message-body-html"
      />
    )
  } else if (body.text) {
    bodyPreview = (
      <div
        className="max-h-[1200px] overflow-auto font-mono text-text-primary"
        data-testid="message-body-text"
      >
        {collapsePlainQuotedText(body.text)}
      </div>
    )
  } else {
    bodyPreview = <p className="text-text-muted">No body</p>
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="message-card">
      <div>
        <h3 className="text-sm font-medium text-text-primary">
          {envelope.subject || '(no subject)'}
        </h3>
        <p className="mt-1 text-[11px] text-text-muted">
          {envelope.sender_name || envelope.sender_address || '—'}
          {envelope.sender_name && envelope.sender_address
            ? ` <${envelope.sender_address}>`
            : ''}
        </p>
        <p className="tabular-nums font-mono text-[11px] text-text-muted">
          {formatTimestamp(envelope.date)}
        </p>
        <p className="text-[11px] text-text-muted">
          {recipientsSummary(envelope.recipients)}
        </p>
        <p className="text-[11px] text-text-muted">
          Mailbox: {envelope.mailbox || '—'}
        </p>
        <p className="font-mono text-[10px] text-text-muted">{envelope.id}</p>
      </div>

      <div className="text-[11px] text-text-muted">
        Source status: sanitized
        {body.had_active_content ? ' · active content removed' : ''}
      </div>

      {remote > 0 ? (
        <p className="text-[11px] text-event" data-testid="remote-blocked">
          {remote} remote resource{remote === 1 ? '' : 's'} blocked
        </p>
      ) : null}

      <div className="min-h-0 flex-1 overflow-hidden rounded border border-steel bg-graphite-950 p-2">
        {bodyPreview}
      </div>

      <div className="flex flex-wrap gap-1.5">
        <Link
          to={`/source/${encodeURIComponent(sid)}`}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="open-full-source"
        >
          Open full source
        </Link>
        {envelope.thread_id ? (
          <Link
            to={`/source/${encodeURIComponent(sid)}?thread=1`}
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
            data-testid="open-thread"
          >
            Open thread
          </Link>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="message-close"
        >
          Close
        </button>
      </div>
    </div>
  )
}
