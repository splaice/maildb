import { describe, expect, it } from 'vitest'

import {
  canvasHeightForLanes,
  AXIS_H,
  LANE_GAP,
  LANE_H,
  MULTIROW_HEADER_H,
  MULTIROW_ROW_H,
  layoutLanes,
  laneAtY,
  specsForKeys,
} from './laneModel'

describe('laneModel', () => {
  it('canvas height responds to lane config and multirow rows', () => {
    const barsOnly = specsForKeys(['messages', 'attachments'])
    const h2 = canvasHeightForLanes(barsOnly)
    expect(h2).toBe(AXIS_H + 2 * (LANE_GAP + LANE_H))

    const withPeople = specsForKeys(['messages', 'attachments', 'people'])
    expect(canvasHeightForLanes(withPeople)).toBe(AXIS_H + 3 * (LANE_GAP + LANE_H))

    const multi = specsForKeys(['top_people'])
    const hMulti = canvasHeightForLanes(multi, {
      top_people: {
        contacts: [
          { contact_id: 'a', display_name: 'A', buckets: [] },
          { contact_id: 'b', display_name: 'B', buckets: [] },
          { contact_id: 'c', display_name: 'C', buckets: [] },
        ],
      },
    })
    expect(hMulti).toBe(
      AXIS_H + LANE_GAP + MULTIROW_HEADER_H + 3 * MULTIROW_ROW_H,
    )
  })

  it('layout hit-test maps multirow rows to top_people:contact_id', () => {
    const specs = specsForKeys(['messages', 'top_people'])
    const layout = layoutLanes(specs, {
      messages: [],
      top_people: {
        contacts: [
          { contact_id: 'c1', display_name: 'A', buckets: [] },
          { contact_id: 'c2', display_name: 'B', buckets: [] },
        ],
      },
    })
    expect(laneAtY(10, layout)).toBeNull()
    expect(laneAtY(AXIS_H + LANE_GAP + 10, layout)).toBe('messages')
    const multiTop = layout[1]!.top + MULTIROW_HEADER_H + 1
    expect(laneAtY(multiTop, layout)).toBe('top_people:c1')
    expect(laneAtY(multiTop + MULTIROW_ROW_H, layout)).toBe('top_people:c2')
  })
})
