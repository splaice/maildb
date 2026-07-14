/**
 * Claim status visual coding: text + symbol (never color alone).
 * direct=green ✓, supported=blue ~, conflicting=red ✕, unresolved=muted ?
 */

import type { ClaimStatus } from '../api/types'

export interface ClaimStatusVisual {
  symbol: string
  label: string
  /** Tailwind text class (color is supplementary to symbol+label). */
  className: string
}

const MAP: Record<string, ClaimStatusVisual> = {
  direct: {
    symbol: '✓',
    label: 'direct',
    className: 'text-attachment',
  },
  supported: {
    symbol: '~',
    label: 'supported',
    className: 'text-action',
  },
  conflicting: {
    symbol: '✕',
    label: 'conflicting',
    className: 'text-conflict',
  },
  unresolved: {
    symbol: '?',
    label: 'unresolved',
    className: 'text-text-muted',
  },
}

export function claimStatusVisual(
  status: ClaimStatus | string,
): ClaimStatusVisual {
  return (
    MAP[status] ?? {
      symbol: '?',
      label: String(status),
      className: 'text-text-muted',
    }
  )
}

/** Prefix string e.g. "✓ direct" for accessible text coding. */
export function claimStatusText(status: ClaimStatus | string): string {
  const v = claimStatusVisual(status)
  return `${v.symbol} ${v.label}`
}
