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

/** Inspector / timeline selection (URL param `sel`). */
export type Selection =
  | { kind: 'bucket'; bucketIso: string; lane: string }
  | { kind: 'message'; sid: string }
  | null

/** Serializable working-set slice for the URL codec. */
export interface UrlWorkingState {
  scope: QueryScope
  viewport: Viewport | null
  aggregation: Aggregation
  view: ViewMode
  selection: Selection
}

export const DEFAULT_URL_STATE: UrlWorkingState = {
  scope: {},
  viewport: null,
  aggregation: 'auto',
  view: 'canvas',
  selection: null,
}

/**
 * Encode selection for the `sel` URL param.
 * - bucket: `b:<lane>:<bucketIso>`
 * - message: `m:<sid>`
 */
export function encodeSelection(selection: Selection): string | null {
  if (!selection) return null
  if (selection.kind === 'bucket') {
    if (!selection.lane || !selection.bucketIso) return null
    return `b:${selection.lane}:${selection.bucketIso}`
  }
  if (selection.kind === 'message') {
    if (!selection.sid) return null
    return `m:${selection.sid}`
  }
  return null
}

/**
 * Decode `sel` param. Total: bad values → null, never throws.
 */
export function decodeSelection(raw: string | null): Selection {
  if (!raw) return null
  if (raw.startsWith('b:')) {
    // b:<lane>:<bucketIso> — lane has no colons; bucketIso may contain colons (ISO).
    const rest = raw.slice(2)
    const colon = rest.indexOf(':')
    if (colon <= 0) return null
    const lane = rest.slice(0, colon)
    const bucketIso = rest.slice(colon + 1)
    if (!lane || !bucketIso) return null
    const ms = Date.parse(bucketIso)
    if (!Number.isFinite(ms)) return null
    return { kind: 'bucket', lane, bucketIso }
  }
  if (raw.startsWith('m:')) {
    const sid = raw.slice(2)
    if (!sid || !/^(msg|att)_[A-Za-z0-9_-]+$/.test(sid)) return null
    return { kind: 'message', sid }
  }
  return null
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
  const { scope, viewport, aggregation, view, selection } = state

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

  const sel = encodeSelection(selection ?? null)
  if (sel) params.set('sel', sel)

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
    selection: decodeSelection(params.get('sel')),
  }
}

/** True when scope has no date/mailbox/sender constraints. */
export function isScopePristine(scope: QueryScope): boolean {
  const hasDate = !!(scope.date?.from || scope.date?.to)
  const hasMb = (scope.mailboxes?.length ?? 0) > 0
  const hasSd = (scope.senders?.length ?? 0) > 0
  return !hasDate && !hasMb && !hasSd
}
