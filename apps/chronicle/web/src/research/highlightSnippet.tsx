import type { ReactNode } from 'react'

/**
 * Emphasize free-text hits in a snippet via React nodes (never raw HTML).
 * Escapes all text content — only `<mark>` is structural.
 */
export function highlightSnippet(
  snippet: string,
  freeText: string | null | undefined,
): ReactNode {
  if (!snippet) return null
  const needle = (freeText ?? '').trim()
  if (!needle) return snippet

  const lower = snippet.toLowerCase()
  const n = needle.toLowerCase()
  const parts: ReactNode[] = []
  let start = 0
  let idx = lower.indexOf(n, start)
  let key = 0

  while (idx >= 0) {
    if (idx > start) {
      parts.push(snippet.slice(start, idx))
    }
    parts.push(
      <mark key={`m-${key++}`} className="bg-event/30 text-text-primary">
        {snippet.slice(idx, idx + needle.length)}
      </mark>,
    )
    start = idx + needle.length
    idx = lower.indexOf(n, start)
  }
  if (start < snippet.length) {
    parts.push(snippet.slice(start))
  }
  return parts.length === 1 && typeof parts[0] === 'string' ? parts[0] : <>{parts}</>
}
