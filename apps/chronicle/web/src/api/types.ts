/** Types mirroring chronicle_server JSON responses. */

export interface SessionInfo {
  username: string
}

export interface AccountSummary {
  account: string
  messages: number
}

export interface DateRange {
  from: string | null
  to: string | null
}

export interface ArchiveCounts {
  messages: number
  threads: number
  attachments: number
  contacts: number
}

export interface ExtractionCoverage {
  extracted: number
  failed: number
  skipped: number
  pending: number
}

export interface EmbeddingCoverage {
  embedded: number
  missing: number
}

export interface ArchiveVersions {
  schema: string
  api: string
}

export interface ArchiveSummary {
  accounts: AccountSummary[]
  date_range: DateRange
  counts: ArchiveCounts
  extraction: ExtractionCoverage
  embedding: EmbeddingCoverage
  versions: ArchiveVersions
}
