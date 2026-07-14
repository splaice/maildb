import { useQuery } from '@tanstack/react-query'

import { getAttachmentFamily } from './api'
import { formatBytes } from './format'
import type { FamilyCandidate } from './types'

export interface FamilyPanelProps {
  attSid: string
  onClose: () => void
  onCompare: (a: string, b: string) => void
  onSelect: (attSid: string) => void
}

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

function confidenceLabel(c: FamilyCandidate): string {
  if (c.confidence === 'exact-duplicate') return 'exact duplicate'
  return 'probable version'
}

export function FamilyPanel({
  attSid,
  onClose,
  onCompare,
  onSelect,
}: FamilyPanelProps) {
  const query = useQuery({
    queryKey: ['attachments', 'family', attSid],
    queryFn: ({ signal }) => getAttachmentFamily(attSid, signal),
    retry: false,
  })

  const candidates = query.data?.candidates ?? []

  return (
    <div
      className="rounded-md border border-steel bg-graphite-900 p-3"
      data-testid="family-panel"
    >
      <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium text-text-primary">
            Version family
          </h3>
          {query.data?.stem ? (
            <p className="font-mono text-[11px] text-text-muted" data-testid="family-stem">
              stem: {query.data.stem}
            </p>
          ) : null}
        </div>
        <button type="button" className={btnClass} onClick={onClose} data-testid="family-close">
          Close
        </button>
      </header>

      {query.isLoading ? (
        <p className="text-[12px] text-text-muted" data-testid="family-loading">
          Loading family…
        </p>
      ) : null}
      {query.isError ? (
        <p role="alert" className="text-conflict" data-testid="family-error">
          Failed to load version family
        </p>
      ) : null}

      {candidates.length > 0 ? (
        <ul className="space-y-2" data-testid="family-candidates">
          {candidates.map((c) => (
            <li
              key={c.id}
              className="rounded border border-steel/60 bg-graphite-950 px-2 py-1.5"
              data-testid={`family-candidate-${c.id}`}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <button
                  type="button"
                  className="text-left text-[12px] text-action underline"
                  onClick={() => onSelect(c.id)}
                  data-testid={`family-select-${c.id}`}
                >
                  {c.filename}
                </button>
                <span
                  className="text-[10px] text-text-muted"
                  data-testid={`family-confidence-${c.id}`}
                >
                  {confidenceLabel(c)}
                  {c.signals.length > 0
                    ? ` · signals: ${c.signals.join(', ')}`
                    : ''}
                </span>
              </div>
              <p className="mt-0.5 font-mono text-[10px] tabular-nums text-text-muted">
                {c.date ? c.date.slice(0, 10) : '—'} · {c.sender || '—'} ·{' '}
                {formatBytes(c.size)}
              </p>
              <div className="mt-1 flex flex-wrap gap-1">
                {candidates
                  .filter((other) => other.id !== c.id)
                  .map((other) => (
                    <button
                      key={other.id}
                      type="button"
                      className={btnClass}
                      data-testid={`family-compare-${c.id}-${other.id}`}
                      onClick={() => onCompare(c.id, other.id)}
                    >
                      Compare with {other.filename.length > 24
                        ? `${other.filename.slice(0, 23)}…`
                        : other.filename}
                    </button>
                  ))}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {!query.isLoading && !query.isError && candidates.length === 0 ? (
        <p className="text-[12px] text-text-muted">No family candidates</p>
      ) : null}
    </div>
  )
}
