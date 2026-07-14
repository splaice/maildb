import { describe, expect, it } from 'vitest'

import { formatDerivationLine } from './derivation'

describe('formatDerivationLine', () => {
  it('formats generation metadata', () => {
    const line = formatDerivationLine({
      generated_at: '2026-07-13T12:00:00Z',
      process_version: 'event-v1',
      model_route: 'local',
      scope_fingerprint: 'qs_abcdef0123456789',
    })
    expect(line).toContain('Generated 2026-07-13')
    expect(line).toContain('event-v1')
    expect(line).toContain('model route local')
    expect(line).toContain('scope qs_')
  })

  it('returns null for empty derivation', () => {
    expect(formatDerivationLine(null)).toBeNull()
    expect(formatDerivationLine(undefined)).toBeNull()
  })
})
