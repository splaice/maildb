/** Origin badge text for topics (TA-002 — always visible). */

const ORIGIN_LABEL: Record<string, string> = {
  automatic: 'Automatic',
  curated: 'Curated',
  manual: 'Manual',
}

export function originLabel(origin: string): string {
  return ORIGIN_LABEL[origin] ?? origin
}

export function OriginBadge({ origin }: { origin: string }) {
  return (
    <span
      className="rounded border border-steel bg-graphite-800 px-1 py-0.5 text-[10px] uppercase tracking-wide text-text-muted"
      data-testid="topic-origin-badge"
      data-origin={origin}
    >
      {originLabel(origin)}
    </span>
  )
}
