/**
 * Workspace API helpers (authenticated fetch wrappers).
 */

import { apiFetch, apiGet, apiPost } from '../api/client'
import type {
  BlockCreateRequest,
  BlockPatchRequest,
  Workspace,
  WorkspaceBlock,
  WorkspaceCreateRequest,
  WorkspaceExportRequest,
  WorkspaceExportReview,
  WorkspaceListResponse,
  WorkspacePatchRequest,
} from '../api/types'

export function listWorkspaces(signal?: AbortSignal): Promise<WorkspaceListResponse> {
  return apiGet<WorkspaceListResponse>('/api/workspaces', signal)
}

export function getWorkspace(id: string, signal?: AbortSignal): Promise<Workspace> {
  return apiGet<Workspace>(`/api/workspaces/${encodeURIComponent(id)}`, signal)
}

export function createWorkspace(
  body: WorkspaceCreateRequest,
  signal?: AbortSignal,
): Promise<Workspace> {
  return apiPost<Workspace>('/api/workspaces', body, signal)
}

export function patchWorkspace(
  id: string,
  body: WorkspacePatchRequest,
  signal?: AbortSignal,
): Promise<Workspace> {
  return apiFetch<Workspace>(`/api/workspaces/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
    signal,
  })
}

export function deleteWorkspace(id: string, signal?: AbortSignal): Promise<void> {
  return apiFetch<void>(`/api/workspaces/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    signal,
  })
}

export function createBlock(
  workspaceId: string,
  body: BlockCreateRequest,
  signal?: AbortSignal,
): Promise<WorkspaceBlock> {
  return apiPost<WorkspaceBlock>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/blocks`,
    body,
    signal,
  )
}

export function patchBlock(
  workspaceId: string,
  blockId: string,
  body: BlockPatchRequest,
  signal?: AbortSignal,
): Promise<WorkspaceBlock> {
  return apiFetch<WorkspaceBlock>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/blocks/${encodeURIComponent(blockId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(body),
      signal,
    },
  )
}

export function deleteBlock(
  workspaceId: string,
  blockId: string,
  signal?: AbortSignal,
): Promise<void> {
  return apiFetch<void>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/blocks/${encodeURIComponent(blockId)}`,
    {
      method: 'DELETE',
      signal,
    },
  )
}

export type ExportWorkspaceResult =
  | {
      type: 'file'
      blob: Blob
      filename: string
      fingerprint: string | null
    }
  | {
      type: 'review'
      review: WorkspaceExportReview
    }
  | {
      type: 'reauth-required'
    }

function isReauthRequiredBody(body: unknown): boolean {
  if (!body || typeof body !== 'object') return false
  const b = body as Record<string, unknown>
  if (b.reason === 'reauth-required') return true
  const detail = b.detail
  if (detail && typeof detail === 'object' && (detail as { reason?: string }).reason === 'reauth-required') {
    return true
  }
  return false
}

function isReviewBody(body: unknown): body is WorkspaceExportReview {
  return (
    !!body &&
    typeof body === 'object' &&
    (body as WorkspaceExportReview).review === true
  )
}

/** POST export: file download, redaction review, or reauth-required. */
export async function exportWorkspace(
  workspaceId: string,
  request: WorkspaceExportRequest,
  signal?: AbortSignal,
): Promise<ExportWorkspaceResult> {
  const url = `/api/workspaces/${encodeURIComponent(workspaceId)}/export`
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
    signal,
  })

  if (response.status === 401) {
    let body: unknown
    try {
      body = await response.json()
    } catch {
      body = undefined
    }
    if (isReauthRequiredBody(body)) {
      return { type: 'reauth-required' }
    }
    throw new Error('Export unauthorized')
  }

  if (!response.ok) {
    throw new Error(`Export failed: HTTP ${response.status}`)
  }

  const contentType = response.headers.get('Content-Type') || ''
  const disposition = response.headers.get('Content-Disposition') || ''
  const isAttachment = /attachment/i.test(disposition)

  if (!isAttachment && contentType.includes('application/json')) {
    const body: unknown = await response.json()
    if (isReviewBody(body)) {
      return { type: 'review', review: body }
    }
    // JSON export file without attachment header — treat as file
    const blob = new Blob([JSON.stringify(body)], { type: 'application/json' })
    return {
      type: 'file',
      blob,
      filename: `workspace.json`,
      fingerprint: response.headers.get('X-Manifest-Fingerprint'),
    }
  }

  const match = /filename="([^"]+)"/.exec(disposition)
  const format = request.format
  const filename =
    match?.[1] ||
    `workspace.${format === 'markdown' ? 'md' : format}`
  const fingerprint = response.headers.get('X-Manifest-Fingerprint')
  const blob = await response.blob()
  return { type: 'file', blob, filename, fingerprint }
}

/** @deprecated Use exportWorkspace; kept for call-site migration. */
export async function exportWorkspaceBlob(
  workspaceId: string,
  format: WorkspaceExportRequest['format'],
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string; fingerprint: string | null }> {
  const result = await exportWorkspace(workspaceId, { format }, signal)
  if (result.type !== 'file') {
    throw new Error(`Unexpected export result: ${result.type}`)
  }
  return {
    blob: result.blob,
    filename: result.filename,
    fingerprint: result.fingerprint,
  }
}

export function triggerBlobDownload(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(href)
}
