import { describe, expect, it } from 'vitest'

import {
  TOKEN_PAIRS,
  contrastRatio,
  parseHex,
  relativeLuminance,
} from './contrast'

describe('contrastRatio (WCAG)', () => {
  it('parses hex', () => {
    expect(parseHex('#fff')).toEqual([255, 255, 255])
    expect(parseHex('#000000')).toEqual([0, 0, 0])
    expect(parseHex('#e6edf3')).toEqual([230, 237, 243])
  })

  it('black vs white is 21:1', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5)
    expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 5)
  })

  it('identical colors are 1:1', () => {
    expect(contrastRatio('#151b23', '#151b23')).toBeCloseTo(1, 5)
  })

  it('known mid pair matches published WCAG examples approximately', () => {
    // white on #777 → ratio ≈ 4.48 (near AA threshold)
    const r = contrastRatio('#ffffff', '#777777')
    expect(r).toBeGreaterThan(4.4)
    expect(r).toBeLessThan(4.6)
  })

  it('relative luminance of black is 0 and white is 1', () => {
    expect(relativeLuminance('#000000')).toBeCloseTo(0, 5)
    expect(relativeLuminance('#ffffff')).toBeCloseTo(1, 5)
  })
})

describe('§13.1 token contrast verification', () => {
  const recorded: { name: string; ratio: number; min: number; ok: boolean }[] = []

  for (const pair of TOKEN_PAIRS) {
    const min = pair.role === 'text' ? 4.5 : 3
    it(`${pair.name} ≥ ${min}:1${pair.expectedFail ? ' (expected-fail)' : ''}`, () => {
      const ratio = contrastRatio(pair.fg, pair.bg)
      const ok = ratio >= min
      recorded.push({ name: pair.name, ratio, min, ok })

      if (pair.expectedFail) {
        // Documented failure for the 5.5 report — do not hide the ratio
        expect(
          ok,
          `expected-fail pair ${pair.name}: actual ratio ${ratio.toFixed(3)} (min ${min})`,
        ).toBe(false)
        // Still surface the measurement
        expect(ratio).toBeGreaterThan(0)
        return
      }

      expect(
        ok,
        `contrast ${pair.name}: ${ratio.toFixed(3)}:1 < ${min}:1 — ` +
          `fg=${pair.fg} bg=${pair.bg}. If this fails, mark expectedFail and record for 5.5 report (do not change tokens).`,
      ).toBe(true)
    })
  }

  it('records ratios for all pairs (summary)', () => {
    // Recompute so summary is independent of test order
    const lines = TOKEN_PAIRS.map((pair) => {
      const min = pair.role === 'text' ? 4.5 : 3
      const ratio = contrastRatio(pair.fg, pair.bg)
      const ok = ratio >= min
      const tag = pair.expectedFail
        ? 'expected-fail'
        : ok
          ? 'pass'
          : 'FAIL'
      return `${pair.name}: ${ratio.toFixed(3)}:1 (min ${min}) [${tag}]`
    })
    // eslint-disable-next-line no-console
    console.info('Token contrast report:\n' + lines.join('\n'))
    expect(lines.length).toBe(TOKEN_PAIRS.length)
  })
})
