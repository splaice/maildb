import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { apiGet } from '../api/client'
import type { ArchiveHealth, AuditTailRow } from '../api/types'

export const archiveHealthQueryKey = ['health', 'archive'] as const

function useArchiveHealth() {
  return useQuery({
    queryKey: archiveHealthQueryKey,
    queryFn: ({ signal }) => apiGet<ArchiveHealth>('/api/health/archive', signal),
    retry: false,
  })
}

function HealthSkeleton() {
  return (
    <div
      className="animate-pulse space-y-2 rounded-lg border border-steel bg-graphite-900 p-4"
      data-testid="data-health-skeleton"
      aria-busy="true"
      aria-label="Loading data health"
    >
      <div className="h-4 w-40 rounded bg-graphite-800" />
      <div className="h-3 w-full rounded bg-graphite-800" />
      <div className="h-3 w-5/6 rounded bg-graphite-800" />
      <div className="h-3 w-2/3 rounded bg-graphite-800" />
    </div>
  )
}

function FailedCell({ count }: { count: number }) {
  if (count > 0) {
    return <span className="text-conflict">failed: {count.toLocaleString()}</span>
  }
  return <span>{count.toLocaleString()}</span>
}

function Panel({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="rounded-lg border border-steel bg-graphite-900 p-4">
      <h2 className="mb-3 text-sm font-medium text-text-primary">{title}</h2>
      {children}
    </section>
  )
}

