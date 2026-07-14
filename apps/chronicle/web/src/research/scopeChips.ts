import type { QueryScope } from '../api/types'

/** A parsed constraint rendered as an editable research chip. */
export interface ConstraintChip {
  id: string
  /** Operator-style category shown before the value. */
  category: string
  value: string
  /** Field key on QueryScope for edits. */
  field:
    | 'date.from'
    | 'date.to'
    | 'date'
    | 'mailboxes'
    | 'senders'
    | 'recipients'
    | 'participants'
    | 'subject_contains'
    | 'has_attachment'
    | 'file_types'
    | 'filenames'
    | 'source_types'
  /** Index into list fields; -1 for scalar. */
  index: number
}

/**
 * Build editable constraint chips from a merged search-response scope.
 * free_text is residual query — not a chip.
 */
export function chipsFromSearchScope(scope: QueryScope): ConstraintChip[] {
  const chips: ConstraintChip[] = []

  if (scope.date?.from || scope.date?.to) {
    const from = scope.date.from ?? '…'
    const to = scope.date.to ?? '…'
    chips.push({
      id: 'date',
      category: 'date',
      value: `${from} – ${to}`,
      field: 'date',
      index: -1,
    })
  }

  for (let i = 0; i < (scope.mailboxes ?? []).length; i++) {
    const v = scope.mailboxes![i]!
    chips.push({ id: `mailbox:${v}`, category: 'mailbox', value: v, field: 'mailboxes', index: i })
  }
  for (let i = 0; i < (scope.senders ?? []).length; i++) {
    const v = scope.senders![i]!
    chips.push({ id: `from:${v}`, category: 'from', value: v, field: 'senders', index: i })
  }
  for (let i = 0; i < (scope.recipients ?? []).length; i++) {
    const v = scope.recipients![i]!
    chips.push({ id: `to:${v}`, category: 'to', value: v, field: 'recipients', index: i })
  }
  for (let i = 0; i < (scope.participants ?? []).length; i++) {
    const v = scope.participants![i]!
    chips.push({
      id: `participant:${v}`,
      category: 'participant',
      value: v,
      field: 'participants',
      index: i,
    })
  }
  if (scope.subject_contains) {
    chips.push({
      id: 'subject',
      category: 'subject',
      value: scope.subject_contains,
      field: 'subject_contains',
      index: -1,
    })
  }
  if (scope.has_attachment != null) {
    chips.push({
      id: 'has_attachment',
      category: 'has',
      value: scope.has_attachment ? 'attachment' : 'no-attachment',
      field: 'has_attachment',
      index: -1,
    })
  }
  for (let i = 0; i < (scope.file_types ?? []).length; i++) {
    const v = scope.file_types![i]!
    chips.push({
      id: `filetype:${v}`,
      category: 'filetype',
      value: v,
      field: 'file_types',
      index: i,
    })
  }
  for (let i = 0; i < (scope.filenames ?? []).length; i++) {
    const v = scope.filenames![i]!
    chips.push({
      id: `filename:${v}`,
      category: 'filename',
      value: v,
      field: 'filenames',
      index: i,
    })
  }
  for (let i = 0; i < (scope.source_types ?? []).length; i++) {
    const v = scope.source_types![i]!
    chips.push({
      id: `is:${v}`,
      category: 'is',
      value: v,
      field: 'source_types',
      index: i,
    })
  }

  return chips
}

/** Apply an edited chip value back onto a scope copy. */
export function applyChipEdit(
  scope: QueryScope,
  chip: ConstraintChip,
  newValue: string,
): QueryScope {
  const next: QueryScope = {
    ...scope,
    mailboxes: scope.mailboxes ? [...scope.mailboxes] : undefined,
    senders: scope.senders ? [...scope.senders] : undefined,
    recipients: scope.recipients ? [...scope.recipients] : undefined,
    participants: scope.participants ? [...scope.participants] : undefined,
    file_types: scope.file_types ? [...scope.file_types] : undefined,
    filenames: scope.filenames ? [...scope.filenames] : undefined,
    source_types: scope.source_types ? [...scope.source_types] : undefined,
    date: scope.date ? { ...scope.date } : undefined,
  }
  const trimmed = newValue.trim()

  switch (chip.field) {
    case 'date': {
      // Expect "from – to" or single year-ish; keep simple split on en-dash/hyphen span.
      const parts = trimmed.split(/\s*[–-]\s*/)
      if (parts.length >= 2) {
        next.date = {
          from: parts[0] === '…' ? undefined : parts[0],
          to: parts[1] === '…' ? undefined : parts[1],
        }
      }
      break
    }
    case 'date.from':
      next.date = { ...(next.date ?? {}), from: trimmed || undefined }
      break
    case 'date.to':
      next.date = { ...(next.date ?? {}), to: trimmed || undefined }
      break
    case 'subject_contains':
      next.subject_contains = trimmed || null
      break
    case 'has_attachment':
      next.has_attachment =
        trimmed === 'attachment' || trimmed === 'true' || trimmed === 'yes'
          ? true
          : trimmed === 'no-attachment' || trimmed === 'false' || trimmed === 'no'
            ? false
            : true
      break
    case 'mailboxes':
    case 'senders':
    case 'recipients':
    case 'participants':
    case 'file_types':
    case 'filenames':
    case 'source_types': {
      const list = next[chip.field] ?? []
      if (chip.index >= 0 && chip.index < list.length) {
        if (!trimmed) {
          list.splice(chip.index, 1)
        } else {
          list[chip.index] = trimmed
        }
        next[chip.field] = list
      }
      break
    }
  }
  return next
}

/** Remove a chip's constraint from scope. */
export function removeChipFromScope(scope: QueryScope, chip: ConstraintChip): QueryScope {
  const next: QueryScope = {
    ...scope,
    mailboxes: scope.mailboxes ? [...scope.mailboxes] : undefined,
    senders: scope.senders ? [...scope.senders] : undefined,
    recipients: scope.recipients ? [...scope.recipients] : undefined,
    participants: scope.participants ? [...scope.participants] : undefined,
    file_types: scope.file_types ? [...scope.file_types] : undefined,
    filenames: scope.filenames ? [...scope.filenames] : undefined,
    source_types: scope.source_types ? [...scope.source_types] : undefined,
    date: scope.date ? { ...scope.date } : undefined,
  }

  switch (chip.field) {
    case 'date':
    case 'date.from':
    case 'date.to':
      delete next.date
      break
    case 'subject_contains':
      delete next.subject_contains
      break
    case 'has_attachment':
      delete next.has_attachment
      break
    case 'mailboxes':
    case 'senders':
    case 'recipients':
    case 'participants':
    case 'file_types':
    case 'filenames':
    case 'source_types': {
      const list = next[chip.field] ?? []
      if (chip.index >= 0) list.splice(chip.index, 1)
      else {
        // remove by value
        const filtered = list.filter((x) => x !== chip.value)
        next[chip.field] = filtered
      }
      if ((next[chip.field]?.length ?? 0) === 0) delete next[chip.field]
      break
    }
  }
  return next
}
