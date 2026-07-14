import { describe, expect, it } from 'vitest'

import {
  canvasHeightForLanes,
  AXIS_H,
  LANE_GAP,
  LANE_H,
  MARKS_LANE_H,
  MULTIROW_HEADER_H,
  MULTIROW_ROW_H,
  eventPositionMs,
  layoutLanes,
  laneAtY,
  originGlyph,
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

    const marks = specsForKeys(['events'])
    expect(canvasHeightForLanes(marks)).toBe(AXIS_H + LANE_GAP + MARKS_LANE_H)
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

  it('events lane is marks kind and hit-tests as events', () => {
    const specs = specsForKeys(['events'])
    expect(specs[0]?.kind).toBe('marks')
    const layout = layoutLanes(specs, {
      events: {
        events: [
          {
            event_id: 'e1',
            title: 'T',
            time_start: '2015-06-01T00:00:00Z',
            time_end: null,
            time_precision: 'day',
            origin: 'analyst',
            event_type: 'meeting',
            status: 'confirmed',
            evidence_strength: null,
          },
        ],
        truncated: false,
      },
    })
    expect(laneAtY(AXIS_H + LANE_GAP + 10, layout)).toBe('events')
  })

  it('topics lane is multirow with topic-purple hit keys', () => {
    const multi = specsForKeys(['topics'])
    expect(multi[0]?.kind).toBe('multirow')
    expect(multi[0]?.key).toBe('topics')
    const hMulti = canvasHeightForLanes(multi, {
      topics: {
        topics: [
          {
            topic_id: 't1',
            label: 'House',
            origin: 'automatic',
            buckets: [],
          },
          {
            topic_id: 't2',
            label: 'Travel',
            origin: 'curated',
            buckets: [],
          },
        ],
      },
    })
    expect(hMulti).toBe(
      AXIS_H + LANE_GAP + MULTIROW_HEADER_H + 2 * MULTIROW_ROW_H,
    )
    const layout = layoutLanes(multi, {
      topics: {
        topics: [
          {
            topic_id: 't1',
            label: 'House',
            origin: 'automatic',
            buckets: [],
          },
          {
            topic_id: 't2',
            label: 'Travel',
            origin: 'curated',
            buckets: [],
          },
        ],
      },
    })
    const multiTop = layout[0]!.top + MULTIROW_HEADER_H + 1
    expect(laneAtY(multiTop, layout)).toBe('topics:t1')
    expect(laneAtY(multiTop + MULTIROW_ROW_H, layout)).toBe('topics:t2')
  })
})

describe('eventPositionMs day-floor', () => {
  it('floors date-only precision to UTC start of day', () => {
    const iso = '2015-06-15T14:30:00.000Z'
    const day = eventPositionMs(iso, 'day')
    expect(day).toBe(Date.UTC(2015, 5, 15))
    expect(eventPositionMs(iso, 'month')).toBe(Date.UTC(2015, 5, 15))
    expect(eventPositionMs(iso, 'year')).toBe(Date.UTC(2015, 5, 15))
    // Hour keeps full timestamp — no fabricated floor
    expect(eventPositionMs(iso, 'hour')).toBe(Date.parse(iso))
  })

  it('origin glyphs are text (not color-only)', () => {
    expect(originGlyph('analyst')).toBe('A')
    expect(originGlyph('automatic')).toBe('⚙')
    expect(originGlyph('source')).toBe('S')
    expect(originGlyph('imported')).toBe('I')
  })
})

