/**
 * WCAG 2.x relative luminance and contrast ratio (pure, no DOM).
 * https://www.w3.org/TR/WCAG21/#dfn-relative-luminance
 */

function srgbChannelToLinear(c: number): number {
  const cs = c / 255
  return cs <= 0.04045 ? cs / 12.92 : ((cs + 0.055) / 1.055) ** 2.4
}

/** Parse #RGB or #RRGGBB (case-insensitive) to [r,g,b] 0–255. */
export function parseHex(hex: string): [number, number, number] {
  let h = hex.trim().replace(/^#/, '')
  if (h.length === 3) {
    h = h
      .split('')
      .map((ch) => ch + ch)
      .join('')
  }
  if (!/^[0-9a-fA-F]{6}$/.test(h)) {
    throw new Error(`invalid hex color: ${hex}`)
  }
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ]
}

export function relativeLuminance(hex: string): number {
  const [r, g, b] = parseHex(hex)
  const R = srgbChannelToLinear(r)
  const G = srgbChannelToLinear(g)
  const B = srgbChannelToLinear(b)
  return 0.2126 * R + 0.7152 * G + 0.0722 * B
}

/** WCAG contrast ratio of two colors (order-independent), ≥ 1. */
export function contrastRatio(hexA: string, hexB: string): number {
  const L1 = relativeLuminance(hexA)
  const L2 = relativeLuminance(hexB)
  const lighter = Math.max(L1, L2)
  const darker = Math.min(L1, L2)
  return (lighter + 0.05) / (darker + 0.05)
}

/** §13.1 tokens used in the UI (from index.css). */
export const TOKENS = {
  'graphite-950': '#0d1117',
  'graphite-900': '#151b23',
  'text-primary': '#e6edf3',
  'text-muted': '#91a0b5',
  action: '#5aa7ff',
  conflict: '#f07470',
  event: '#e0a84a',
} as const

/**
 * Pairs under test (spec §13.1 roles on graphite surfaces).
 * text roles ≥ 4.5:1; large/status accents ≥ 3:1.
 */
export const TOKEN_PAIRS: {
  name: string
  fg: string
  bg: string
  role: 'text' | 'large'
  /** When true, failure is documented rather than failing the suite (5.5 report). */
  expectedFail?: boolean
}[] = [
  {
    name: 'text-primary / graphite-950',
    fg: TOKENS['text-primary'],
    bg: TOKENS['graphite-950'],
    role: 'text',
  },
  {
    name: 'text-primary / graphite-900',
    fg: TOKENS['text-primary'],
    bg: TOKENS['graphite-900'],
    role: 'text',
  },
  {
    name: 'text-muted / graphite-900',
    fg: TOKENS['text-muted'],
    bg: TOKENS['graphite-900'],
    role: 'text',
  },
  {
    name: 'action / graphite-900',
    fg: TOKENS.action,
    bg: TOKENS['graphite-900'],
    role: 'large',
  },
  {
    name: 'conflict / graphite-900',
    fg: TOKENS.conflict,
    bg: TOKENS['graphite-900'],
    role: 'large',
  },
  {
    name: 'event / graphite-900',
    fg: TOKENS.event,
    bg: TOKENS['graphite-900'],
    role: 'large',
  },
]
