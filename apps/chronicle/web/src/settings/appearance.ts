/**
 * Per-device appearance preferences (G-008): density + reduced motion.
 * Persisted in localStorage; applied as body classes by Workstation.
 */

export type Density = 'compact' | 'comfortable'
export type ReducedMotionPref = 'auto' | 'always'

export const DENSITY_STORAGE_KEY = 'chronicle.appearance.density.v1'
export const REDUCED_MOTION_STORAGE_KEY = 'chronicle.appearance.reducedMotion.v1'

export const DENSITY_CLASS = {
  compact: 'density-compact',
  comfortable: 'density-comfortable',
} as const

/** Always-on reduced motion (ignores prefers-reduced-motion media). */
export const REDUCE_MOTION_CLASS = 'reduce-motion'

export function readDensity(): Density {
  try {
    const v = localStorage.getItem(DENSITY_STORAGE_KEY)
    if (v === 'comfortable' || v === 'compact') return v
  } catch {
    /* ignore */
  }
  return 'compact'
}

export function writeDensity(density: Density): void {
  try {
    localStorage.setItem(DENSITY_STORAGE_KEY, density)
  } catch {
    /* ignore */
  }
  applyDensityClass(density)
}

export function readReducedMotion(): ReducedMotionPref {
  try {
    const v = localStorage.getItem(REDUCED_MOTION_STORAGE_KEY)
    if (v === 'always' || v === 'auto') return v
  } catch {
    /* ignore */
  }
  return 'auto'
}

export function writeReducedMotion(pref: ReducedMotionPref): void {
  try {
    localStorage.setItem(REDUCED_MOTION_STORAGE_KEY, pref)
  } catch {
    /* ignore */
  }
  applyReducedMotionClass(pref)
}

/** Whether timeline/canvas should use instant viewport (no smooth pan easing). */
export function shouldReduceMotion(pref: ReducedMotionPref = readReducedMotion()): boolean {
  if (pref === 'always') return true
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * Props contract for TimelineCanvas: when reducedMotion is true, viewport
 * changes apply instantly (no smooth-pan easing).
 */
export function getTimelineMotionProps(
  pref: ReducedMotionPref = readReducedMotion(),
): { reducedMotion: boolean } {
  return { reducedMotion: shouldReduceMotion(pref) }
}

export function applyDensityClass(density: Density = readDensity()): void {
  if (typeof document === 'undefined') return
  const body = document.body
  body.classList.remove(DENSITY_CLASS.compact, DENSITY_CLASS.comfortable)
  body.classList.add(DENSITY_CLASS[density])
}

export function applyReducedMotionClass(
  pref: ReducedMotionPref = readReducedMotion(),
): void {
  if (typeof document === 'undefined') return
  const body = document.body
  if (pref === 'always') {
    body.classList.add(REDUCE_MOTION_CLASS)
  } else {
    body.classList.remove(REDUCE_MOTION_CLASS)
  }
}

/** Apply both appearance classes from storage (call on shell mount). */
export function applyAppearanceFromStorage(): void {
  applyDensityClass(readDensity())
  applyReducedMotionClass(readReducedMotion())
}
