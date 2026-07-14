/**
 * Client-side date phrase parser for Explore mode and palette date jumps.
 * Accepts years, year ranges, and month-year phrases. Returns null for garbage.
 */

export interface ParsedDateRange {
  /** Inclusive start (UTC midnight). */
  fromMs: number
  /** Exclusive end (UTC midnight of day after last day). */
  toMs: number
  /** Human label for palette display. */
  label: string
}

const MONTHS: Record<string, number> = {
  january: 0,
  jan: 0,
  february: 1,
  feb: 1,
  march: 2,
  mar: 2,
  april: 3,
  apr: 3,
  may: 4,
  june: 5,
  jun: 5,
  july: 6,
  jul: 6,
  august: 7,
  aug: 7,
  september: 8,
  sep: 8,
  sept: 8,
  october: 9,
  oct: 9,
  november: 10,
  nov: 10,
  december: 11,
  dec: 11,
}

function utcMs(y: number, m: number, d: number): number {
  return Date.UTC(y, m, d)
}

/** Last day of month as exclusive end = first of next month. */
function monthEndExclusive(y: number, m: number): number {
  return Date.UTC(y, m + 1, 1)
}

/**
 * Parse a short date phrase into a viewport range.
 * Supported:
 * - `2015` → full calendar year
 * - `2014..2018` / `2014-2018` → inclusive multi-year span
 * - `june 2015` / `Jun 2015` → full calendar month
 * Unparseable input → null.
 */
export function parseDatePhrase(raw: string): ParsedDateRange | null {
  const text = raw.trim().toLowerCase().replace(/\s+/g, ' ')
  if (!text) return null

  // Year range: 2014..2018 or 2014-2018
  const rangeMatch = /^(\d{4})\s*(?:\.\.|-|–|—)\s*(\d{4})$/.exec(text)
  if (rangeMatch) {
    const y1 = Number(rangeMatch[1])
    const y2 = Number(rangeMatch[2])
    if (y1 < 1900 || y2 > 2100 || y2 < y1) return null
    return {
      fromMs: utcMs(y1, 0, 1),
      toMs: utcMs(y2 + 1, 0, 1),
      label: `${y1}–${y2}`,
    }
  }

  // Single year
  if (/^\d{4}$/.test(text)) {
    const y = Number(text)
    if (y < 1900 || y > 2100) return null
    return {
      fromMs: utcMs(y, 0, 1),
      toMs: utcMs(y + 1, 0, 1),
      label: String(y),
    }
  }

  // Month year: "june 2015", "jun 2015"
  const monthYear = /^([a-z]+)\s+(\d{4})$/.exec(text)
  if (monthYear) {
    const monthName = monthYear[1]!
    const y = Number(monthYear[2])
    const m = MONTHS[monthName]
    if (m === undefined || y < 1900 || y > 2100) return null
    const labelMonth = monthName.charAt(0).toUpperCase() + monthName.slice(1, 3)
    return {
      fromMs: utcMs(y, m, 1),
      toMs: monthEndExclusive(y, m),
      label: `${labelMonth} ${y}`,
    }
  }

  return null
}
