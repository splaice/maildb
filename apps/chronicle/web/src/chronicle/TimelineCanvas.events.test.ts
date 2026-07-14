import { describe, expect, it } from 'vitest'

import { eventAtX } from './TimelineCanvas'
import type { EventLaneMark } from '../api/types'

const vp = {
  fromMs: Date.UTC(2015, 0, 1),
  toMs: Date.UTC(2016, 0, 1),
}
const plotW = 365

const marks: EventLaneMark[] = [
  {
    event_id: 'e-day',
    title: 'Day event',
    time_start: '2015-06-15T14:30:00.000Z',
    time_end: null,
    time_precision: 'day',
    origin: 'analyst',
    event_type: 'meeting',
    status: 'confirmed',
    evidence_strength: null,
  },
  {
    event_id: 'e-dismissed',
    title: 'Hidden',
    time_start: '2015-06-15T00:00:00.000Z',
    time_end: null,
    time_precision: 'day',
    origin: 'automatic',
    event_type: 'communication',
    status: 'dismissed',
    evidence_strength: null,
  },
]

describe('eventAtX', () => {
  it('hits diamond at day-floored position, ignores dismissed', () => {
    // Day-floor of 2015-06-15T14:30 is midnight UTC
    const t = Date.UTC(2015, 5, 15)
    const x = ((t - vp.fromMs) / (vp.toMs - vp.fromMs)) * plotW
    expect(eventAtX(x, vp, plotW, marks)).toBe('e-day')
    // Far from mark
    expect(eventAtX(0, vp, plotW, marks)).toBeNull()
  })
})
