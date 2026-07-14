/** Pure time↔pixel math for the Chronicle timeline. No DOM / React. */

export interface Viewport {
  fromMs: number
  toMs: number
}

export type Unit = 'hour' | 'day' | 'week' | 'month' | 'quarter' | 'year'

/** Minimum zoom span: 1 hour. */
export const MIN_SPAN_MS = 60 * 60 * 1000

/** Approximate unit widths (ms) for bar sizing and tick steps. */
export const UNIT_MS: Record<Unit, number> = {
  hour: 60 * 60 * 1000,
  day: 24 * 60 * 60 * 1000,
  week: 7 * 24 * 60 * 60 * 1000,
  month: 30 * 24 * 60 * 60 * 1000,
  quarter: 91 * 24 * 60 * 60 * 1000,
  year: 365 * 24 * 60 * 60 * 1000,
}

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const

export function xForTime(t: number, vp: Viewport, width: number): number {
  const span = vp.toMs - vp.fromMs
  if (span <= 0 || width <= 0) return 0
  return ((t - vp.fromMs) / span) * width
}

export function timeForX(x: number, vp: Viewport, width: number): number {
  const span = vp.toMs - vp.fromMs
  if (width <= 0) return vp.fromMs
  return vp.fromMs + (x / width) * span
}

/** Shift viewport later by `deltaPx` pixels (positive → later times). */
export function panViewport(vp: Viewport, deltaPx: number, width: number): Viewport {
  if (width <= 0) return { ...vp }
  const span = vp.toMs - vp.fromMs
  const deltaMs = (deltaPx / width) * span
  return { fromMs: vp.fromMs + deltaMs, toMs: vp.toMs + deltaMs }
}

/**
 * Zoom viewport by `factor` (span multiplier), keeping the time at `anchorPx` fixed.
 * factor < 1 zooms in; factor > 1 zooms out.
 * Invariant: timeForX(anchorPx) is identical before and after.
 */
export function zoomViewport(
  vp: Viewport,
  factor: number,
  anchorPx: number,
  width: number,
): Viewport {
  if (width <= 0 || !Number.isFinite(factor) || factor <= 0) return { ...vp }
  const anchorTime = timeForX(anchorPx, vp, width)
  const span = vp.toMs - vp.fromMs
  const newSpan = span * factor
  const fromMs = anchorTime - (anchorPx / width) * newSpan
  return { fromMs, toMs: fromMs + newSpan }
}

/**
 * Map wheel deltaY to a zoom factor: exp(-deltaY * 0.002), clamped to [0.5, 2].
 */
export function wheelZoomFactor(deltaY: number): number {
  const raw = Math.exp(-deltaY * 0.002)
  return Math.min(2, Math.max(0.5, raw))
}

/**
 * Clamp span to [minSpanMs, extentSpan * 1.2] and keep the window within
 * the extent expanded by the max-span slack.
 */
export function clampViewport(
  vp: Viewport,
  extent: Viewport,
  minSpanMs: number,
): Viewport {
  const extentSpan = Math.max(extent.toMs - extent.fromMs, minSpanMs)
  const maxSpan = extentSpan * 1.2

  let span = vp.toMs - vp.fromMs
  if (!Number.isFinite(span) || span <= 0) {
    span = extentSpan
  }
  span = Math.min(Math.max(span, minSpanMs), maxSpan)

  const mid = (vp.fromMs + vp.toMs) / 2
  let fromMs = mid - span / 2
  let toMs = mid + span / 2

  const slack = (maxSpan - extentSpan) / 2
  const boundFrom = extent.fromMs - slack
  const boundTo = extent.toMs + slack

  if (fromMs < boundFrom) {
    fromMs = boundFrom
    toMs = fromMs + span
  }
  if (toMs > boundTo) {
    toMs = boundTo
    fromMs = toMs - span
  }
  if (fromMs < boundFrom) {
    fromMs = boundFrom
    toMs = Math.min(fromMs + span, boundTo)
  }

  return { fromMs, toMs }
}

