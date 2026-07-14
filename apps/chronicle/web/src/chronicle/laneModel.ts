import type {
  BucketPoint,
  EventLaneMark,
  LaneData,
  TopicSeries,
  TopPeopleContact,
  TopPeopleLane,
  TopicsLane,
} from '../api/types'
import {
  isBucketSeries,
  isEventsLane,
  isTopPeopleLane,
  isTopicsLane,
} from '../api/types'
import type { LaneKey } from '../workingset/urlState'

export type LaneKind = 'bars' | 'multirow' | 'marks'

/** Catalog keys include `topics` even when URL codec lags (server-first lane). */
export type ChronicleLaneKey = LaneKey | 'topics'

export interface LaneSpec {
  key: ChronicleLaneKey
  label: string
  kind: LaneKind
}

/** Catalog of available lanes for config panel + canvas. */
export const LANE_CATALOG: readonly LaneSpec[] = [
  { key: 'messages', label: 'Messages', kind: 'bars' },
  { key: 'attachments', label: 'Attachments', kind: 'bars' },
  { key: 'people', label: 'People (distinct)', kind: 'bars' },
  { key: 'top_people', label: 'Top people', kind: 'multirow' },
  { key: 'topics', label: 'Topics', kind: 'multirow' },
  { key: 'events', label: 'Events', kind: 'marks' },
] as const

const CATALOG_BY_KEY = new Map(LANE_CATALOG.map((s) => [s.key, s]))

export const BAR_LANE_COLORS: Record<string, string> = {
  messages: '#5aa7ff', // action blue
  attachments: '#55c2a3', // green
  people: '#56d4dd', // people cyan
}

/** Spec event amber (#E0A84A) for diamond marks. */
export const EVENT_AMBER = '#E0A84A'

export const PEOPLE_CYAN = '#56d4dd'

/** Spec topic purple (#A78BFA) for topics multirow marks. */
export const TOPIC_PURPLE = '#A78BFA'

export const AXIS_H = 28
export const LANE_H = 72
export const LANE_LABEL_W = 72
export const LANE_GAP = 4
export const MULTIROW_HEADER_H = 16
export const MULTIROW_ROW_H = 18
/** Marks lane height (events diamonds). */
export const MARKS_LANE_H = 56

/** Build ordered LaneSpec[] from store lane keys (unknown keys dropped). */
export function specsForKeys(keys: readonly string[]): LaneSpec[] {
  const out: LaneSpec[] = []
  for (const key of keys) {
    const spec = CATALOG_BY_KEY.get(key as ChronicleLaneKey)
    if (spec) out.push(spec)
  }
  return out
}

export function multirowHeight(rowCount: number): number {
  return MULTIROW_HEADER_H + MULTIROW_ROW_H * Math.max(0, rowCount)
}

/** Multirow series rows for top_people or topics (shared canvas/table shape). */
export interface MultirowSeries {
  id: string
  label: string
  buckets: BucketPoint[]
}

export function multirowSeriesForLane(
  spec: LaneSpec,
  laneData: Record<string, LaneData> | undefined,
): MultirowSeries[] {
  const data = laneData?.[spec.key]
  if (spec.key === 'topics' && isTopicsLane(data)) {
    return data.topics.map((t) => ({
      id: t.topic_id,
      label: t.label,
      buckets: t.buckets,
    }))
  }
  if (isTopPeopleLane(data)) {
    return data.contacts.map((c) => ({
      id: c.contact_id,
      label: c.display_name,
      buckets: c.buckets,
    }))
  }
  return []
}

export function multirowHitPrefix(laneKey: string): string {
  return laneKey === 'topics' ? 'topics' : 'top_people'
}

export function multirowMarkColor(laneKey: string): string {
  return laneKey === 'topics' ? TOPIC_PURPLE : PEOPLE_CYAN
}

export function laneContentHeight(
  spec: LaneSpec,
  laneData: Record<string, LaneData> | undefined,
): number {
  if (spec.kind === 'bars') return LANE_H
  if (spec.kind === 'marks') return MARKS_LANE_H
  return multirowHeight(multirowSeriesForLane(spec, laneData).length)
}

/** Total canvas CSS height for the given lane set and data. */
export function canvasHeightForLanes(
  specs: readonly LaneSpec[],
  laneData?: Record<string, LaneData>,
): number {
  let h = AXIS_H
  for (const spec of specs) {
    h += LANE_GAP + laneContentHeight(spec, laneData)
  }
  return h
}

