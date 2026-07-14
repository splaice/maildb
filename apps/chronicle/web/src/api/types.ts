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

/** Last 25 model/export audit rows (ask, events_generate, workspace_export, download). */
export interface AuditTailRow {
  at: string | null
  username: string
  action: string
  detail: Record<string, unknown>
}

export interface ArchiveHealth {
  coverage: HealthCoverage
  threading: HealthThreading
  extraction: HealthExtraction
  embeddings: HealthEmbeddings
  imports: ImportHistoryRow[]
  /** Model & export activity (spec §11.1 Audit). */
  audit_tail?: AuditTailRow[]
  generated_at: string
}

/** POST /api/events/generate */

export interface EventGenerateRequest {
  scope?: QueryScope
  viewport: ChronicleTimeRange
}

export interface EventGenerateResult {
  bursts: number
  created: number
  superseded: number
  suggested: number
  skipped_unavailable: boolean
}

export interface EventGenerateUnavailable {
  available: false
}

export type EventGenerateResponse = EventGenerateResult | EventGenerateUnavailable

export function isEventGenerateUnavailable(
  body: EventGenerateResponse,
): body is EventGenerateUnavailable {
  return (
    body != null &&
    typeof body === 'object' &&
    'available' in body &&
    (body as EventGenerateUnavailable).available === false
  )
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
  /** v2 additive fields (search / query-syntax) */
  recipients?: string[]
  participants?: string[]
  subject_contains?: string | null
  has_attachment?: boolean | null
  file_types?: string[]
  filenames?: string[]
  source_types?: string[]
  free_text?: string | null
}

export interface ChronicleTimeRange {
  from: string
  to: string
}

export interface BucketPoint {
  bucket: string
  count: number
}

/** top_people lane: activity bucket series for one contact. */
export interface TopPeopleContact {
  contact_id: string
  display_name: string
  buckets: BucketPoint[]
}

/** top_people lane payload (activity spans only). */
export interface TopPeopleLane {
  contacts: TopPeopleContact[]
}

/** topics lane: activity bucket series for one topic. */
export interface TopicSeries {
  topic_id: string
  label: string
  origin: string
  buckets: BucketPoint[]
}

/** topics lane payload (top topics by volume; multirow). */
export interface TopicsLane {
  topics: TopicSeries[]
}

/** Sparse event diamond mark on the events lane. */
export interface EventLaneMark {
  event_id: string
  title: string
  time_start: string
  time_end: string | null
  time_precision: string
  origin: string
  event_type: string
  status: string
  evidence_strength: string | null
}

/** events lane payload (sparse diamonds, not bucket counts). */
export interface EventsLane {
  events: EventLaneMark[]
  truncated: boolean
}

/** A bars-style lane is BucketPoint[]; multirow / events are nested objects. */
export type LaneData = BucketPoint[] | TopPeopleLane | TopicsLane | EventsLane

export function isTopPeopleLane(data: LaneData | undefined): data is TopPeopleLane {
  return (
    data != null &&
    typeof data === 'object' &&
    !Array.isArray(data) &&
    Array.isArray((data as TopPeopleLane).contacts)
  )
}

export function isTopicsLane(data: LaneData | undefined): data is TopicsLane {
  return (
    data != null &&
    typeof data === 'object' &&
    !Array.isArray(data) &&
    Array.isArray((data as TopicsLane).topics)
  )
}

export function isEventsLane(data: LaneData | undefined): data is EventsLane {
  return (
    data != null &&
    typeof data === 'object' &&
    !Array.isArray(data) &&
    Array.isArray((data as EventsLane).events)
  )
}

export function isBucketSeries(data: LaneData | undefined): data is BucketPoint[] {
  return Array.isArray(data)
}

/** Event origin (Table 15). */
export type EventOrigin = 'source' | 'imported' | 'automatic' | 'analyst'

