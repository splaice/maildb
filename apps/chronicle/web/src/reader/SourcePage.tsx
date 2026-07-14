import { useQuery } from '@tanstack/react-query'
import { useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import { apiGet } from '../api/client'
import type {
  AttachmentMeta,
  MessageSource,
  SourceResponse,
  ThreadResponse,
} from '../api/types'
import {
  collapsePlainQuotedText,
  wrapBlockquotesInDetails,
} from '../inspector/quotedText'

function isMessage(src: SourceResponse): src is MessageSource {
  return src.kind === 'msg'
}

function formatBytes(size: number | null | undefined): string {
  if (size == null || !Number.isFinite(size)) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function recipientsBlock(recipients: unknown): ReactNode {
  if (!recipients || typeof recipients !== 'object') return <span>—</span>
  const r = recipients as Record<string, unknown>
  const rows: { label: string; value: string }[] = []
  for (const key of ['to', 'cc', 'bcc'] as const) {
    const v = r[key]
    if (Array.isArray(v) && v.length > 0) {
      rows.push({ label: key, value: v.map(String).join(', ') })
    }
  }
  if (rows.length === 0) return <span>—</span>
  return (
    <ul className="space-y-0.5">
      {rows.map((row) => (
        <li key={row.label}>
          <span className="text-text-muted">{row.label}: </span>
          {row.value}
        </li>
      ))}
    </ul>
  )
}

function AttachmentCard({ att }: { att: AttachmentMeta }) {
  const [open, setOpen] = useState(false)
  const detail = useQuery({
    queryKey: ['sources', att.id],
    queryFn: ({ signal }) => apiGet<SourceResponse>(`/api/sources/${att.id}`, signal),
    enabled: open,
    retry: false,
  })

  return (
    <div className="rounded border border-steel bg-graphite-900 p-2" data-testid={`att-${att.id}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left"
      >
        <div className="font-medium text-text-primary">{att.filename}</div>
        <div className="text-[11px] text-text-muted">
          {att.content_type || 'unknown type'} · {formatBytes(att.size)}
        </div>
        <div className="font-mono text-[10px] text-text-muted">{att.id}</div>
      </button>
      {open ? (
        <div className="mt-2 border-t border-steel pt-2 text-[11px] text-text-muted">
          {detail.isLoading ? (
            <span>Loading extraction status…</span>
          ) : detail.isError ? (
            <span className="text-conflict">Failed to load attachment</span>
          ) : detail.data && detail.data.kind === 'att' ? (
            <div>
              <div>
                Extraction: {detail.data.extraction_status || 'unknown'}
                {detail.data.extraction_reason
                  ? ` (${detail.data.extraction_reason})`
                  : ''}
              </div>
              <div>
                Extracted text:{' '}
                {detail.data.markdown != null && detail.data.markdown.length > 0
                  ? 'available (preview in Phase 2)'
                  : 'not available'}
              </div>
            </div>
          ) : (
            <span>No extraction metadata</span>
          )}
        </div>
      ) : null}
    </div>
  )
}

function ThreadPanel({
  thrSid,
  currentSid,
}: {
  thrSid: string
  currentSid: string
}) {
  const query = useQuery({
    queryKey: ['threads', thrSid],
    queryFn: ({ signal }) => apiGet<ThreadResponse>(`/api/threads/${thrSid}`, signal),
    retry: false,
  })

  if (query.isLoading) {
    return (
      <div className="animate-pulse space-y-1" aria-busy="true">
        <div className="h-3 w-1/2 rounded bg-graphite-800" />
        <div className="h-8 rounded bg-graphite-800" />
      </div>
    )
  }
  if (query.isError || !query.data) {
    return (
      <div role="alert" className="text-conflict">
        Failed to load thread
        <button
          type="button"
          className="ml-2 underline"
          onClick={() => void query.refetch()}
        >
          Retry
        </button>
      </div>
    )
  }

  const t = query.data
  return (
    <div data-testid="thread-panel" className="space-y-2">
      <div className="text-[11px] text-text-muted">
        {t.message_count} message{t.message_count === 1 ? '' : 's'}
        {t.date_range.from || t.date_range.to
          ? ` · ${t.date_range.from ?? '—'} – ${t.date_range.to ?? '—'}`
          : ''}
      </div>
      <div className="text-[11px] text-text-muted">
        Participants:{' '}
        {t.participants
          .map((p) => p.name || p.address || '?')
          .join(', ') || '—'}
      </div>
      <ul className="space-y-0.5">
        {t.messages.map((m) => {
          const active = m.id === currentSid
          return (
            <li key={m.id}>
              <Link
                to={`/source/${encodeURIComponent(m.id)}?thread=1`}
                className={`block rounded px-2 py-1.5 ${
                  active
                    ? 'border border-action bg-action/10'
                    : 'hover:bg-graphite-800'
                }`}
                aria-current={active ? 'true' : undefined}
              >
                <div className="truncate text-text-primary">
                  {m.subject || '(no subject)'}
                </div>
                <div className="flex gap-2 text-[11px] text-text-muted">
                  <span className="tabular-nums font-mono">
                    {m.date ? m.date.replace('T', ' ').replace(/\.\d{3}.*/, '') : '—'}
                  </span>
                  <span className="truncate">
                    {m.sender_name || m.sender_address || '—'}
                  </span>
                </div>
              </Link>
            </li>
          )
        })}
      </ul>
      {t.truncated ? (
        <p className="text-[11px] text-event">Thread truncated at 500 messages</p>
      ) : null}
    </div>
  )
}

export function SourcePage() {
  const { sid: rawSid } = useParams<{ sid: string }>()
  const sid = rawSid ? decodeURIComponent(rawSid) : ''
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const threadOpenDefault = searchParams.get('thread') === '1'
  const [mode, setMode] = useState<'reading' | 'plain'>('reading')
  const [threadOpen, setThreadOpen] = useState(threadOpenDefault)

  const query = useQuery({
    queryKey: ['sources', sid],
    queryFn: ({ signal }) => apiGet<SourceResponse>(`/api/sources/${sid}`, signal),
    enabled: !!sid,
    retry: false,
  })

  const bodyNodes = useMemo(() => {
    if (!query.data || !isMessage(query.data)) return null
    const { body } = query.data
    if (mode === 'plain') {
      return (
        <pre className="whitespace-pre-wrap font-mono text-text-primary">
          {body.text ?? ''}
        </pre>
      )
    }
    if (body.html) {
      const wrapped = wrapBlockquotesInDetails(body.html)
      return (
        <div
          className="prose-invert text-text-primary [&_a]:text-action"
          dangerouslySetInnerHTML={{ __html: wrapped }}
          data-testid="reader-body-html"
        />
      )
    }
    if (body.text) {
      return (
        <div className="font-mono text-text-primary" data-testid="reader-body-text">
          {collapsePlainQuotedText(body.text)}
        </div>
      )
    }
    return <p className="text-text-muted">No body</p>
  }, [mode, query.data])

  return (
    <div className="mx-auto max-w-3xl space-y-4" data-testid="source-page">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => {
            // Return contract: history.back() restores chronicle viewport + selection.
            if (window.history.length > 1) {
              window.history.back()
            } else {
              void navigate('/')
            }
          }}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="source-back"
        >
          ← Back
        </button>
      </div>

      {query.isLoading ? (
        <div className="space-y-2" aria-busy="true" data-testid="source-skeleton">
          <div className="h-6 w-2/3 animate-pulse rounded bg-graphite-800" />
          <div className="h-4 w-full animate-pulse rounded bg-graphite-800" />
          <div className="h-40 animate-pulse rounded bg-graphite-800" />
        </div>
      ) : null}

      {query.isError ? (
        <div role="alert" className="text-conflict">
          Failed to load source
          <button
            type="button"
            className="ml-2 underline"
            onClick={() => void query.refetch()}
          >
            Retry
          </button>
        </div>
      ) : null}

      {query.data && isMessage(query.data) ? (
        <>
          <header className="space-y-1 border-b border-steel pb-3">
            <h1 className="text-base font-medium text-text-primary">
              {query.data.envelope.subject || '(no subject)'}
            </h1>
            <div className="text-[12px] text-text-muted">
              <div>
                From:{' '}
                {query.data.envelope.sender_name ||
                  query.data.envelope.sender_address ||
                  '—'}
                {query.data.envelope.sender_name && query.data.envelope.sender_address
                  ? ` <${query.data.envelope.sender_address}>`
                  : ''}
              </div>
              <div className="mt-1">{recipientsBlock(query.data.envelope.recipients)}</div>
              <div className="mt-1 tabular-nums font-mono">
                {query.data.envelope.date || '—'}
              </div>
              <div>Mailbox: {query.data.envelope.mailbox || '—'}</div>
              <div className="font-mono text-[11px]">{query.data.envelope.id}</div>
              {query.data.envelope.labels?.length ? (
                <div className="flex flex-wrap gap-1 pt-1">
                  {query.data.envelope.labels.map((lab) => (
                    <span
                      key={lab}
                      className="rounded bg-graphite-800 px-1.5 py-0.5 text-[10px] text-text-muted"
                    >
                      {lab}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </header>

          {query.data.body.remote_resources_blocked > 0 ? (
            <p className="text-[12px] text-event">
              {query.data.body.remote_resources_blocked} remote resource
              {query.data.body.remote_resources_blocked === 1 ? '' : 's'} blocked
            </p>
          ) : null}

          <div
            role="tablist"
            aria-label="Source modes"
            className="flex gap-1 border-b border-steel"
          >
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'reading'}
              onClick={() => setMode('reading')}
              className={`px-3 py-1.5 ${
                mode === 'reading'
                  ? 'border-b-2 border-action text-text-primary'
                  : 'text-text-muted'
              }`}
            >
              Reading
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'plain'}
              onClick={() => setMode('plain')}
              className={`px-3 py-1.5 ${
                mode === 'plain'
                  ? 'border-b-2 border-action text-text-primary'
                  : 'text-text-muted'
              }`}
              data-testid="mode-plain"
            >
              Plain text
            </button>
          </div>

          <article className="min-h-[12rem] rounded-lg border border-steel bg-graphite-900 p-4">
            {bodyNodes}
          </article>

          {query.data.envelope.attachments?.length ? (
            <section aria-label="Attachments" className="space-y-2">
              <h2 className="text-sm font-medium text-text-primary">Attachments</h2>
              <div className="grid gap-2 sm:grid-cols-2">
                {query.data.envelope.attachments.map((att) => (
                  <AttachmentCard key={att.id} att={att} />
                ))}
              </div>
            </section>
          ) : null}

          {query.data.envelope.thread_id ? (
            <section aria-label="Thread" className="rounded-lg border border-steel bg-graphite-900">
              <button
                type="button"
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm text-text-primary"
                onClick={() => setThreadOpen((v) => !v)}
                aria-expanded={threadOpen}
                data-testid="thread-toggle"
              >
                <span>Thread</span>
                <span className="text-text-muted">{threadOpen ? '▾' : '▸'}</span>
              </button>
              {threadOpen ? (
                <div className="border-t border-steel p-3">
                  <ThreadPanel
                    thrSid={query.data.envelope.thread_id}
                    currentSid={sid}
                  />
                </div>
              ) : null}
            </section>
          ) : null}
        </>
      ) : null}

      {query.data && !isMessage(query.data) ? (
        <div className="space-y-2">
          <h1 className="text-base font-medium">{query.data.filename}</h1>
          <p className="font-mono text-[11px] text-text-muted">{query.data.id}</p>
          <p className="text-text-muted">
            {query.data.content_type || 'unknown'} · {formatBytes(query.data.size)}
          </p>
          <p className="text-text-muted">
            Extraction: {query.data.extraction_status || 'unknown'}
          </p>
          {query.data.markdown ? (
            <pre className="whitespace-pre-wrap rounded border border-steel bg-graphite-900 p-3 font-mono">
              {query.data.markdown}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