export function bucketWidthPx(unit: Unit, vp: Viewport, width: number): number {
  const span = vp.toMs - vp.fromMs
  if (span <= 0 || width <= 0) return 0
  return (UNIT_MS[unit] / span) * width
}

export interface Tick {
  timeMs: number
  label: string
  major: boolean
}

type LabelKind = 'year' | 'quarter' | 'month' | 'day' | 'hour'

function labelFor(timeMs: number, kind: LabelKind): string {
  const d = new Date(timeMs)
  const y = d.getUTCFullYear()
  const mon = MONTHS[d.getUTCMonth()]
  const day = d.getUTCDate()
  const h = d.getUTCHours()
  switch (kind) {
    case 'year':
      return String(y)
    case 'quarter':
      return `Q${Math.floor(d.getUTCMonth() / 3) + 1} ${y}`
    case 'month':
      return `${mon} ${y}`
    case 'day':
      return `${day} ${mon}`
    case 'hour':
      return `${day} ${mon} ${String(h).padStart(2, '0')}:00`
  }
}

/** Nice step sizes in ms, coarsest last. */
const NICE_STEPS_MS: { ms: number; kind: LabelKind; majorEvery: number }[] = [
  { ms: UNIT_MS.hour, kind: 'hour', majorEvery: 6 },
  { ms: 3 * UNIT_MS.hour, kind: 'hour', majorEvery: 4 },
  { ms: 6 * UNIT_MS.hour, kind: 'hour', majorEvery: 4 },
  { ms: 12 * UNIT_MS.hour, kind: 'hour', majorEvery: 2 },
  { ms: UNIT_MS.day, kind: 'day', majorEvery: 7 },
  { ms: 2 * UNIT_MS.day, kind: 'day', majorEvery: 7 },
  { ms: UNIT_MS.week, kind: 'day', majorEvery: 4 },
  { ms: UNIT_MS.month, kind: 'month', majorEvery: 3 },
  { ms: 3 * UNIT_MS.month, kind: 'quarter', majorEvery: 4 },
  { ms: UNIT_MS.year, kind: 'year', majorEvery: 5 },
  { ms: 2 * UNIT_MS.year, kind: 'year', majorEvery: 5 },
  { ms: 5 * UNIT_MS.year, kind: 'year', majorEvery: 2 },
  { ms: 10 * UNIT_MS.year, kind: 'year', majorEvery: 1 },
]

/**
 * Tick density ≈ every 80–120 px. Labels: year / "Q1 2015" / "Jan 2015" /
 * "12 Jan" / "12 Jan 14:00" by span.
 */
export function ticksFor(vp: Viewport, width: number): Tick[] {
  if (width <= 0) return []
  const span = vp.toMs - vp.fromMs
  if (span <= 0) return []

  // Target ~100 px between ticks (within 80–120).
  const targetStepMs = (100 / width) * span

  // Pick the smallest nice step that is ≥ target (avoids overcrowding).
  let chosen = NICE_STEPS_MS[NICE_STEPS_MS.length - 1]!
  for (const step of NICE_STEPS_MS) {
    if (step.ms >= targetStepMs * 0.85) {
      chosen = step
      break
    }
  }

  let ticks: Tick[]
  if (chosen.kind === 'year' || chosen.ms >= UNIT_MS.year) {
    const years = Math.max(1, Math.round(chosen.ms / UNIT_MS.year))
    ticks = calendarTicks(vp, 'year', years)
  } else if (chosen.kind === 'quarter' || chosen.ms === 3 * UNIT_MS.month) {
    ticks = calendarTicks(vp, 'quarter', 1)
  } else if (chosen.kind === 'month' || chosen.ms === UNIT_MS.month) {
    ticks = calendarTicks(vp, 'month', 1)
  } else {
    const stepMs = chosen.ms
    const start = Math.ceil(vp.fromMs / stepMs) * stepMs
    ticks = []
    let i = 0
    for (let t = start; t <= vp.toMs + 1; t += stepMs) {
      if (t < vp.fromMs - 1) {
        i++
        continue
      }
      if (t > vp.toMs + stepMs * 0.01) break
      ticks.push({
        timeMs: t,
        label: labelFor(t, chosen.kind),
        major: i % chosen.majorEvery === 0,
      })
      i++
      if (ticks.length > 200) break
    }
  }

  return thinTicks(ticks, vp, width)
}