export interface LaneLayoutRow {
  /** Hit-test id: lane key, or `top_people:<id>` / `topics:<id>` for multirow. */
  hitKey: string
  /** Y of the top of this hit region (local canvas coords). */
  top: number
  /** Height of the hit region. */
  height: number
  /** Lane key for data lookup. */
  laneKey: string
  kind: LaneKind
  /** Contact id when top_people multirow row. */
  contactId?: string
  /** Topic id when topics multirow row. */
  topicId?: string
}

export interface LaneLayoutBlock {
  spec: LaneSpec
  top: number
  height: number
  rows: LaneLayoutRow[]
}

/** Compute vertical layout for hit-testing and drawing. */
export function layoutLanes(
  specs: readonly LaneSpec[],
  laneData?: Record<string, LaneData>,
): LaneLayoutBlock[] {
  const blocks: LaneLayoutBlock[] = []
  let y = AXIS_H
  for (const spec of specs) {
    y += LANE_GAP
    const height = laneContentHeight(spec, laneData)
    const rows: LaneLayoutRow[] = []
    if (spec.kind === 'bars' || spec.kind === 'marks') {
      rows.push({
        hitKey: spec.key,
        top: y,
        height,
        laneKey: spec.key,
        kind: spec.kind,
      })
    } else {
      const series = multirowSeriesForLane(spec, laneData)
      const prefix = multirowHitPrefix(spec.key)
      series.forEach((row, i) => {
        rows.push({
          hitKey: `${prefix}:${row.id}`,
          top: y + MULTIROW_HEADER_H + i * MULTIROW_ROW_H,
          height: MULTIROW_ROW_H,
          laneKey: spec.key,
          kind: 'multirow',
          contactId: prefix === 'top_people' ? row.id : undefined,
          topicId: prefix === 'topics' ? row.id : undefined,
        })
      })
    }
    blocks.push({ spec, top: y, height, rows })
    y += height
  }
  return blocks
}

/** Which hit key contains localY, or null (axis / gap). */
export function laneAtY(
  localY: number,
  layout: readonly LaneLayoutBlock[],
): string | null {
  if (localY <= AXIS_H) return null
  for (const block of layout) {
    for (const row of block.rows) {
      if (localY >= row.top && localY < row.top + row.height) {
        return row.hitKey
      }
    }
  }
  return null
}

export function barsPoints(
  laneData: Record<string, LaneData> | undefined,
  key: string,
): BucketPoint[] {
  const data = laneData?.[key]
  return isBucketSeries(data) ? data : []
}

export function topPeopleData(
  laneData: Record<string, LaneData> | undefined,
): TopPeopleLane | undefined {
  const data = laneData?.top_people
  return isTopPeopleLane(data) ? data : undefined
}

export function topicsData(
  laneData: Record<string, LaneData> | undefined,
): TopicsLane | undefined {
  const data = laneData?.topics
  return isTopicsLane(data) ? data : undefined
}

export function eventsMarks(
  laneData: Record<string, LaneData> | undefined,
): EventLaneMark[] {
  const data = laneData?.events
  return isEventsLane(data) ? data.events : []
}

/** Resolve multirow series by hit key (`top_people:…` or `topics:…`). */
export function multirowBucketsForHit(
  hitKey: string,
  laneData: Record<string, LaneData> | undefined,
): BucketPoint[] {
  if (hitKey.startsWith('topics:')) {
    const topicId = hitKey.slice('topics:'.length)
    const topics = topicsData(laneData)
    const row = topics?.topics.find((t: TopicSeries) => t.topic_id === topicId)
    return row?.buckets ?? []
  }
  if (hitKey.startsWith('top_people:')) {
    const contactId = hitKey.slice('top_people:'.length)
    const tp = topPeopleData(laneData)
    const row = tp?.contacts.find((c: TopPeopleContact) => c.contact_id === contactId)
    return row?.buckets ?? []
  }
  return []
}

/**
 * Origin glyph (text, not color-only) for event marks.
 * A=analyst, ⚙=automatic, S=source, I=imported.
 */
export function originGlyph(origin: string): string {
  switch (origin) {
    case 'analyst':
      return 'A'
    case 'automatic':
      return '⚙'
    case 'source':
      return 'S'
    case 'imported':
      return 'I'
    default:
      return '?'
  }
}

/**
 * Epoch ms used for x-position of an event mark.
 *
 * Date-only precisions (year, quarter, month, week, day) floor to UTC start of
 * day — never a fabricated hour within the day. Hour precision uses the
 * full timestamp.
 */
export function eventPositionMs(
  timeStartIso: string,
  timePrecision: string,
): number | null {
  const ms = Date.parse(timeStartIso)
  if (!Number.isFinite(ms)) return null
  if (timePrecision === 'hour') return ms
  // Floor to UTC midnight of the calendar day.
  const d = new Date(ms)
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
}
