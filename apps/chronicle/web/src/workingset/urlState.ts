import type { QueryScope } from '../api/types'
import type { Unit, Viewport } from '../chronicle/timeScale'

/** Aggregation values accepted in the URL `agg` param (omit = auto). */
const UNITS: ReadonlySet<string> = new Set([
  'hour',
  'day',
  'week',
  'month',
  'quarter',
  'year',
])

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/

export type Aggregation = 'auto' | Unit
export type ViewMode = 'canvas' | 'table'

/** Serializable working-set slice for the URL codec. */
export interface UrlWorkingState {
  scope: QueryScope
  viewport: Viewport | null
  aggregation: Aggregation
  view: ViewMode
}

export const DEFAULT_URL_STATE: UrlWorkingState = {
  scope: {},
  viewport: null,
  aggregation: 'auto',
  view: 'canvas',
}

/** Format epoch ms as UTC ISO datetime with second precision (`…Z`, no ms). */
export function toIsoSeconds(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z')
}

function parseIsoDate(value: string | null): string | null {
  if (!value || !ISO_DATE_RE.test(value)) return null
  // Reject obviously invalid calendar values via Date.parse of midnight UTC.
  const ms = Date.parse(`${value}T00:00:00Z`)
  if (!Number.isFinite(ms)) return null
  return value
}

function parseViewportIso(value: string | null): number | null {
  if (!value) return null
  const ms = Date.parse(value)
  if (!Number.isFinite(ms)) return null
  return ms
}

function parseCsv(value: string | null): string[] {
  if (!value) return []
  return value
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

function parseAggregation(value: string | null): Aggregation {
  if (!value || value === 'auto') return 'auto'
  if (UNITS.has(value)) return value as Unit
  return 'auto'
}

function parseView(value: string | null): ViewMode {
  if (value === 'table') return 'table'
  return 'canvas'
}

/**
 * Encode working-set state into short, human-readable search params.
 * Omits defaults and empties entirely — pristine state → empty query string.
 */
export function encodeState(state: UrlWorkingState): URLSearchParams {
  const params = new URLSearchParams()
  const { scope, viewport, aggregation, view } = state

  const dateFrom = scope.date?.from ?? null
  const dateTo = scope.date?.to ?? null
  if (dateFrom) params.set('df', dateFrom)
  if (dateTo) params.set('dt', dateTo)

  const mailboxes = scope.mailboxes ?? []
  if (mailboxes.length > 0) params.set('mb', mailboxes.join(','))

  const senders = scope.senders ?? []
  if (senders.length > 0) params.set('sd', senders.join(','))

  if (viewport) {
    params.set('vf', toIsoSeconds(viewport.fromMs))
    params.set('vt', toIsoSeconds(viewport.toMs))
  }

  if (aggregation !== 'auto') params.set('agg', aggregation)
  if (view !== 'canvas') params.set('view', view)

  return params
}

/**
 * Decode search params into working-set state.
 * Total function: bad values fall back to defaults, never throws.
 */
export function decodeState(params: URLSearchParams): UrlWorkingState {
  const df = parseIsoDate(params.get('df'))
  const dt = parseIsoDate(params.get('dt'))
  const mailboxes = parseCsv(params.get('mb'))
  const senders = parseCsv(params.get('sd'))

  const scope: QueryScope = {}
  if (df || dt) {
    scope.date = {
      ...(df ? { from: df } : {}),
      ...(dt ? { to: dt } : {}),
    }
  }
  if (mailboxes.length > 0) scope.mailboxes = mailboxes
  if (senders.length > 0) scope.senders = senders

  const vf = parseViewportIso(params.get('vf'))
  const vt = parseViewportIso(params.get('vt'))
  let viewport: Viewport | null = null
  if (vf != null && vt != null && vt > vf) {
    viewport = { fromMs: vf, toMs: vt }
  }

  return {
    scope,
    viewport,
    aggregation: parseAggregation(params.get('agg')),
    view: parseView(params.get('view')),
  }
}

/** True when scope has no date/mailbox/sender constraints. */
export function isScopePristine(scope: QueryScope): boolean {
  const hasDate = !!(scope.date?.from || scope.date?.to)
  const hasMb = (scope.mailboxes?.length ?? 0) > 0
  const hasSd = (scope.senders?.length ?? 0) > 0
  return !hasDate && !hasMb && !hasSd
}
