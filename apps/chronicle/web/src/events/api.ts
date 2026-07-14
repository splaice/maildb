/**
 * Events API helpers (authenticated fetch wrappers).
 */

import { apiFetch, apiGet, apiPost } from '../api/client'
import type {
  ChronicleEvent,
  EventAdoptRequest,
  EventCreateRequest,
  EventListRequest,
  EventListResponse,
  EventPatchRequest,
  EventVersionsResponse,
  SourceContext,
} from '../api/types'

export function getEvent(id: string, signal?: AbortSignal): Promise<ChronicleEvent> {
  return apiGet<ChronicleEvent>(`/api/events/${encodeURIComponent(id)}`, signal)
}

export function createEvent(
  body: EventCreateRequest,
  signal?: AbortSignal,
): Promise<ChronicleEvent> {
  return apiPost<ChronicleEvent>('/api/events', body, signal)
}

export function patchEvent(
  id: string,
  body: EventPatchRequest,
  signal?: AbortSignal,
): Promise<ChronicleEvent> {
  return apiFetch<ChronicleEvent>(`/api/events/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
    signal,
  })
}

export function deleteEvent(id: string, signal?: AbortSignal): Promise<void> {
  return apiFetch<void>(`/api/events/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    signal,
  })
}

export function listEvents(
  body: EventListRequest,
  signal?: AbortSignal,
): Promise<EventListResponse> {
  return apiPost<EventListResponse>('/api/events/list', body, signal)
}

export function getEventVersions(
  id: string,
  signal?: AbortSignal,
): Promise<EventVersionsResponse> {
  return apiGet<EventVersionsResponse>(
    `/api/events/${encodeURIComponent(id)}/versions`,
    signal,
  )
}

export function adoptEventVersion(
  id: string,
  version: number,
  body: EventAdoptRequest,
  signal?: AbortSignal,
): Promise<ChronicleEvent> {
  return apiPost<ChronicleEvent>(
    `/api/events/${encodeURIComponent(id)}/adopt/${version}`,
    body,
    signal,
  )
}

/** Freshness check: re-fetch excerpt at stored location (§12.2). */
export function getSourceContext(
  sid: string,
  start: number,
  end: number,
  signal?: AbortSignal,
): Promise<SourceContext> {
  const q = new URLSearchParams({
    start: String(start),
    end: String(end),
  })
  return apiGet<SourceContext>(
    `/api/sources/${encodeURIComponent(sid)}/context?${q.toString()}`,
    signal,
  )
}
