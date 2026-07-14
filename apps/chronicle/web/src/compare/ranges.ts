import type { Viewport } from '../chronicle/timeScale'
import { formatPeriodLabel } from '../chronicle/timeScale'

/** Default before/after window around an event anchor (Table 16). */
export const COMPARE_EVENT_DELTA_MS = 90 * 24 * 60 * 60 * 1000

/**
 * Entry ranges for Shift+C / toolbar Compare (spec §4.3 / §14.2).
 * - With a brush: A = current viewport, B = brush.
 * - Without: B = A shifted back by A's duration ("previous period").
 */
export function compareRangesFromEntry(
  viewport: Viewport,
  brush: Viewport | null,
): { a: Viewport; b: Viewport } {
  if (brush && brush.toMs > brush.fromMs) {
    return {
      a: { fromMs: viewport.fromMs, toMs: viewport.toMs },
      b: { fromMs: brush.fromMs, toMs: brush.toMs },
    }
  }
  const duration = viewport.toMs - viewport.fromMs
  return {
    a: { fromMs: viewport.fromMs, toMs: viewport.toMs },
    b: { fromMs: viewport.fromMs - duration, toMs: viewport.fromMs },
  }
}

/**
 * Event before/after anchor: A = [start−Δ, start), B = [start, start+Δ), Δ = 90d.
 */
export function compareRangesAroundEvent(
  eventStartMs: number,
  deltaMs: number = COMPARE_EVENT_DELTA_MS,
): { a: Viewport; b: Viewport } {
  return {
    a: { fromMs: eventStartMs - deltaMs, toMs: eventStartMs },
    b: { fromMs: eventStartMs, toMs: eventStartMs + deltaMs },
  }
}

/** Relative difference of two durations vs the longer (0–1). */
export function durationRelativeDiff(aMs: number, bMs: number): number {
  const longer = Math.max(aMs, bMs)
  if (longer <= 0) return 0
  return Math.abs(aMs - bMs) / longer
}

/** Durations within 5% → aligned lanes (server also computes this). */
export function durationsWithinAlignTolerance(
  a: Viewport,
  b: Viewport,
  tolerance = 0.05,
): boolean {
  const da = a.toMs - a.fromMs
  const db = b.toMs - b.fromMs
  return durationRelativeDiff(da, db) <= tolerance
}

/**
 * Human label for a compare panel duration, e.g. "Jan–Jun 2015 · 6 months".
 */
export function formatCompareDurationLabel(vp: Viewport): string {
  const from = formatPeriodLabel(vp.fromMs)
  const to = formatPeriodLabel(vp.toMs)
  const spanMs = Math.max(0, vp.toMs - vp.fromMs)
  const days = spanMs / (24 * 60 * 60 * 1000)
  let durationText: string
  if (days >= 360) {
    const years = Math.round(days / 365)
    durationText = years === 1 ? '1 year' : `${years} years`
  } else if (days >= 27) {
    const months = Math.max(1, Math.round(days / 30.44))
    durationText = months === 1 ? '1 month' : `${months} months`
  } else if (days >= 6) {
    const weeks = Math.max(1, Math.round(days / 7))
    durationText = weeks === 1 ? '1 week' : `${weeks} weeks`
  } else {
    const d = Math.max(1, Math.round(days))
    durationText = d === 1 ? '1 day' : `${d} days`
  }
  // Compact same-year range: "Jan–Jun 2015"
  const fromY = new Date(vp.fromMs).getUTCFullYear()
  const toY = new Date(vp.toMs).getUTCFullYear()
  const fromM = new Date(vp.fromMs).toLocaleString('en-US', {
    month: 'short',
    timeZone: 'UTC',
  })
  const toM = new Date(vp.toMs).toLocaleString('en-US', {
    month: 'short',
    timeZone: 'UTC',
  })
  let rangeText: string
  if (fromY === toY) {
    rangeText = fromM === toM ? `${fromM} ${fromY}` : `${fromM}–${toM} ${fromY}`
  } else {
    rangeText = `${from} – ${to}`
  }
  return `${rangeText} · ${durationText}`
}

/**
 * Percent delta of B vs A: (b − a) / a × 100. Null when A is zero.
 */
export function percentDelta(a: number, b: number): number | null {
  if (a === 0) return b === 0 ? 0 : null
  return ((b - a) / a) * 100
}

/** Format "+38%" / "−12%" / "n/a" for totals row. */
export function formatPercentDelta(a: number, b: number): string {
  const d = percentDelta(a, b)
  if (d == null) return 'n/a'
  const rounded = Math.round(d)
  if (rounded > 0) return `+${rounded}%`
  return `${rounded}%`
}