export type EventTimePrecision =
  | 'year'
  | 'quarter'
  | 'month'
  | 'week'
  | 'day'
  | 'hour'

export type EventType =
  | 'decision'
  | 'meeting'
  | 'travel'
  | 'purchase'
  | 'deadline'
  | 'transition'
  | 'document'
  | 'communication'
  | 'user_defined'

export type EventStatus =
  | 'unreviewed'
  | 'confirmed'
  | 'edited'
  | 'dismissed'
  | 'superseded'
  | 'unresolved'

export type ClaimStatus = 'direct' | 'supported' | 'conflicting' | 'unresolved'

export interface EventCitation {
  source_id: string
  source_type: string
  excerpt?: string | null
  excerpt_hash?: string | null
  location?: Record<string, unknown> | null
  /** Hydrated display metadata */
  date?: string | null
  sender?: string | null
  subject?: string | null
}

export interface EventClaim {
  id: string
  position: number
  text: string
  status: ClaimStatus | string
  citations: EventCitation[]
}

export interface EventVersion {
  version: number
  author: string
  title: string
  summary: string | null
  derivation: Record<string, unknown>
  created_at?: string | null
}

/** Conflict row on GET /api/events/:id (claim statuses for UI conflict panel). */
export interface EventConflict {
  claim_position: number
  statuses: string[]
}

export interface ChronicleEvent {
  id: string
  title: string
  time_start: string
  time_end: string | null
  time_precision: EventTimePrecision | string
  origin: EventOrigin | string
  event_type: EventType | string
  status: EventStatus | string
  evidence_strength: string | null
  scope_fingerprint?: string | null
  current_version: number
  created_at?: string | null
  updated_at?: string | null
  summary?: string | null
  derivation?: Record<string, unknown>
  version?: EventVersion | null
  claims?: EventClaim[]
  /** True when any version number is higher than current_version. */
  has_suggestions?: boolean
  /** Claims with conflicting status or source-overlap conflicts. */
  conflicts?: EventConflict[]
}

/** One version row from GET /api/events/:id/versions. */
export interface EventVersionDetail extends EventVersion {
  claims: EventClaim[]
  is_suggestion: boolean
}

/** GET /api/events/:id/versions */
export interface EventVersionsResponse {
  event_id: string
  current_version: number
  versions: EventVersionDetail[]
}

/** POST /api/events/:id/adopt/:version */
export interface EventAdoptRequest {
  current_version: number
}

/** POST /api/events/list */
export interface EventListRequest {
  scope?: QueryScope
  viewport: ChronicleTimeRange
  include_dismissed?: boolean
  cursor?: string | null
  limit?: number
}

export interface EventListResponse {
  items: ChronicleEvent[]
  next_cursor: string | null
}

/** GET /api/sources/:sid/context */
export interface SourceContext {
  id: string
  start: number
  end: number
  excerpt: string
  context_before: string
  context_after: string
  sha256: string
  window: number
}

export interface EventCreateRequest {
  title: string
  time_start: string
  time_end?: string | null
  time_precision: EventTimePrecision | string
  event_type: EventType | string
  summary?: string | null
  claims?: Array<{
    text: string
    citations?: string[]
    status?: ClaimStatus | string
  }>
}

export interface EventPatchRequest {
  current_version: number
  title?: string
  time_start?: string
  time_end?: string | null
  time_precision?: EventTimePrecision | string
  event_type?: EventType | string
  summary?: string | null
  claims?: Array<{
    text: string
    citations?: string[]
    status?: ClaimStatus | string
  }>
  status?: EventStatus | string
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
  lanes: Record<string, LaneData>
  density: DensitySeries
  extent: ChronicleExtent
  generated_at: string
}

/** POST /api/chronicle/compare */

export interface ChronicleCompareRequest {
  scope?: QueryScope
  a: ChronicleTimeRange
  b: ChronicleTimeRange
  pixel_width?: number
  lanes?: string[]
}

