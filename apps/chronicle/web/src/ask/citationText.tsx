/**
 * Parse answer text for [S#] markers and render citation chips as React nodes.
 * Never uses innerHTML.
 */

import type { ReactNode } from 'react'

import type { AskCitationEvent } from './sseClient'

const MARKER_RE = /\[(S\d+)\]/g

export function renderAnswerWithCitations(
  text: string,
  citations: AskCitationEvent[],
  onCitationClick: (citation: AskCitationEvent) => void,
): ReactNode[] {
  const byMarker = new Map<string, AskCitationEvent>()
  for (const c of citations) {
    // marker may be "[S1]" or "S1"
    const key = c.marker.replace(/^\[|\]$/g, '')
    byMarker.set(key, c)
    byMarker.set(c.marker, c)
  }

  const nodes: ReactNode[] = []
  let last = 0
  let match: RegExpExecArray | null
  const re = new RegExp(MARKER_RE.source, 'g')
  let i = 0
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(text.slice(last, match.index))
    }
    const raw = match[0]
    const key = match[1]!
    const cit = byMarker.get(key) ?? byMarker.get(raw)
    if (cit) {
      nodes.push(
        <button
          key={`cit-${i}-${key}`}
          type="button"
          className="mx-0.5 inline rounded border border-action/40 bg-graphite-800 px-1 py-0 font-mono text-[11px] text-action hover:bg-graphite-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-action"
          data-testid={`citation-chip-${key}`}
          onClick={() => onCitationClick(cit)}
        >
          {raw}
        </button>,
      )
    } else {
      nodes.push(
        <span
          key={`unmatched-${i}-${key}`}
          className="mx-0.5 font-mono text-[11px] text-text-muted"
          data-testid={`citation-unmatched-${key}`}
        >
          {raw}
        </span>,
      )
    }
    last = match.index + raw.length
    i += 1
  }
  if (last < text.length) {
    nodes.push(text.slice(last))
  }
  return nodes
}
