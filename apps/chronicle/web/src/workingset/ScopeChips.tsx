import { type KeyboardEvent, useRef } from 'react'

import type { QueryScope } from '../api/types'
import { useWorkingSetStore } from './store'

export interface ScopeChipItem {
  id: string
  category: 'Date' | 'Mailbox' | 'Sender'
  label: string
  onRemove: () => void
  removeAriaLabel: string
}

export function chipsFromScope(
  scope: QueryScope,
  actions: {
    setScopeDate: (date: null) => void
    removeMailbox: (m: string) => void
    removeSender: (s: string) => void
  },
): ScopeChipItem[] {
  const chips: ScopeChipItem[] = []

  if (scope.date?.from || scope.date?.to) {
    const fromY = scope.date.from?.slice(0, 4) ?? '…'
    const toY = scope.date.to?.slice(0, 4) ?? '…'
    const value = `${fromY} – ${toY}`
    chips.push({
      id: 'date',
      category: 'Date',
      label: `Date: ${value}`,
      removeAriaLabel: `Remove filter Date ${value}`,
      onRemove: () => actions.setScopeDate(null),
    })
  }

  for (const mb of scope.mailboxes ?? []) {
    chips.push({
      id: `mailbox:${mb}`,
      category: 'Mailbox',
      label: `Mailbox: ${mb}`,
      removeAriaLabel: `Remove filter Mailbox ${mb}`,
      onRemove: () => actions.removeMailbox(mb),
    })
  }

  for (const sd of scope.senders ?? []) {
    chips.push({
      id: `sender:${sd}`,
      category: 'Sender',
      label: `Sender: ${sd}`,
      removeAriaLabel: `Remove filter Sender ${sd}`,
      onRemove: () => actions.removeSender(sd),
    })
  }

  return chips
}

const chipClass =
  'inline-flex items-center gap-1 rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

const removeClass =
  'rounded px-1 text-text-muted hover:text-text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

/**
 * Keyboard-reachable chip list: each chip is a button group; Left/Right
 * moves focus within the bar.
 */
export function ScopeChipList({ chips }: { chips: ScopeChipItem[] }) {
  const listRef = useRef<HTMLDivElement>(null)

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    const root = listRef.current
    if (!root) return
    const focusables = Array.from(
      root.querySelectorAll<HTMLElement>('[data-scope-chip-focus]'),
    )
    if (focusables.length === 0) return
    const active = document.activeElement as HTMLElement | null
    const idx = focusables.indexOf(active as HTMLElement)
    if (idx < 0) return
    e.preventDefault()
    const next =
      e.key === 'ArrowRight'
        ? Math.min(focusables.length - 1, idx + 1)
        : Math.max(0, idx - 1)
    focusables[next]?.focus()
  }

  if (chips.length === 0) {
    return (
      <span className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-muted">
        Archive: all mailboxes
      </span>
    )
  }

  return (
    <div
      ref={listRef}
      className="flex flex-wrap items-center gap-1"
      role="list"
      aria-label="Scope filters"
      onKeyDown={onKeyDown}
    >
      {chips.map((chip) => (
        <span
          key={chip.id}
          role="listitem"
          className={chipClass}
          data-testid={`scope-chip-${chip.category.toLowerCase()}`}
        >
          <button
            type="button"
            data-scope-chip-focus
            className="text-left"
            aria-label={chip.label}
          >
            <span className="text-text-muted">{chip.category}:</span>{' '}
            {chip.label.replace(/^[^:]+:\s*/, '')}
          </button>
          <button
            type="button"
            data-scope-chip-focus
            className={removeClass}
            aria-label={chip.removeAriaLabel}
            onClick={chip.onRemove}
          >
            ×
          </button>
        </span>
      ))}
    </div>
  )
}

/** Hook-friendly chip builder bound to the working-set store. */
export function useScopeChips(): ScopeChipItem[] {
  const scope = useWorkingSetStore((s) => s.scope)
  const setScopeDate = useWorkingSetStore((s) => s.setScopeDate)
  const removeMailbox = useWorkingSetStore((s) => s.removeMailbox)
  const removeSender = useWorkingSetStore((s) => s.removeSender)
  return chipsFromScope(scope, {
    setScopeDate: () => setScopeDate(null),
    removeMailbox,
    removeSender,
  })
}
