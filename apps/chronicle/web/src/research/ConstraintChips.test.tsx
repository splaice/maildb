import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ConstraintChips, type DisplayConstraintChip } from './ConstraintChips'

const baseChip = (
  overrides: Partial<DisplayConstraintChip> & Pick<DisplayConstraintChip, 'id' | 'category' | 'value' | 'field'>,
): DisplayConstraintChip => ({
  index: -1,
  ...overrides,
})

describe('ConstraintChips', () => {
  it('renders origin badge dots with title text for syntax and model', () => {
    const chips: DisplayConstraintChip[] = [
      baseChip({
        id: 'from:a@x.com',
        category: 'from',
        value: 'a@x.com',
        field: 'senders',
        index: 0,
        origin: 'syntax',
      }),
      baseChip({
        id: 'date',
        category: 'date',
        value: '2014-01-01..2018-12-31',
        field: 'date',
        origin: 'model',
      }),
    ]
    render(
      <ConstraintChips
        chips={chips}
        unsupported={[]}
        onEdit={vi.fn()}
        onRemove={vi.fn()}
        onRemoveUnsupported={vi.fn()}
      />,
    )

    const dots = screen.getAllByTestId('chip-origin-dot')
    expect(dots).toHaveLength(2)
    expect(dots[0]).toHaveAttribute('data-origin', 'syntax')
    expect(dots[0]).toHaveAttribute('title', 'Origin: syntax')
    expect(dots[1]).toHaveAttribute('data-origin', 'model')
    expect(dots[1]).toHaveAttribute('title', 'Origin: model')
  })

  it('unresolved person chip is muted-editable and resolve applies address', () => {
    const onResolve = vi.fn()
    const chip = baseChip({
      id: 'unresolved:Alex',
      category: 'person',
      value: 'Alex',
      field: 'senders',
      index: -1,
      origin: 'model',
      unresolved: true,
      display: 'Alex',
    })
    render(
      <ConstraintChips
        chips={[chip]}
        unsupported={[]}
        onEdit={vi.fn()}
        onRemove={vi.fn()}
        onRemoveUnsupported={vi.fn()}
        onResolvePerson={onResolve}
      />,
    )

    const btn = screen.getByTestId('unresolved-person-Alex')
    expect(btn.closest('[data-unresolved="true"]')).toBeTruthy()
    fireEvent.click(btn)
    const input = screen.getByTestId('constraint-edit-unresolved:Alex')
    fireEvent.change(input, { target: { value: 'alex@example.com' } })
    fireEvent.submit(input.closest('form')!)
    expect(onResolve).toHaveBeenCalledWith(chip, 'alex@example.com')
  })
})
