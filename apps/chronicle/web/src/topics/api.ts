/**
 * Topics Atlas API helpers (authenticated fetch wrappers).
 */

import { apiFetch, apiGet } from '../api/client'
import type {
  TopicDetail,
  TopicListResponse,
  TopicMatrixResponse,
  TopicMembersResponse,
  TopicPatchRequest,
  TopicProjectionResponse,
  TopicRiverResponse,
} from '../api/types'
import type { QueryScope } from '../api/types'

function scopeQuery(scope: QueryScope | undefined): Record<string, string> {
  const q: Record<string, string> = {}
  if (!scope) return q
  if (scope.mailboxes?.length) q.mb = scope.mailboxes.join(',')
  if (scope.senders?.length) q.sd = scope.senders.join(',')
  if (scope.date?.from) q.df = scope.date.from
  if (scope.date?.to) q.dt = scope.date.to
  return q
}

function toSearch(params: Record<string, string | number | undefined | null>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === '') continue
    sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export function listTopics(
  includeHidden = true,
  signal?: AbortSignal,
): Promise<TopicListResponse> {
  const q = includeHidden ? '?include_hidden=true' : ''
  return apiGet<TopicListResponse>(`/api/topics${q}`, signal)
}

export function getTopic(id: string, signal?: AbortSignal): Promise<TopicDetail> {
  return apiGet<TopicDetail>(`/api/topics/${encodeURIComponent(id)}`, signal)
}

export function patchTopic(
  id: string,
  body: TopicPatchRequest,
  signal?: AbortSignal,
): Promise<TopicDetail> {
  return apiFetch<TopicDetail>(`/api/topics/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
    signal,
  })
}

export function getTopicRiver(
  opts: {
    from: string
    to: string
    unit?: string
    top?: number
    scope?: QueryScope
  },
  signal?: AbortSignal,
): Promise<TopicRiverResponse> {
  const q = toSearch({
    from: opts.from,
    to: opts.to,
    unit: opts.unit ?? 'auto',
    top: opts.top ?? 8,
    ...scopeQuery(opts.scope),
  })
  return apiGet<TopicRiverResponse>(`/api/topics/river${q}`, signal)
}

export function getTopicMatrix(
  opts: { by?: string; scope?: QueryScope } = {},
  signal?: AbortSignal,
): Promise<TopicMatrixResponse> {
  const q = toSearch({
    by: opts.by ?? 'year',
    ...scopeQuery(opts.scope),
  })
  return apiGet<TopicMatrixResponse>(`/api/topics/matrix${q}`, signal)
}

export function getTopicProjection(
  signal?: AbortSignal,
): Promise<TopicProjectionResponse> {
  return apiGet<TopicProjectionResponse>('/api/topics/projection', signal)
}

export function listTopicMembers(
  id: string,
  opts: { cursor?: string | null; limit?: number } = {},
  signal?: AbortSignal,
): Promise<TopicMembersResponse> {
  const q = toSearch({
    cursor: opts.cursor ?? undefined,
    limit: opts.limit ?? 50,
  })
  return apiGet<TopicMembersResponse>(
    `/api/topics/${encodeURIComponent(id)}/members${q}`,
    signal,
  )
}
