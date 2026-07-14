import type { SearchResult } from '../api/types'
import { highlightSnippet } from './highlightSnippet'

export interface ResultCardProps {
  result: SearchResult
  freeText: string
  selected: boolean
  onSelect: () => void
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  // Tabular date display
  return iso.replace('T', ' ').replace(/\.\d{3}Z?$/, 'Z').slice(0, 19)
}

function typeIconText(contentType: string | null, filename: string): string {
  const ct = (contentType || '').toLowerCase()
  const fn = filename.toLowerCase()
  if (ct.includes('pdf') || fn.endsWith('.pdf')) return 'PDF'
  if (ct.includes('image') || /\.(png|jpe?g|gif|webp)$/.test(fn)) return 'IMG'
  if (ct.includes('sheet') || ct.includes('excel') || /\.(xlsx?|csv)$/.test(fn)) return 'XLS'
  if (ct.includes('word') || /\.(docx?|rtf)$/.test(fn)) return 'DOC'
  if (ct.includes('zip') || ct.includes('archive')) return 'ZIP'
  return 'FILE'
}

function MatchExplanation({ match }: { match: SearchResult['match'] }) {
  const rows: string[] = [`kind: ${match.kind}`]
  if (match.kind === 'exact' && match.field) rows.push(`field: ${match.field}`)
  if (match.kind === 'semantic') {
    if (match.similarity != null) rows.push(`similarity: ${match.similarity}`)
  }
  if (match.kind === 'hybrid') {
    if (match.exact_rank != null) rows.push(`exact_rank: ${match.exact_rank}`)
    if (match.semantic_rank != null) rows.push(`semantic_rank: ${match.semantic_rank}`)
    if (match.similarity != null) rows.push(`similarity: ${match.similarity}`)
  }
  return (
    <div className="mt-1 space-y-0.5 font-mono text-[11px] text-text-muted" data-testid="match-explanation">
      {rows.map((r) => (
        <div key={r}>{r}</div>
      ))}
    </div>
  )
}

export function ResultCard({ result, freeText, selected, onSelect }: ResultCardProps) {
  const typeLabel = result.result_type.toUpperCase()

  return (
    <article
      role="button"
      tabIndex={0}
      data-testid={`result-card-${result.id}`}
      data-result-id={result.id}
      data-result-type={result.result_type}
      aria-selected={selected}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect()
        }
      }}
      className={[
        'cursor-pointer rounded-md border px-3 py-2 text-left transition-opacity',
        selected
          ? 'border-action bg-graphite-800'
          : 'border-steel bg-graphite-900 hover:border-steel hover:bg-graphite-800',
      ].join(' ')}
    >
      <div className="mb-1 flex items-center gap-2">
        <span
          className="text-[10px] font-medium uppercase tracking-wide text-text-muted"
          data-testid="result-type-label"
        >
          {typeLabel}
        </span>
        {result.result_type === 'message' && result.has_attachment ? (
          <span className="rounded bg-attachment/20 px-1 text-[10px] text-attachment">
            attachment
          </span>
        ) : null}
        {result.result_type === 'message' &&
        result.thread_size != null &&
        result.thread_size > 1 ? (
          <span className="rounded bg-graphite-800 px-1 text-[10px] text-text-muted tabular-nums">
            thread · {result.thread_size}
          </span>
        ) : null}
      </div>

      {result.result_type === 'message' ? (
        <>
          <h3 className="text-sm font-medium text-text-primary">
            {result.subject || '(no subject)'}
          </h3>
          <p className="text-[11px] text-text-muted">
            {result.sender_name || result.sender || '—'}
            {result.sender_name && result.sender ? ` <${result.sender}>` : ''}
          </p>
          <p className="tabular-nums font-mono text-[11px] text-text-muted">
            {formatDate(result.date)}
          </p>
          <p className="text-[11px] text-text-muted">Mailbox: {result.mailbox || '—'}</p>
        </>
      ) : (
        <>
          <h3 className="flex items-center gap-2 text-sm font-medium text-text-primary">
            <span className="rounded border border-steel px-1 font-mono text-[10px] text-text-muted">
              {typeIconText(result.content_type, result.filename)}
            </span>
            {result.filename}
          </h3>
          <p className="text-[11px] text-text-muted">
            Source: {result.sender || '—'}
            {result.source_message_id ? ` · ${result.source_message_id}` : ''}
          </p>
          <p className="tabular-nums font-mono text-[11px] text-text-muted">
            {formatDate(result.date)}
          </p>
          {result.extraction_status ? (
            <p className="text-[11px] text-text-muted">
              Extraction: {result.extraction_status}
            </p>
          ) : null}
        </>
      )}

      {result.snippet ? (
        <p className="mt-1 text-[12px] text-text-primary" data-testid="result-snippet">
          {highlightSnippet(result.snippet, freeText)}
        </p>
      ) : null}

      <details className="mt-1" data-testid="why-matched">
        <summary className="cursor-pointer text-[11px] text-text-muted">
          Why this matched
        </summary>
        <MatchExplanation match={result.match} />
      </details>
    </article>
  )
}
