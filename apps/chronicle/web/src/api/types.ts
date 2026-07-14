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

/** GET /api/health/archive */

export interface HealthCoverage {
  accounts: AccountSummary[]
  date_range: DateRange
  messages: number
  threads: number
  attachments: number
  contacts: number
}

export interface HealthThreading {
  single_message_threads: number
  max_thread_size: number
  null_date_messages: number
}

export interface FailureReason {
  reason: string
  count: number
}

export interface ContentTypeExtraction {
  content_type: string
  extracted: number
  failed: number
  skipped: number
}

export interface HealthExtraction {
  by_status: ExtractionCoverage
  top_failure_reasons: FailureReason[]
  by_content_type: ContentTypeExtraction[]
}

export interface HealthEmbeddings {
  emails: EmbeddingCoverage
  attachment_chunks: EmbeddingCoverage
}

export interface ImportHistoryRow {
  started_at: string | null
  source_account: string
  status: string
  messages_inserted: number
  messages_skipped: number
}

export interface ArchiveHealth {
  coverage: HealthCoverage
  threading: HealthThreading
  extraction: HealthExtraction
  embeddings: HealthEmbeddings
  imports: ImportHistoryRow[]
  generated_at: string
}

/** POST /api/chronicle/buckets */

export interface QueryScopeDate {
  from?: string | null
  to?: string | null
}

export interface QueryScope {
  version?: number
  date?: QueryScopeDate | null
  mailboxes?: string[]
  senders?: string[]
}

export interface ChronicleTimeRange {
  from: string
  to: string
}

export interface BucketPoint {
  bucket: string
  count: number
}

export interface DensitySeries {
  unit: string
  buckets: BucketPoint[]
}

export interface ChronicleExtent {
  from: string | null
  to: string | null
}

/** Request body for POST /api/chronicle/buckets */
export interface ChronicleRequest {
  scope?: QueryScope
  viewport: ChronicleTimeRange
  pixel_width?: number
  aggregation?: string
  lanes?: string[]
}

/** Response body for POST /api/chronicle/buckets */
export interface ChronicleBuckets {
  scope_fingerprint: string
  aggregation: string
  unit: string
  viewport: ChronicleTimeRange
  lanes: Record<string, BucketPoint[]>
  density: DensitySeries
  extent: ChronicleExtent
  generated_at: string
}
