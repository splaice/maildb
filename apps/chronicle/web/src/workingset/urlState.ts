import type { QueryScope, SearchMode } from '../api/types'
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

/** All lane keys the UI understands (config panel + codec). */
export const ALL_LANE_KEYS = [
  'messages',
  'attachments',
  'people',
  'top_people',
  'events',
] as const

export type LaneKey = (typeof ALL_LANE_KEYS)[number]

/** Default visible lane order (store + URL fallback). */
export const DEFAULT_LANES: LaneKey[] = ['messages', 'attachments', 'top_people']

/** localStorage key for per-device saved lens (lane config). */
export const LANES_STORAGE_KEY = 'chronicle.lanes.v1'

const LANE_KEY_SET: ReadonlySet<string> = new Set(ALL_LANE_KEYS)

export type Aggregation = 'auto' | Unit
export type ViewMode = 'canvas' | 'table'

/** Files lens view mode (URL param `fv`). */
export type FilesViewMode = 'table' | 'gallery'

/** Research Desk grouping (URL param `grp`). */
export type ResearchGrouping = 'none' | 'thread' | 'year' | 'mailbox'

const RESEARCH_GROUPINGS: ReadonlySet<string> = new Set([
  'none',
  'thread',
  'year',
  'mailbox',
])

const SEARCH_MODES: ReadonlySet<string> = new Set(['hybrid', 'exact', 'semantic'])

/** Inspector / timeline / research selection (URL param `sel`). */
export type Selection =
  | { kind: 'bucket'; bucketIso: string; lane: string }
  | { kind: 'message'; sid: string }
  | { kind: 'attachment'; sid: string }
  | { kind: 'event'; eventId: string }
  | null

/** Serializable working-set slice for the URL codec. */
export interface UrlWorkingState {
  scope: QueryScope
  viewport: Viewport | null
  aggregation: Aggregation
  view: ViewMode
  selection: Selection
  /** Ordered visible lane keys; null means "not present in URL". */
  lanes: string[] | null
  /**
   * Focus-mode period (URL params `ff`/`ft`). Optional on encode input so
   * older call sites remain valid; decode always returns Viewport | null.
   */
  focus?: Viewport | null
  /**
   * Compare mode ranges (URL params `ca`/`cb` as `from..to` ISO pairs).
   * Optional on encode so older call sites remain valid; decode always
   * returns `{ a, b } | null`.
   */
  compare?: { a: Viewport; b: Viewport } | null
  /** Research query text (URL param `q`). */
  query?: string
  /** Research retrieval mode (URL param `mode`); default hybrid. */
  mode?: SearchMode
  /** Research result grouping (URL param `grp`); default none. */
  grouping?: ResearchGrouping
  /**
   * Files lens table|gallery (URL param `fv`). Optional so store-driven
   * encodes that omit it preserve the current location value.
   */
  filesView?: FilesViewMode
  /** Files lens filename query (URL param `fq`). */
  filesQuery?: string
}

export const DEFAULT_URL_STATE: UrlWorkingState = {
  scope: {},
  viewport: null,
  aggregation: 'auto',
  view: 'canvas',
  selection: null,
  lanes: null,
  focus: null,
  compare: null,
  query: '',
  mode: 'hybrid',
  grouping: 'none',
  filesView: 'table',
  filesQuery: '',
}

/**
 * Encode selection for the `sel` URL param.
 * - bucket: `b:<lane>:<bucketIso>`
 * - message: `m:<sid>`
 * - attachment: `a:<sid>`
 * - event: `e:<event_id>`
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
  if (selection.kind === 'attachment') {
    if (!selection.sid) return null
    return `a:${selection.sid}`
  }
  if (selection.kind === 'event') {
    if (!selection.eventId) return null
    return `e:${selection.eventId}`
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
    if (!sid || !/^msg_[A-Za-z0-9_-]+$/.test(sid)) return null
    return { kind: 'message', sid }
  }
  if (raw.startsWith('a:')) {
    const sid = raw.slice(2)
    if (!sid || !/^att_[A-Za-z0-9_-]+$/.test(sid)) return null
    return { kind: 'attachment', sid }
  }
  if (raw.startsWith('e:')) {
    const eventId = raw.slice(2)
    // UUID (with or without hyphens) or any non-empty opaque id.
    if (!eventId || !/^[A-Za-z0-9_-]+$/.test(eventId)) return null
    return { kind: 'event', eventId }
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

/**
 * Encode a compare range as `from..to` ISO pair (second precision).
 * Returns null when the range is invalid.
 */
