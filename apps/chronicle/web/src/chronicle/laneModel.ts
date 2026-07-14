import type { BucketPoint, LaneData, TopPeopleLane } from '../api/types'
import { isBucketSeries, isTopPeopleLane } from '../api/types'
import type { LaneKey } from '../workingset/urlState'

export type LaneKind = 'bars' | 'multirow'

export interface LaneSpec {
  key: LaneKey
  label: string
  kind: LaneKind
}

/** Catalog of available lanes for config panel + canvas. */
export const LANE_CATALOG: readonly LaneSpec[] = [
  { key: 'messages', label: 'Messages', kind: 'bars' },
  { key: 'attachments', label: 'Attachments', kind: 'bars' },
  { key: 'people', label: 'People (distinct)', kind: 'bars' },
  { key: 'top_people', label: 'Top people', kind: 'multirow' },
] as const

const CATALOG_BY_KEY = new Map(LANE_CATALOG.map((s) => [s.key, s]))

export const BAR_LANE_COLORS: Record<string, string> = {
  messages: '#5aa7ff', // action blue
  attachments: '#55c2a3', // green
  people: '#56d4dd', // people cyan
}

export const PEOPLE_CYAN = '#56d4dd'

export const AXIS_H = 28
export const LANE_H = 72
export const LANE_LABEL_W = 72
export const LANE_GAP = 4
export const MULTIROW_HEADER_H = 16
export const MULTIROW_ROW_H = 18

/** Build ordered LaneSpec[] from store lane keys (unknown keys dropped). */
export function specsForKeys(keys: readonly string[]): LaneSpec[] {
  const out: LaneSpec[] = []
  for (const key of keys) {
    const spec = CATALOG_BY_KEY.get(key as LaneKey)
    if (spec) out.push(spec)
  }
  return out
}

export function multirowHeight(contactCount: number): number {
  return MULTIROW_HEADER_H + MULTIROW_ROW_H * Math.max(0, contactCount)
}

export function laneContentHeight(
  spec: LaneSpec,
  laneData: Record<string, LaneData> | undefined,
): number {
  if (spec.kind === 'bars') return LANE_H
  const data = laneData?.[spec.key]
  const n = isTopPeopleLane(data) ? data.contacts.length : 0
  return multirowHeight(n)
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
  /** Hit-test id: lane key, or `top_people:<contact_id>` for multirow rows. */
  hitKey: string
  /** Y of the top of this hit region (local canvas coords). */
  top: number
  /** Height of the hit region. */
  height: number
  /** Lane key for data lookup. */
  laneKey: string
  kind: LaneKind
  /** Contact id when multirow row. */
  contactId?: string
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
    if (spec.kind === 'bars') {
      rows.push({
        hitKey: spec.key,
        top: y,
        height,
        laneKey: spec.key,
        kind: 'bars',
      })
    } else {
      const data = laneData?.[spec.key]
      const contacts = isTopPeopleLane(data) ? data.contacts : []
      contacts.forEach((c, i) => {
        rows.push({
          hitKey: `top_people:${c.contact_id}`,
          top: y + MULTIROW_HEADER_H + i * MULTIROW_ROW_H,
          height: MULTIROW_ROW_H,
          laneKey: spec.key,
          kind: 'multirow',
          contactId: c.contact_id,
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