/** Drop ticks until median spacing is at least ~80px. */
function thinTicks(ticks: Tick[], vp: Viewport, width: number): Tick[] {
  if (ticks.length < 2 || width <= 0) return ticks
  const spacing =
    xForTime(ticks[1]!.timeMs, vp, width) - xForTime(ticks[0]!.timeMs, vp, width)
  if (spacing >= 80) return ticks
  const keepEvery = Math.max(1, Math.ceil(90 / Math.max(spacing, 1)))
  const thinned: Tick[] = []
  ticks.forEach((tick, i) => {
    if (i % keepEvery === 0) {
      thinned.push({ ...tick, major: true })
    }
  })
  return thinned.length >= 2 ? thinned : ticks
}

function calendarTicks(
  vp: Viewport,
  kind: 'year' | 'quarter' | 'month',
  stepUnits: number,
): Tick[] {
  const ticks: Tick[] = []
  const d = new Date(vp.fromMs)
  d.setUTCMilliseconds(0)
  d.setUTCSeconds(0)
  d.setUTCMinutes(0)
  d.setUTCHours(0)

  if (kind === 'year') {
    d.setUTCMonth(0, 1)
    if (d.getTime() < vp.fromMs) {
      d.setUTCFullYear(d.getUTCFullYear() + 1)
    }
    const y0 = d.getUTCFullYear()
    const aligned = Math.ceil(y0 / stepUnits) * stepUnits
    d.setUTCFullYear(aligned)
    let idx = 0
    while (d.getTime() <= vp.toMs + 1 && ticks.length < 200) {
      const t = d.getTime()
      if (t >= vp.fromMs - 1) {
        ticks.push({
          timeMs: t,
          label: labelFor(t, 'year'),
          major: idx % 5 === 0 || stepUnits >= 5,
        })
        idx++
      }
      d.setUTCFullYear(d.getUTCFullYear() + stepUnits)
    }
  } else if (kind === 'quarter') {
    const m = d.getUTCMonth()
    d.setUTCMonth(m - (m % 3), 1)
    if (d.getTime() < vp.fromMs) {
      d.setUTCMonth(d.getUTCMonth() + 3)
    }
    let idx = 0
    while (d.getTime() <= vp.toMs + 1 && ticks.length < 200) {
      const t = d.getTime()
      if (t >= vp.fromMs - 1) {
        ticks.push({
          timeMs: t,
          label: labelFor(t, 'quarter'),
          major: idx % 4 === 0,
        })
        idx++
      }
      d.setUTCMonth(d.getUTCMonth() + 3)
    }
  } else {
    d.setUTCDate(1)
    if (d.getTime() < vp.fromMs) {
      d.setUTCMonth(d.getUTCMonth() + 1)
    }
    while (d.getTime() <= vp.toMs + 1 && ticks.length < 200) {
      const t = d.getTime()
      if (t >= vp.fromMs - 1) {
        ticks.push({
          timeMs: t,
          label: labelFor(t, 'month'),
          major: d.getUTCMonth() % 3 === 0,
        })
      }
      d.setUTCMonth(d.getUTCMonth() + 1)
    }
  }
  return ticks
}

/** Format a UTC ms timestamp for toolbar / aria (e.g. "Jan 2014"). */
export function formatPeriodLabel(ms: number): string {
  const d = new Date(ms)
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

export function formatPeriodRange(vp: Viewport): string {
  return `${formatPeriodLabel(vp.fromMs)} – ${formatPeriodLabel(vp.toMs)}`
}

export function viewportToIso(vp: Viewport): { from: string; to: string } {
  return {
    from: new Date(vp.fromMs).toISOString(),
    to: new Date(vp.toMs).toISOString(),
  }
}

export function isoToMs(iso: string): number {
  return Date.parse(iso)
}

export function parseUnit(unit: string): Unit {
  if (unit in UNIT_MS) return unit as Unit
  return 'month'
}