export function encodeCompareRange(vp: Viewport): string | null {
  if (!(vp.toMs > vp.fromMs)) return null
  return `${toIsoSeconds(vp.fromMs)}..${toIsoSeconds(vp.toMs)}`
}

/**
 * Decode `ca`/`cb` param (`from..to` ISO). Total: bad values → null.
 */
export function decodeCompareRange(raw: string | null): Viewport | null {
  if (!raw) return null
  const sep = raw.indexOf('..')
  if (sep <= 0) return null
  const fromMs = parseViewportIso(raw.slice(0, sep))
  const toMs = parseViewportIso(raw.slice(sep + 2))
  if (fromMs == null || toMs == null || !(toMs > fromMs)) return null
  return { fromMs, toMs }
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
 * Parse `ln` CSV into ordered valid lane keys. Unknown tokens dropped.
 * Empty / all-invalid → null (caller applies localStorage / default).
 */
export function parseLanesParam(value: string | null): string[] | null {
  if (value == null || value === '') return null
  const seen = new Set<string>()
  const out: string[] = []
  for (const part of parseCsv(value)) {
    if (!LANE_KEY_SET.has(part) || seen.has(part)) continue
    seen.add(part)
    out.push(part)
  }
  return out.length > 0 ? out : null
}

/** Read saved lens from localStorage. Returns null if missing/invalid. */
export function loadSavedLanes(): string[] | null {
  try {
    if (typeof localStorage === 'undefined') return null
    return parseLanesParam(localStorage.getItem(LANES_STORAGE_KEY))
  } catch {
    return null
  }
}

/** Persist current lane config as per-device default (saved lens). */
export function saveLanesAsDefault(lanes: string[]): void {
  try {
    if (typeof localStorage === 'undefined') return
    const valid = parseLanesParam(lanes.join(','))
    if (!valid) return
    localStorage.setItem(LANES_STORAGE_KEY, valid.join(','))
  } catch {
    // Quota / private mode — ignore.
  }
}

/**
 * Resolve lanes with precedence: URL (`decoded`) > localStorage > default.
 * When `decoded` is null (no `ln` param), localStorage is consulted.
 */
export function resolveLanes(urlLanes: string[] | null): string[] {
  if (urlLanes != null && urlLanes.length > 0) return [...urlLanes]
  const saved = loadSavedLanes()
  if (saved != null && saved.length > 0) return [...saved]
  return [...DEFAULT_LANES]
}

function lanesEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  return a.every((v, i) => v === b[i])
}

/**
 * Encode working-set state into short, human-readable search params.
 * Omits defaults and empties entirely — pristine state → empty query string.
 */
