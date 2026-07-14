/**
 * Transient status-strip-style hints (e.g. G with no person selected).
 * Keyboard layer owns the channel so StatusStrip.tsx need not change.
 */

type Listener = (message: string | null) => void

let current: string | null = null
const listeners = new Set<Listener>()
let clearTimer: ReturnType<typeof setTimeout> | null = null

export function setStatusHint(message: string | null, ttlMs = 3000): void {
  if (clearTimer) {
    clearTimeout(clearTimer)
    clearTimer = null
  }
  current = message
  for (const l of listeners) l(current)
  if (message != null && ttlMs > 0) {
    clearTimer = setTimeout(() => {
      current = null
      for (const l of listeners) l(null)
      clearTimer = null
    }, ttlMs)
  }
}

export function getStatusHint(): string | null {
  return current
}

export function subscribeStatusHint(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