function CoverageSection({ data }: { data: ArchiveHealth['coverage'] }) {
  return (
    <Panel title="Coverage">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">Archive coverage counts</caption>
        <tbody className="tabular-nums font-mono text-text-primary">
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Messages
            </th>
            <td className="py-1.5">{data.messages.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Threads
            </th>
            <td className="py-1.5">{data.threads.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Attachments
            </th>
            <td className="py-1.5">{data.attachments.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Contacts
            </th>
            <td className="py-1.5">{data.contacts.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Date from
            </th>
            <td className="py-1.5">{data.date_range.from ?? '—'}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Date to
            </th>
            <td className="py-1.5">{data.date_range.to ?? '—'}</td>
          </tr>
          <tr>
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Accounts
            </th>
            <td className="py-1.5">
              {data.accounts.length === 0
                ? '—'
                : data.accounts
                    .map((a) => `${a.account} (${a.messages.toLocaleString()})`)
                    .join(', ')}
            </td>
          </tr>
        </tbody>
      </table>
    </Panel>
  )
}

function ThreadingSection({ data }: { data: ArchiveHealth['threading'] }) {
  return (
    <Panel title="Threading">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">Threading statistics</caption>
        <tbody className="tabular-nums font-mono text-text-primary">
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Single-message threads
            </th>
            <td className="py-1.5">{data.single_message_threads.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Max thread size
            </th>
            <td className="py-1.5">{data.max_thread_size.toLocaleString()}</td>
          </tr>
          <tr>
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Null-date messages
            </th>
            <td className="py-1.5">{data.null_date_messages.toLocaleString()}</td>
          </tr>
        </tbody>
      </table>
    </Panel>
  )
}

function ExtractionSection({ data }: { data: ArchiveHealth['extraction'] }) {
  const { by_status, top_failure_reasons, by_content_type } = data
  return (
    <Panel title="Extraction">
      <div className="space-y-4">
        <table className="w-full border-collapse text-left">
          <caption className="sr-only">Extraction status summary</caption>
          <thead>
            <tr className="border-b border-steel text-text-muted">
              <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
                Status
              </th>
              <th scope="col" className="py-1.5 font-sans font-normal">
                Count
              </th>
            </tr>
          </thead>
          <tbody className="tabular-nums font-mono text-text-primary">
            <tr className="border-b border-steel">
              <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
                Extracted
              </th>
              <td className="py-1.5">{by_status.extracted.toLocaleString()}</td>
            </tr>
            <tr className="border-b border-steel">
              <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
                Failed
              </th>
              <td className="py-1.5">
                <FailedCell count={by_status.failed} />
              </td>
            </tr>
            <tr className="border-b border-steel">
              <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
                Skipped
              </th>
              <td className="py-1.5">{by_status.skipped.toLocaleString()}</td>
            </tr>
            <tr>
              <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
                Pending
              </th>
              <td className="py-1.5">{by_status.pending.toLocaleString()}</td>
            </tr>
          </tbody>
        </table>

        <table className="w-full border-collapse text-left">
          <caption className="sr-only">Top extraction failure reasons</caption>
          <thead>
            <tr className="border-b border-steel text-text-muted">
              <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
                Reason
              </th>
              <th scope="col" className="py-1.5 font-sans font-normal">
                Count
              </th>
            </tr>
          </thead>
          <tbody className="tabular-nums font-mono text-text-primary">
            {top_failure_reasons.length === 0 ? (
              <tr>
                <td colSpan={2} className="py-1.5 text-text-muted">
                  No failures
                </td>
              </tr>
            ) : (
              top_failure_reasons.map((row) => (
                <tr key={row.reason} className="border-b border-steel last:border-0">
                  <th
                    scope="row"
                    className="py-1.5 pr-4 font-sans font-normal text-text-muted"
                  >
                    {row.reason || '(empty)'}
                  </th>
                  <td className="py-1.5">
                    <FailedCell count={row.count} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>

        <table className="w-full border-collapse text-left">
          <caption className="sr-only">Extraction by content type</caption>
          <thead>
            <tr className="border-b border-steel text-text-muted">
              <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
                Content type
              </th>
              <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
                Extracted
              </th>
              <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
                Failed
              </th>
              <th scope="col" className="py-1.5 font-sans font-normal">
                Skipped
              </th>
            </tr>
          </thead>
          <tbody className="tabular-nums font-mono text-text-primary">
            {by_content_type.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-1.5 text-text-muted">
                  No content types
                </td>
              </tr>
            ) : (
              by_content_type.map((row) => (
                <tr
                  key={row.content_type}
                  className="border-b border-steel last:border-0"
                >
                  <th
                    scope="row"
                    className="py-1.5 pr-4 font-sans font-normal text-text-muted"
                  >
                    {row.content_type || '(none)'}
                  </th>
                  <td className="py-1.5 pr-4">{row.extracted.toLocaleString()}</td>
                  <td className="py-1.5 pr-4">
                    <FailedCell count={row.failed} />
                  </td>
                  <td className="py-1.5">{row.skipped.toLocaleString()}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function EmbeddingsSection({ data }: { data: ArchiveHealth['embeddings'] }) {
  return (
    <Panel title="Embeddings">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">Embedding coverage</caption>
        <thead>
          <tr className="border-b border-steel text-text-muted">
            <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
              Target
            </th>
            <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
              Embedded
            </th>
            <th scope="col" className="py-1.5 font-sans font-normal">
              Missing
            </th>
          </tr>
        </thead>
        <tbody className="tabular-nums font-mono text-text-primary">
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Emails
            </th>
            <td className="py-1.5 pr-4">{data.emails.embedded.toLocaleString()}</td>
            <td className="py-1.5">{data.emails.missing.toLocaleString()}</td>
          </tr>
          <tr>
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Attachment chunks
            </th>
            <td className="py-1.5 pr-4">
              {data.attachment_chunks.embedded.toLocaleString()}
            </td>
            <td className="py-1.5">
              {data.attachment_chunks.missing.toLocaleString()}
            </td>
          </tr>
        </tbody>
      </table>
    </Panel>
  )
}

function ImportsSection({ data }: { data: ArchiveHealth['imports'] }) {
  return (
    <Panel title="Recent imports">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">Recent import sessions</caption>
        <thead>
          <tr className="border-b border-steel text-text-muted">
            <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
              Started
            </th>
            <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
              Account
            </th>
            <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
              Status
            </th>
            <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
              Inserted
            </th>
            <th scope="col" className="py-1.5 font-sans font-normal">
              Skipped
            </th>
          </tr>
        </thead>
        <tbody className="tabular-nums font-mono text-text-primary">
          {data.length === 0 ? (
            <tr>
              <td colSpan={5} className="py-1.5 text-text-muted">
                No imports
              </td>
            </tr>
          ) : (
            data.map((row, i) => (
              <tr
                key={`${row.started_at ?? 'none'}-${row.source_account}-${i}`}
                className="border-b border-steel last:border-0"
              >
                <td className="py-1.5 pr-4">{row.started_at ?? '—'}</td>
                <td className="py-1.5 pr-4">{row.source_account}</td>
                <td className="py-1.5 pr-4">{row.status}</td>
                <td className="py-1.5 pr-4">
                  {row.messages_inserted.toLocaleString()}
                </td>
                <td className="py-1.5">{row.messages_skipped.toLocaleString()}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </Panel>
  )
}

function formatDetail(detail: Record<string, unknown>): string {
  try {
    return JSON.stringify(detail)
  } catch {
    return String(detail)
  }
}

function AuditTailSection({ data }: { data: AuditTailRow[] }) {
  return (
    <Panel title="Model & export activity">
      <table
        className="w-full border-collapse text-left"
        data-testid="audit-tail-table"
      >
        <caption className="sr-only">Model and export audit tail</caption>
        <thead>
          <tr className="border-b border-steel text-text-muted">
            <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
              At
            </th>
            <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
              User
            </th>
            <th scope="col" className="py-1.5 pr-4 font-sans font-normal">
              Action
            </th>
            <th scope="col" className="py-1.5 font-sans font-normal">
              Detail
            </th>
          </tr>
        </thead>
        <tbody className="tabular-nums font-mono text-text-primary">
          {data.length === 0 ? (
            <tr>
              <td colSpan={4} className="py-1.5 text-text-muted">
                No model or export activity
              </td>
            </tr>
          ) : (
            data.map((row, i) => (
              <tr
                key={`${row.at ?? 'none'}-${row.action}-${i}`}
                className="border-b border-steel last:border-0"
              >
                <td className="py-1.5 pr-4">{row.at ?? '—'}</td>
                <td className="py-1.5 pr-4">{row.username}</td>
                <td className="py-1.5 pr-4">{row.action}</td>
                <td className="max-w-md truncate py-1.5 text-xs text-text-muted">
                  {formatDetail(row.detail ?? {})}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </Panel>
  )
}

function formatGeneratedAt(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export function DataHealthPage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useArchiveHealth()

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-base font-medium text-text-primary">Data Health</h1>
        {data ? (
          <p className="text-text-muted tabular-nums">
            Generated {formatGeneratedAt(data.generated_at)}
          </p>
        ) : null}
      </div>

      {isLoading ? <HealthSkeleton /> : null}

      {isError ? (
        <div
          role="alert"
          className="rounded-lg border border-conflict bg-graphite-900 p-4 text-conflict"
        >
          <p className="mb-2">
            Failed to load data health
            {error instanceof Error ? `: ${error.message}` : ''}
          </p>
          <button
            type="button"
            onClick={() => void refetch()}
            disabled={isFetching}
            className="rounded-md border border-steel bg-graphite-800 px-3 py-1.5 text-text-primary"
          >
            Retry
          </button>
        </div>
      ) : null}

      {data ? (
        <div className="space-y-4">
          <CoverageSection data={data.coverage} />
          <ThreadingSection data={data.threading} />
          <ExtractionSection data={data.extraction} />
          <EmbeddingsSection data={data.embeddings} />
          <ImportsSection data={data.imports} />
          <AuditTailSection data={data.audit_tail ?? []} />
        </div>
      ) : null}
    </div>
  )
}
