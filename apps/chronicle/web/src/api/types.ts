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

/** POST /api/sources/list */

export interface SourceListRequest {
  scope?: QueryScope
  date_from: string
  date_to: string
  cursor?: string | null
  limit?: number
}

export interface SourceListItem {
  id: string
  subject: string | null
  sender_name: string | null
  sender_address: string | null
  date: string | null
  mailbox: string | null
  has_attachment: boolean
  attachment_count: number
  thread_id: string | null
}

export interface SourceListResponse {
  items: SourceListItem[]
  next_cursor: string | null
  scope_fingerprint: string
}

/** GET /api/sources/:sid (message) */

export interface AttachmentMeta {
  id: string
  filename: string
  content_type: string | null
  size: number | null
}

export interface MessageEnvelope {
  id: string
  thread_id: string | null
  subject: string | null
  sender_name: string | null
  sender_address: string | null
  recipients: unknown
  date: string | null
  mailbox: string | null
  labels: string[]
  has_attachment: boolean
  attachments: AttachmentMeta[]
}

export interface BodyDescriptor {
  text: string | null
  html: string | null
  remote_resources_blocked: number
  had_active_content: boolean
}

export interface MessageSource {
  kind: 'msg'
  envelope: MessageEnvelope
  body: BodyDescriptor
}

export interface AttachmentSource {
  kind: 'att'
  id: string
  filename: string
  content_type: string | null
  size: number | null
  source_message_id: string | null
  source_envelope: MessageEnvelope | null
  extraction_status: string | null
  extraction_reason: string | null
  markdown: string | null
  truncated: boolean
  text_offset: number
}

export type SourceResponse = MessageSource | AttachmentSource

/** GET /api/threads/:thr */

export interface ThreadParticipant {
  name: string | null
  address: string | null
}

export interface ThreadDateRange {
  from: string | null
  to: string | null
}

export interface ThreadMessage {
  id: string
  subject: string | null
  sender_name: string | null
  sender_address: string | null
  recipients: unknown
  date: string | null
  mailbox: string | null
  labels: string[]
  has_attachment: boolean
}

export interface ThreadResponse {
  thread_id: string
  subject: string | null
  date_range: ThreadDateRange
  participants: ThreadParticipant[]
  message_count: number
  messages: ThreadMessage[]
  truncated: boolean
}
