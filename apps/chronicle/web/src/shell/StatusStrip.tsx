import { useArchiveSummary } from '../routes/useArchiveSummary'

function yearFromIso(iso: string | null | undefined): string | null {
  if (!iso) return null
  const y = iso.slice(0, 4)
  return /^\d{4}$/.test(y) ? y : null
}

export function StatusStrip() {
  const { data } = useArchiveSummary()

  let coverage: string | null = null
  if (data) {
    const fromY = yearFromIso(data.date_range.from)
    const toY = yearFromIso(data.date_range.to)
    const range =
      fromY && toY ? `${fromY}–${toY}` : fromY || toY || '—'
    coverage = `${data.counts.messages.toLocaleString()} messages · ${range}`
  }

  return (
    <footer
      className="col-span-3 flex items-center justify-between border-t border-steel bg-graphite-900 px-3 text-text-muted"
      style={{ height: 24 }}
    >
      <span className="tabular-nums font-mono text-[11px]">
        {coverage ?? 'Archive coverage loading…'}
      </span>
      <span className="text-[11px]">grok/claude build</span>
    </footer>
  )
}
