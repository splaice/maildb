import { useState } from 'react'

import type { ConstraintChip } from './scopeChips'

/** Chip origin for interpretation badges (syntax=steel, model=topic-purple). */
export type ChipOriginBadge = 'syntax' | 'model' | string

/**
 * Display chip for the constraint row. Extends the scope-derived chip with
 * optional origin badge and unresolved-person state from interpret.
 */
export interface DisplayConstraintChip extends ConstraintChip {
  origin?: ChipOriginBadge
  /** When true, muted-editable; edit with an address converts to sender. */
  unresolved?: boolean
  display?: string | null
}

export interface ConstraintChipsProps {
  chips: DisplayConstraintChip[]
  unsupported: string[]
  onEdit: (chip: DisplayConstraintChip, newValue: string) => void
  onRemove: (chip: DisplayConstraintChip) => void
  onRemoveUnsupported: (token: string) => void
  /** Resolve unresolved_person by filling an address (becomes sender). */
  onResolvePerson?: (chip: DisplayConstraintChip, address: string) => void
}

const chipClass =
  'inline-flex items-center gap-1 rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary'

const mutedChipClass =
  'inline-flex items-center gap-1 rounded-md border border-steel bg-graphite-900 px-2 py-1 text-text-muted'

const removeClass =
  'rounded px-1 text-text-muted hover:text-text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

function OriginDot({ origin }: { origin: ChipOriginBadge }) {
  const isModel = origin === 'model'
  const color = isModel ? 'bg-topic' : 'bg-steel'
  const title = isModel ? 'Origin: model' : origin === 'syntax' ? 'Origin: syntax' : `Origin: ${origin}`
  return (
    <span
      className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${color}`}
      title={title}
      data-testid="chip-origin-dot"
      data-origin={origin}
      aria-label={title}
    />
  )
}

export function ConstraintChips({
  chips,
  unsupported,
  onEdit,
  onRemove,
  onRemoveUnsupported,
  onResolvePerson,
}: ConstraintChipsProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')

  if (chips.length === 0 && unsupported.length === 0) {
    return null
  }

  return (
    <div
      className="flex flex-wrap items-center gap-1"
      role="list"
      aria-label="Parsed query constraints"
      data-testid="constraint-chips"
    >
      {chips.map((chip) => {
        const isEditing = editingId === chip.id
        const isUnresolved = Boolean(chip.unresolved)
        const rowClass = isUnresolved ? mutedChipClass : chipClass
        const labelValue = chip.display && !isUnresolved ? chip.display : chip.value

        return (
          <span
            key={chip.id}
            role="listitem"
            className={rowClass}
            data-testid={`constraint-chip-${chip.id}`}
            data-unresolved={isUnresolved ? 'true' : undefined}
            data-origin={chip.origin}
          >
            {isEditing ? (
              <form
                className="flex items-center gap-1"
                onSubmit={(e) => {
                  e.preventDefault()
                  const trimmed = editValue.trim()
                  if (isUnresolved && onResolvePerson) {
                    onResolvePerson(chip, trimmed)
                  } else {
                    onEdit(chip, trimmed)
                  }
                  setEditingId(null)
                }}
              >
                <span className="text-text-muted">
                  {isUnresolved ? 'person' : chip.category}:
                </span>
                <input
                  autoFocus
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setEditingId(null)
                  }}
                  placeholder={isUnresolved ? 'email@example.com' : undefined}
                  className="w-40 rounded border border-steel bg-graphite-900 px-1 text-text-primary"
                  data-testid={`constraint-edit-${chip.id}`}
                  aria-label={
                    isUnresolved
                      ? `Resolve person ${chip.value} to address`
                      : `Edit ${chip.category}`
                  }
                />
                <button type="submit" className={removeClass}>
                  OK
                </button>
              </form>
            ) : (
              <>
                {chip.origin ? <OriginDot origin={chip.origin} /> : null}
                <button
                  type="button"
                  className="text-left"
                  onClick={() => {
                    setEditingId(chip.id)
                    setEditValue(isUnresolved ? '' : chip.value)
                  }}
                  aria-label={
                    isUnresolved
                      ? `Resolve person: ${chip.value}`
                      : `Edit ${chip.category}: ${labelValue}`
                  }
                  data-testid={
                    isUnresolved
                      ? `unresolved-person-${chip.value}`
                      : undefined
                  }
                >
                  <span className="text-text-muted">
                    {isUnresolved ? 'person' : chip.category}:
                  </span>{' '}
                  {isUnresolved ? chip.value : labelValue}
                  {chip.display && !isUnresolved && chip.display !== chip.value ? (
                    <span className="text-text-muted"> ({chip.value})</span>
                  ) : null}
                </button>
                <button
                  type="button"
                  className={removeClass}
                  aria-label={
                    isUnresolved
                      ? `Remove unresolved ${chip.value}`
                      : `Remove ${chip.category} ${chip.value}`
                  }
                  onClick={() => onRemove(chip)}
                >
                  ×
                </button>
              </>
            )}
          </span>
        )
      })}
      {unsupported.map((token) => (
        <span
          key={`u:${token}`}
          role="listitem"
          className={mutedChipClass}
          data-testid={`unsupported-chip-${token}`}
        >
          <span>not yet supported: {token}</span>
          <button
            type="button"
            className={removeClass}
            aria-label={`Remove unsupported ${token}`}
            onClick={() => onRemoveUnsupported(token)}
          >
            ×
          </button>
        </span>
      ))}
    </div>
  )
}