export function encodeState(state: UrlWorkingState): URLSearchParams {
  const params = new URLSearchParams()
  const {
    scope,
    viewport,
    aggregation,
    view,
    selection,
    lanes,
    focus,
    query,
    mode,
    grouping,
  } = state

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

  // Encode ln when present and non-default (null = omit; empty array should not occur).
  if (lanes != null && lanes.length > 0 && !lanesEqual(lanes, DEFAULT_LANES)) {
    params.set('ln', lanes.join(','))
  }

  // Focus period (analytical): ff/ft ISO datetime, second precision.
  if (focus && focus.toMs > focus.fromMs) {
    params.set('ff', toIsoSeconds(focus.fromMs))
    params.set('ft', toIsoSeconds(focus.toMs))
  }

  // Compare ranges (analytical): ca/cb as from..to ISO pairs.
  // When omitted from state, preserve from current location (like filesView).
  let compare = state.compare
  if (typeof window !== 'undefined' && compare === undefined) {
    try {
      const current = new URLSearchParams(window.location.search)
      const a = decodeCompareRange(current.get('ca'))
      const b = decodeCompareRange(current.get('cb'))
      if (a && b) compare = { a, b }
      else compare = null
    } catch {
      compare = null
    }
  }
  if (compare && compare.a && compare.b) {
    const ca = encodeCompareRange(compare.a)
    const cb = encodeCompareRange(compare.b)
    if (ca && cb) {
      params.set('ca', ca)
      params.set('cb', cb)
    }
  }

  // Research Desk: q / mode / grp (omit defaults)
  const q = (query ?? '').trim()
  if (q) params.set('q', q)
  if (mode && mode !== 'hybrid') params.set('mode', mode)
  if (grouping && grouping !== 'none') params.set('grp', grouping)

  // Files lens: fv / fq. When omitted from state, preserve from current location
  // so store-driven URL rewrites (selection etc.) do not wipe files params.
  let filesView = state.filesView
  let filesQuery = state.filesQuery
  if (
    typeof window !== 'undefined' &&
    (filesView === undefined || filesQuery === undefined)
  ) {
    try {
      const current = new URLSearchParams(window.location.search)
      if (filesView === undefined) {
        filesView = current.get('fv') === 'gallery' ? 'gallery' : 'table'
      }
      if (filesQuery === undefined) {
        filesQuery = current.get('fq') ?? ''
      }
    } catch {
      /* ignore */
    }
  }
  if (filesView === 'gallery') params.set('fv', 'gallery')
  const fq = (filesQuery ?? '').trim()
  if (fq) params.set('fq', fq)

  return params
}

/**
 * Decode search params into working-set state.
 * Total function: bad values fall back to defaults, never throws.
 * `lanes` is null when `ln` is absent — hydrate applies localStorage / default.
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

  const ff = parseViewportIso(params.get('ff'))
  const ft = parseViewportIso(params.get('ft'))
  let focus: Viewport | null = null
  if (ff != null && ft != null && ft > ff) {
    focus = { fromMs: ff, toMs: ft }
  }

  const ca = decodeCompareRange(params.get('ca'))
  const cb = decodeCompareRange(params.get('cb'))
  const compare =
    ca && cb ? { a: ca, b: cb } : null

  const rawMode = params.get('mode')
  const mode: SearchMode =
    rawMode && SEARCH_MODES.has(rawMode) ? (rawMode as SearchMode) : 'hybrid'

  const rawGrp = params.get('grp')
  const grouping: ResearchGrouping =
    rawGrp && RESEARCH_GROUPINGS.has(rawGrp)
      ? (rawGrp as ResearchGrouping)
      : 'none'

  const filesView: FilesViewMode =
    params.get('fv') === 'gallery' ? 'gallery' : 'table'
  const filesQuery = params.get('fq') ?? ''

  return {
    scope,
    viewport,
    aggregation: parseAggregation(params.get('agg')),
    view: parseView(params.get('view')),
    selection: decodeSelection(params.get('sel')),
    lanes: parseLanesParam(params.get('ln')),
    focus,
    compare,
    query: params.get('q') ?? '',
    mode,
    grouping,
    filesView,
    filesQuery,
  }
}

/** True when scope has no date/mailbox/sender constraints. */
export function isScopePristine(scope: QueryScope): boolean {
  const hasDate = !!(scope.date?.from || scope.date?.to)
  const hasMb = (scope.mailboxes?.length ?? 0) > 0
  const hasSd = (scope.senders?.length ?? 0) > 0
  const hasRcpt = (scope.recipients?.length ?? 0) > 0
  const hasPart = (scope.participants?.length ?? 0) > 0
  const hasSubj = !!(scope.subject_contains && scope.subject_contains.length > 0)
  const hasAtt = scope.has_attachment != null
  const hasFt = (scope.file_types?.length ?? 0) > 0
  const hasFn = (scope.filenames?.length ?? 0) > 0
  const hasSt = (scope.source_types?.length ?? 0) > 0
  return (
    !hasDate &&
    !hasMb &&
    !hasSd &&
    !hasRcpt &&
    !hasPart &&
    !hasSubj &&
    !hasAtt &&
    !hasFt &&
    !hasFn &&
    !hasSt
  )
}
