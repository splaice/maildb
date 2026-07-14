/**
 * People & Organizations API helpers (authenticated fetch wrappers).
 */

import { apiFetch, apiGet } from '../api/client'
import type {
  ContactCard,
  ContactPatchRequest,
  MergeCandidatesResponse,
  MergeRequest,
  PeopleListResponse,
  UnmergeRequest,
  UnmergeResponse,
} from '../api/types'

function toSearch(
  params: Record<string, string | number | boolean | undefined | null>,
): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === '' || v === false) continue
    sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export function listPeople(
  opts: {
    q?: string
    kind?: string
    needs_review?: boolean
    limit?: number
    cursor?: string | null
  } = {},
  signal?: AbortSignal,
): Promise<PeopleListResponse> {
  const q = toSearch({
    q: opts.q,
    kind: opts.kind,
    needs_review: opts.needs_review ? true : undefined,
    limit: opts.limit ?? 50,
    cursor: opts.cursor ?? undefined,
  })
  return apiGet<PeopleListResponse>(`/api/people${q}`, signal)
}

export function getPerson(
  id: string,
  signal?: AbortSignal,
): Promise<ContactCard> {
  return apiGet<ContactCard>(`/api/people/${encodeURIComponent(id)}`, signal)
}

export function patchPerson(
  id: string,
  body: ContactPatchRequest,
  signal?: AbortSignal,
): Promise<ContactCard> {
  return apiFetch<ContactCard>(`/api/people/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
    signal,
  })
}

export function mergePeople(
  body: MergeRequest,
  signal?: AbortSignal,
): Promise<ContactCard> {
  return apiFetch<ContactCard>('/api/people/merge', {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
  })
}

export function unmergePeople(
  body: UnmergeRequest,
  signal?: AbortSignal,
): Promise<UnmergeResponse> {
  return apiFetch<UnmergeResponse>('/api/people/unmerge', {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
  })
}

export function listMergeCandidates(
  limit = 20,
  signal?: AbortSignal,
): Promise<MergeCandidatesResponse> {
  return apiGet<MergeCandidatesResponse>(
    `/api/people/merge-candidates${toSearch({ limit })}`,
    signal,
  )
}
