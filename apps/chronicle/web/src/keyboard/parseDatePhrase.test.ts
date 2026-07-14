import { describe, expect, it } from 'vitest'

import { parseDatePhrase } from './parseDatePhrase'

describe('parseDatePhrase', () => {
  it('parses a single year', () => {
    const r = parseDatePhrase('2015')
    expect(r).not.toBeNull()
    expect(r!.fromMs).toBe(Date.UTC(2015, 0, 1))
    expect(r!.toMs).toBe(Date.UTC(2016, 0, 1))
    expect(r!.label).toBe('2015')
  })

  it('parses year ranges with .. and -', () => {
    const a = parseDatePhrase('2014..2018')
    expect(a).not.toBeNull()
    expect(a!.fromMs).toBe(Date.UTC(2014, 0, 1))
    expect(a!.toMs).toBe(Date.UTC(2019, 0, 1))
    expect(a!.label).toBe('2014–2018')

    const b = parseDatePhrase('2014-2018')
    expect(b).toEqual(a)
  })

  it('parses month-year phrases', () => {
    const r = parseDatePhrase('june 2015')
    expect(r).not.toBeNull()
    expect(r!.fromMs).toBe(Date.UTC(2015, 5, 1))
    expect(r!.toMs).toBe(Date.UTC(2015, 6, 1))

    const r2 = parseDatePhrase('Jun 2015')
    expect(r2!.fromMs).toBe(r!.fromMs)
    expect(r2!.toMs).toBe(r!.toMs)
  })

  it('returns null for garbage', () => {
    expect(parseDatePhrase('')).toBeNull()
    expect(parseDatePhrase('hello')).toBeNull()
    expect(parseDatePhrase('201')).toBeNull()
    expect(parseDatePhrase('99')).toBeNull()
    expect(parseDatePhrase('june')).toBeNull()
    expect(parseDatePhrase('2018..2014')).toBeNull()
    expect(parseDatePhrase('not a date 2015')).toBeNull()
  })
})
