/**
 * Display helpers for event cards and marks (origin text, precision labels).
 */

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
] as const

/** Origin labels displayed in every event representation (Table 15). */
export const ORIGIN_LABELS: Record<string, string> = {
  source: 'Source',
  imported: 'Imported',
  automatic: 'Automatic',
  analyst: 'Analyst',
}

export function originLabel(origin: string): string {
  return ORIGIN_LABELS[origin] ?? origin
}

/**
 * Time with precision label, e.g. "June 2015 · month precision".
 */
export function formatEventTime(
  timeStartIso: string,
  timePrecision: string,
  timeEndIso?: string | null,
): string {
  const ms = Date.parse(timeStartIso)
  if (!Number.isFinite(ms)) return timeStartIso
  const d = new Date(ms)
  const y = d.getUTCFullYear()
  const m = MONTHS[d.getUTCMonth()] ?? ''
  const day = d.getUTCDate()

  let when: string
  switch (timePrecision) {
    case 'year':
      when = String(y)
      break
    case 'quarter': {
      const q = Math.floor(d.getUTCMonth() / 3) + 1
      when = `Q${q} ${y}`
      break
    }
    case 'month':
      when = `${m} ${y}`
      break
    case 'week':
      when = `week of ${m} ${day}, ${y}`
      break
    case 'hour':
      when = `${m} ${day}, ${y} ${String(d.getUTCHours()).padStart(2, '0')}:00 UTC`
      break
    case 'day':
    default:
      when = `${m} ${day}, ${y}`
      break
  }

  if (timeEndIso) {
    const endMs = Date.parse(timeEndIso)
    if (Number.isFinite(endMs) && endMs !== ms) {
      const e = new Date(endMs)
      const endLabel = `${MONTHS[e.getUTCMonth()]} ${e.getUTCDate()}, ${e.getUTCFullYear()}`
      when = `${when} – ${endLabel}`
    }
  }

  return `${when} · ${timePrecision} precision`
}