export interface ChronicleCompareSide {
  viewport: ChronicleTimeRange
  lanes: Record<string, LaneData>
}

export interface ChronicleCompareTotalsSide {
  messages: number
  attachments: number
}

export interface ChronicleCompareTotals {
  a: ChronicleCompareTotalsSide
  b: ChronicleCompareTotalsSide
}

/** Response body for POST /api/chronicle/compare */
export interface ChronicleCompare {
  unit: string
  aligned: boolean
  a: ChronicleCompareSide
  b: ChronicleCompareSide
  totals: ChronicleCompareTotals
  scope_fingerprint: string
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

/** POST /api/search */

export type SearchMode = 'hybrid' | 'exact' | 'semantic'

export interface SearchRequest {
  query?: string
  mode?: SearchMode
  scope?: QueryScope
  limit?: number
  cursor?: string | null
  include_facets?: boolean
}

export interface ExactMatchInfo {
  kind: 'exact'
  field?: string
}

export interface SemanticMatchInfo {
  kind: 'semantic'
  similarity?: number | null
}

export interface HybridMatchInfo {
  kind: 'hybrid'
  exact_rank?: number | null
  semantic_rank?: number | null
  similarity?: number | null
}

export type MatchInfo = ExactMatchInfo | SemanticMatchInfo | HybridMatchInfo

export interface MessageSearchResult {
  result_type: 'message'
  id: string
  subject: string | null
  sender: string | null
  sender_name?: string | null
  date: string | null
  mailbox: string | null
  thread_id: string | null
  snippet: string
  has_attachment: boolean
  /** Optional; badge when present and > 1 */
  thread_size?: number
  match: MatchInfo
}

export interface AttachmentSearchResult {
  result_type: 'attachment'
  id: string
  filename: string
  content_type: string | null
  source_message_id: string | null
  sender: string | null
  date: string | null
  snippet: string
  extraction_status: string | null
  match: MatchInfo
}

export type SearchResult = MessageSearchResult | AttachmentSearchResult

export interface FacetBucket {
  value: string | number | boolean
  count: number
}

export interface SearchFacets {
  mailbox?: FacetBucket[]
  year?: FacetBucket[]
  has_attachment?: FacetBucket[]
  [key: string]: FacetBucket[] | undefined
}

export interface SearchResponse {
  results: SearchResult[]
  next_cursor: string | null
  scope: QueryScope
  unsupported: string[]
  scope_fingerprint: string
  mode: SearchMode
  took_ms: number
  duplicates_suppressed: number
  facets: SearchFacets | null
  facet_basis: string | null
  degraded: Record<string, string> | null
}

/** POST /api/query/interpret */

export type ChipOrigin = 'syntax' | 'model'

export interface InterpretChip {
  kind: string
  value: string
  origin: ChipOrigin | string
  display?: string | null
}

export interface InterpretRequest {
  text: string
  scope?: QueryScope
}

export interface InterpretResponse {
  scope: QueryScope
  free_text: string
  chips: InterpretChip[]
  model_used: boolean
}

/** POST /api/ask */

export type DeskMode = 'search' | 'ask'

export interface AskRequest {
  question: string
  scope?: QueryScope
  mode?: 'scope'
}

export interface AskUnavailableResponse {
  available: false
  reason: string
}

export interface AskRetrievalPayload {
  count: number
  types: { message?: number; attachment?: number; [k: string]: number | undefined }
  degraded: Record<string, string> | null
}

export interface AskCitationPayload {
  marker: string
  source_id: string
  source_type: string
  excerpt: string
  location: { char_start?: number; char_end?: number; [k: string]: unknown } | null
}

export interface AskDonePayload {
  answer_id: string
  model_route: string
  policy_version: string
  generated_at: string
  unmatched_markers: string[]
}

/** POST /api/attachments/list */

export type ContentTypeFamily =
  | 'pdf'
  | 'image'
  | 'spreadsheet'
  | 'document'
  | 'text'
  | 'other'

export interface AttachmentListFilters {
  filename?: string | null
  content_type_family?: ContentTypeFamily | null
  status?: string | null
  date_from?: string | null
  date_to?: string | null
}

export interface AttachmentListRequest {
  scope?: QueryScope
  filters?: AttachmentListFilters
  cursor?: string | null
  limit?: number
  group_duplicates?: boolean
}

export interface ExtractionInfo {
  status: string
  reason?: string | null
}

export interface AttachmentOccurrence {
  id: string
  subject: string | null
  sender: string | null
  date: string | null
}

export interface AttachmentListItem {
  id: string
  filename: string
  content_type: string | null
  size: number | null
  date: string | null
  sender_name: string | null
  sender_address: string | null
  source_message_id: string
  source_subject: string | null
  extraction: ExtractionInfo
  sha256: string
  duplicate_count: number
  occurrences?: AttachmentOccurrence[] | null
}

export interface AttachmentListResponse {
  items: AttachmentListItem[]
  next_cursor: string | null
  scope_fingerprint: string
}

export interface PreviewDenied {
  preview: false
  reason: string
}

/** Workspaces (GET/POST /api/workspaces) */

export type WorkspaceBlockType = 'heading' | 'note' | 'pin' | 'answer'

export interface WorkspaceCounts {
  blocks: number
  pins: number
  notes: number
  answers: number
  headings: number
}

export interface WorkspaceListItem {
  id: string
  name: string
  updated_at: string | null
  counts: WorkspaceCounts
}

export interface WorkspaceListResponse {
  items: WorkspaceListItem[]
}

export interface HeadingBlockContent {
  text: string
}

export interface NoteBlockContent {
  text: string
}

export interface PinBlockContent {
  source_id: string
  source_type: string
  title: string
  date?: string | null
  sender?: string | null
  excerpt?: string | null
}

export interface AnswerBlockContent {
  answer_id: string
}

export type WorkspaceBlockContent =
  | HeadingBlockContent
  | NoteBlockContent
  | PinBlockContent
  | AnswerBlockContent

export interface WorkspaceAnswerCitation {
  marker: string
  source_id: string
  source_type: string
  excerpt?: string | null
  excerpt_hash?: string | null
  location?: Record<string, unknown> | null
}

export interface WorkspaceAnswerHydration {
  answer_id: string
  question?: string | null
  answer_text?: string | null
  status?: string | null
  model_route?: string | null
  policy_version?: string | null
  scope_fingerprint?: string | null
  created_at?: string | null
  citations: WorkspaceAnswerCitation[]
}

export interface WorkspaceBlock {
  id: string
  workspace_id: string
  position: number
  block_type: WorkspaceBlockType
  content: WorkspaceBlockContent & Record<string, unknown>
  created_at?: string | null
  updated_at?: string | null
  answer?: WorkspaceAnswerHydration
}

export interface Workspace {
  id: string
  name: string
  description?: string | null
  scope: QueryScope
  created_at?: string | null
  updated_at?: string | null
  version: number
  blocks?: WorkspaceBlock[]
}

export interface WorkspaceCreateRequest {
  name: string
  description?: string | null
  scope?: QueryScope
}

export interface WorkspacePatchRequest {
  version: number
  name?: string
  description?: string | null
  scope?: QueryScope
}

export interface BlockCreateRequest {
  block_type: WorkspaceBlockType
  content: WorkspaceBlockContent
  position?: number | null
}

export interface BlockPatchRequest {
  content?: WorkspaceBlockContent
  position?: number | null
}

export type WorkspaceExportFormat = 'markdown' | 'json' | 'csv'

export interface WorkspaceManifestRow {
  source_id: string
  source_type: string
  date?: string | null
  sender?: string | null
  subject_or_filename?: string | null
  excerpt_hash?: string | null
}
