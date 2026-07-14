/**
 * Events API helpers (authenticated fetch wrappers).
 */

import { apiFetch, apiGet, apiPost } from '../api/client'
import type {
  ChronicleEvent,
  EventCreateRequest,
  EventPatchRequest,
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
