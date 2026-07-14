/** Local types for version families + attachment compare (task 4.5). */

export type FamilyConfidence = 'exact-duplicate' | 'probable-version'

export interface FamilyCandidate {
  id: string
  filename: string
  date: string | null
  sender: string | null
  size: number | null
  sha256: string
  confidence: FamilyConfidence
  signals: string[]
}

export interface AttachmentFamilyResponse {
  id: string
  stem: string
  candidates: FamilyCandidate[]
}

export type DiffLineKind = 'same' | 'add' | 'del'

export interface DiffLine {
  kind: DiffLineKind
  text: string
}

export interface DiffHunk {
  a_start: number
  b_start: number
  lines: DiffLine[]
}

export interface AmountChange {
  kind: 'add' | 'del'
  text: string
  amounts: string[]
}

export interface AttachmentMeta {
  id: string
  filename: string
  content_type: string | null
  size: number | null
  date: string | null
  sender: string | null
  sha256: string
  source_message_id: string | null
}

export interface AttachmentCompareResponse {
  a: AttachmentMeta
  b: AttachmentMeta
  hunks: DiffHunk[]
  truncated: boolean
  amount_changes: AmountChange[]
}

/** List item may include family_count from the server. */
export interface AttachmentListItemWithFamily {
  id: string
  family_count?: number
}
