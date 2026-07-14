import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  DENSITY_CLASS,
  DENSITY_STORAGE_KEY,
  REDUCE_MOTION_CLASS,
  REDUCED_MOTION_STORAGE_KEY,
  applyAppearanceFromStorage,
  getTimelineMotionProps,
  readDensity,
  readReducedMotion,
  shouldReduceMotion,
  writeDensity,
  writeReducedMotion,
} from './appearance'

/** In-memory localStorage for environments where jsdom omits it. */
function installMemoryLocalStorage(): void {
  const map = new Map<string, string>()
  const storage: Storage = {
    get length() {
      return map.size
    },
    clear() {
      map.clear()
    },
    getItem(key: string) {
      return map.has(key) ? map.get(key)! : null
    },
    key(index: number) {
      return [...map.keys()][index] ?? null
    },
    removeItem(key: string) {
      map.delete(key)
    },
    setItem(key: string, value: string) {
      map.set(key, String(value))
    },
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
    writable: true,
  })
}

describe('appearance prefs', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
    document.body.classList.remove(
      DENSITY_CLASS.compact,
      DENSITY_CLASS.comfortable,
      REDUCE_MOTION_CLASS,
    )
  })

  afterEach(() => {
    document.body.classList.remove(
      DENSITY_CLASS.compact,
      DENSITY_CLASS.comfortable,
      REDUCE_MOTION_CLASS,
    )
  })

  it('defaults density to compact and motion to auto', () => {
    expect(readDensity()).toBe('compact')
    expect(readReducedMotion()).toBe('auto')
  })

  it('persists density and applies body class', () => {
    writeDensity('comfortable')
    expect(readDensity()).toBe('comfortable')
    expect(localStorage.getItem(DENSITY_STORAGE_KEY)).toBe('comfortable')
    expect(document.body.classList.contains(DENSITY_CLASS.comfortable)).toBe(
      true,
    )
  })

  it('Always reduced motion sets body class and timeline flag', () => {
    writeReducedMotion('always')
    expect(readReducedMotion()).toBe('always')
    expect(document.body.classList.contains(REDUCE_MOTION_CLASS)).toBe(true)
    expect(shouldReduceMotion('always')).toBe(true)
    expect(getTimelineMotionProps('always')).toEqual({ reducedMotion: true })
  })

  it('Auto without system preference → reducedMotion false', () => {
    writeReducedMotion('auto')
    expect(getTimelineMotionProps('auto').reducedMotion).toBe(
      shouldReduceMotion('auto'),
    )
  })

  it('applyAppearanceFromStorage restores both classes', () => {
    localStorage.setItem(DENSITY_STORAGE_KEY, 'comfortable')
    localStorage.setItem(REDUCED_MOTION_STORAGE_KEY, 'always')
    applyAppearanceFromStorage()
    expect(document.body.classList.contains(DENSITY_CLASS.comfortable)).toBe(
      true,
    )
    expect(document.body.classList.contains(REDUCE_MOTION_CLASS)).toBe(true)
  })
})
