/**
 * Central §14.2 shortcut registry. Pure and testable.
 * Pages register only their applicable shortcuts; cleanup on unmount.
 */

export type ShortcutChord = {
  /** Primary key: letter (lowercase), `/`, `?`, `[`, `]`, `Enter`, ` `, `Escape`. */
  key: string
  /** Ctrl or Meta (Cmd). */
  mod?: boolean
  shift?: boolean
  alt?: boolean
}

export interface ShortcutBinding {
  id: string
  chord: ShortcutChord
  description: string
  /** Grouping for the `?` reference overlay. */
  group: string
  /**
   * Handler. Return `false` to signal "not handled" (dispatcher will not
   * preventDefault). Void/true = handled.
   */
  run: (event: KeyboardEvent) => void | boolean
}

function chordKey(c: ShortcutChord): string {
  const parts = [
    c.mod ? 'mod' : '',
    c.shift ? 'shift' : '',
    c.alt ? 'alt' : '',
    c.key.length === 1 ? c.key.toLowerCase() : c.key,
  ].filter(Boolean)
  return parts.join('+')
}

export function eventToChordKey(e: KeyboardEvent): string {
  const key =
    e.key.length === 1 ? e.key.toLowerCase() : e.key === ' ' ? ' ' : e.key
  const parts = [
    e.metaKey || e.ctrlKey ? 'mod' : '',
    e.shiftKey ? 'shift' : '',
    e.altKey ? 'alt' : '',
    key,
  ].filter(Boolean)
  return parts.join('+')
}

export type ConflictWarnFn = (message: string, existingId: string, newId: string) => void

export class ShortcutRegistry {
  private byId = new Map<string, ShortcutBinding>()
  private byChord = new Map<string, string>() // chordKey → id
  private onConflict: ConflictWarnFn

  constructor(onConflict?: ConflictWarnFn) {
    this.onConflict =
      onConflict ??
      ((msg, existingId, newId) => {
        if (import.meta.env?.DEV) {
          console.warn(`[shortcuts] ${msg} (${existingId} vs ${newId})`)
        }
      })
  }

  register(binding: ShortcutBinding): () => void {
    const ck = chordKey(binding.chord)
    const existingChordOwner = this.byChord.get(ck)
    if (existingChordOwner && existingChordOwner !== binding.id) {
      this.onConflict(
        `chord conflict on "${ck}"`,
        existingChordOwner,
        binding.id,
      )
    }
    // Replace same id if re-registered.
    const prev = this.byId.get(binding.id)
    if (prev) {
      const prevCk = chordKey(prev.chord)
      if (this.byChord.get(prevCk) === binding.id) {
        this.byChord.delete(prevCk)
      }
    }
    this.byId.set(binding.id, binding)
    this.byChord.set(ck, binding.id)
    return () => this.unregister(binding.id)
  }

  unregister(id: string): void {
    const prev = this.byId.get(id)
    if (!prev) return
    const ck = chordKey(prev.chord)
    if (this.byChord.get(ck) === id) {
      this.byChord.delete(ck)
    }
    this.byId.delete(id)
  }

  get(id: string): ShortcutBinding | undefined {
    return this.byId.get(id)
  }

  /** Currently registered bindings (for `?` overlay). */
  list(): ShortcutBinding[] {
    return Array.from(this.byId.values()).sort((a, b) => {
      const g = a.group.localeCompare(b.group)
      if (g !== 0) return g
      return a.id.localeCompare(b.id)
    })
  }

  /** Resolve event to a binding, or null. */
  match(e: KeyboardEvent): ShortcutBinding | null {
    const ck = eventToChordKey(e)
    const id = this.byChord.get(ck)
    if (!id) return null
    return this.byId.get(id) ?? null
  }

  /** Test helper: clear all. */
  clear(): void {
    this.byId.clear()
    this.byChord.clear()
  }
}

/** Format a chord for display in the reference table. */
export function formatChord(c: ShortcutChord): string {
  const parts: string[] = []
  if (c.mod) parts.push('Ctrl/Cmd')
  if (c.shift) parts.push('Shift')
  if (c.alt) parts.push('Alt')
  const k = c.key === ' ' ? 'Space' : c.key === 'Escape' ? 'Esc' : c.key
  parts.push(k.length === 1 ? k.toUpperCase() : k)
  return parts.join('+')
}
