import { useState } from 'react'

import type { ConstraintChip } from './scopeChips'

export interface ConstraintChipsProps {
  chips: ConstraintChip[]
  unsupported: string[]
  onEdit: (chip: ConstraintChip, newValue: string) => void
  onRemove: (chip: ConstraintChip) => void
  onRemoveUnsupported: (token: string) => void
}

const chipClass =
  'inline-flex items-center gap-1 rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary'

const mutedChipClass =
  'inline-flex items-center gap-1 rounded-md border border-steel bg-graphite-900 px-2 py-1 text-text-muted'

const removeClass =
  'rounded px-1 text-text-muted hover:text-text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

export function ConstraintChips({
  chips,
  unsupported,
  onEdit,
  onRemove,
  onRemoveUnsupported,
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
        return (
          <span
            key={chip.id}
            role="listitem"
            className={chipClass}
            data-testid={`constraint-chip-${chip.id}`}
          >
            {isEditing ? (
              <form
                className="flex items-center gap-1"
                onSubmit={(e) => {
                  e.preventDefault()
                  onEdit(chip, editValue)
                  setEditingId(null)
                }}
              >
                <span className="text-text-muted">{chip.category}:</span>
                <input
                  autoFocus
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setEditingId(null)
                  }}
                  className="w-40 rounded border border-steel bg-graphite-900 px-1 text-text-primary"
                  data-testid={`constraint-edit-${chip.id}`}
                  aria-label={`Edit ${chip.category}`}
                />
                <button type="submit" className={removeClass}>
                  OK
                </button>
              </form>
            ) : (
              <>
                <button
                  type="button"
                  className="text-left"
                  onClick={() => {
                    setEditingId(chip.id)
                    setEditValue(chip.value)
                  }}
                  aria-label={`Edit ${chip.category}: ${chip.value}`}
                >
                  <span className="text-text-muted">{chip.category}:</span> {chip.value}
                </button>
                <button
                  type="button"
                  className={removeClass}
                  aria-label={`Remove ${chip.category} ${chip.value}`}
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
