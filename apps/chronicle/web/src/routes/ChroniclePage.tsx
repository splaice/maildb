import type { ArchiveSummary } from '../api/types'
import { useArchiveSummary } from './useArchiveSummary'

function SummarySkeleton() {
  return (
    <div
      className="animate-pulse space-y-2 rounded-lg border border-steel bg-graphite-900 p-4"
      data-testid="archive-summary-skeleton"
      aria-busy="true"
      aria-label="Loading archive coverage"
    >
      <div className="h-4 w-40 rounded bg-graphite-800" />
      <div className="h-3 w-full rounded bg-graphite-800" />
      <div className="h-3 w-5/6 rounded bg-graphite-800" />
      <div className="h-3 w-2/3 rounded bg-graphite-800" />
    </div>
  )
}

function formatYear(iso: string | null): string {
  if (!iso) return '—'
  return iso.slice(0, 4)
}

function CoverageTable({ data }: { data: ArchiveSummary }) {
  const { counts, extraction, embedding, date_range } = data
  return (
    <div className="rounded-lg border border-steel bg-graphite-900 p-4">
      <h2 className="mb-3 text-sm font-medium text-text-primary">Archive coverage</h2>
      <table className="w-full border-collapse text-left">
        <tbody className="tabular-nums font-mono text-text-primary">
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Messages
            </th>
            <td className="py-1.5">{counts.messages.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Threads
            </th>
            <td className="py-1.5">{counts.threads.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Attachments
            </th>
            <td className="py-1.5">{counts.attachments.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Contacts
            </th>
            <td className="py-1.5">{counts.contacts.toLocaleString()}</td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Date range
            </th>
            <td className="py-1.5">
              {formatYear(date_range.from)}–{formatYear(date_range.to)}
            </td>
          </tr>
          <tr className="border-b border-steel">
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Extraction
            </th>
            <td className="py-1.5">
              {extraction.extracted} extracted / {extraction.failed} failed /{' '}
              {extraction.skipped} skipped / {extraction.pending} pending
            </td>
          </tr>
          <tr>
            <th scope="row" className="py-1.5 pr-4 font-sans font-normal text-text-muted">
              Embedding
            </th>
            <td className="py-1.5">
              {embedding.embedded} embedded / {embedding.missing} missing
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export function ChroniclePage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useArchiveSummary()

  return (
    <div className="space-y-4">
      <h1 className="text-base font-medium text-text-primary">Chronicle</h1>
      {isLoading ? <SummarySkeleton /> : null}
      {isError ? (
        <div
          role="alert"
          className="rounded-lg border border-conflict bg-graphite-900 p-4 text-conflict"
        >
          <p className="mb-2">
            Failed to load archive coverage
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
      {data ? <CoverageTable data={data} /> : null}
    </div>
  )
}
