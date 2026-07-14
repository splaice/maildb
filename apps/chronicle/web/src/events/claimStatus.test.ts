import { describe, expect, it } from 'vitest'

import { claimStatusText, claimStatusVisual } from './claimStatus'

describe('claimStatus', () => {
  it('codes all four statuses with symbol + text label', () => {
    expect(claimStatusText('direct')).toBe('✓ direct')
    expect(claimStatusText('supported')).toBe('~ supported')
    expect(claimStatusText('conflicting')).toBe('✕ conflicting')
    expect(claimStatusText('unresolved')).toBe('? unresolved')

    expect(claimStatusVisual('direct').className).toContain('attachment')
    expect(claimStatusVisual('supported').className).toContain('action')
    expect(claimStatusVisual('conflicting').className).toContain('conflict')
    expect(claimStatusVisual('unresolved').className).toContain('muted')
  })
})
