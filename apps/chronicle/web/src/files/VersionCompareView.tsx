import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { getAttachmentCompare } from './api'
import { formatBytes, previewUrl } from './format'
import type { AttachmentMeta, DiffLine } from './types'

export interface VersionCompareViewProps {
  a: string
  b: string
  onClose: () => void
}

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary enabled:hover:bg-graphite-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

function MetaCard({ label, meta }: { label: string; meta: AttachmentMeta }) {
  return (
    <div
      className="rounded border border-steel bg-graphite-950 p-2"
      data-testid={`compare-meta-${label}`}
    >
      <p className="text-[10px] font-medium uppercase tracking-wide text-text-muted">
        {label}
      </p>
      <p className="text-[12px] text-text-primary" title={meta.filename}>
        {meta.filename}
      </p>
      <p className="font-mono text-[10px] tabular-nums text-text-muted">
        {meta.date ? meta.date.slice(0, 10) : '—'} · {meta.sender || '—'}
      </p>
      <p className="font-mono text-[10px] text-text-muted">
        {formatBytes(meta.size)} · {meta.content_type || '—'}
      </p>
      <p className="truncate font-mono text-[10px] text-text-muted" title={meta.sha256}>
        sha256: {meta.sha256.slice(0, 12)}…
      </p>
      <Link
        to={previewUrl(meta.id)}
        target="_blank"
        rel="noreferrer"
        className="mt-1 inline-block text-[11px] text-action underline"
        data-testid={`compare-preview-${label}`}
      >
        Preview
      </Link>
    </div>
  )
}

/** Text-prefixed diff line (not color-only). */
function DiffLineRow({ line }: { line: DiffLine }) {
  const prefix =
    line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' '
  const color =
    line.kind === 'add'
      ? 'text-emerald-400'
      : line.kind === 'del'
        ? 'text-conflict'
        : 'text-text-muted'
  return (
    <div
      className={`font-mono text-[11px] whitespace-pre-wrap ${color}`}
      data-testid={`diff-line-${line.kind}`}
      data-diff-kind={line.kind}
    >
      <span className="select-none" aria-hidden>
        {prefix}
      </span>
      {line.text}
    </div>
  )
}

export function VersionCompareView({ a, b, onClose }: VersionCompareViewProps) {
  const query = useQuery({
    queryKey: ['attachments', 'compare', a, b],
    queryFn: ({ signal }) => getAttachmentCompare(a, b, signal),
    retry: false,
  })

  return (
    <div
      className="flex min-h-0 flex-1 flex-col gap-3 rounded-md border border-steel bg-graphite-900 p-3"
      data-testid="version-compare-view"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-text-primary">
          Compare versions
        </h3>
        <button type="button" className={btnClass} onClick={onClose} data-testid="compare-close">
          Close
        </button>
      </header>

      {query.isLoading ? (
        <p className="text-[12px] text-text-muted" data-testid="compare-loading">
          Loading comparison…
        </p>
      ) : null}

      {query.isError ? (
        <div role="alert" className="text-conflict" data-testid="compare-error">
          Failed to compare (extraction may be missing)
          <button
            type="button"
            className={`${btnClass} ml-2`}
            onClick={() => void query.refetch()}
          >
            Retry
          </button>
        </div>
      ) : null}

      {query.data ? (
        <>
          <div
            className="grid gap-2 sm:grid-cols-2"
            data-testid="compare-metadata"
          >
            <MetaCard label="a" meta={query.data.a} />
            <MetaCard label="b" meta={query.data.b} />
          </div>

          {query.data.amount_changes.length > 0 ? (
            <section data-testid="compare-amount-changes">
              <h4 className="mb-1 text-[11px] font-medium text-text-primary">
                Changed amounts
              </h4>
              <ul className="space-y-0.5 rounded border border-steel bg-graphite-950 p-2">
                {query.data.amount_changes.map((ch, i) => (
                  <li
                    key={`${ch.kind}-${i}`}
                    className={`font-mono text-[11px] ${
                      ch.kind === 'add' ? 'text-emerald-400' : 'text-conflict'
                    }`}
                    data-testid={`amount-change-${i}`}
                  >
                    <span className="select-none" aria-hidden>
                      {ch.kind === 'add' ? '+' : '−'}
                    </span>
                    {ch.text}
                    <span className="ml-1 text-text-muted">
                      ({ch.amounts.join(', ')})
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <p className="text-[11px] text-text-muted" data-testid="compare-no-amounts">
              No amount changes detected
            </p>
          )}

          <section data-testid="compare-diff">
            <h4 className="mb-1 text-[11px] font-medium text-text-primary">
              Text diff
              {query.data.truncated ? ' (truncated)' : ''}
            </h4>
            {query.data.hunks.length === 0 ? (
              <p className="text-[11px] text-text-muted">No differences</p>
            ) : (
              <div className="max-h-80 space-y-3 overflow-auto rounded border border-steel bg-graphite-950 p-2">
                {query.data.hunks.map((hunk, hi) => (
                  <div
                    key={hi}
                    className="border-b border-steel/40 pb-2 last:border-0"
                    data-testid={`diff-hunk-${hi}`}
                  >
                    <p className="mb-0.5 font-mono text-[10px] text-text-muted">
                      @@ a:{hunk.a_start} b:{hunk.b_start} @@
                    </p>
                    {hunk.lines.map((line, li) => (
                      <DiffLineRow key={li} line={line} />
                    ))}
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  )
}
