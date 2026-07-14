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
  WorkspaceExportFormat,
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

/** Download export as a blob via authenticated fetch. */
export async function exportWorkspaceBlob(
  workspaceId: string,
  format: WorkspaceExportFormat,
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string; fingerprint: string | null }> {
  const url = `/api/workspaces/${encodeURIComponent(workspaceId)}/export?format=${format}`
  const response = await fetch(url, {
    method: 'GET',
    credentials: 'include',
    signal,
  })
  if (!response.ok) {
    throw new Error(`Export failed: HTTP ${response.status}`)
  }
  const disposition = response.headers.get('Content-Disposition') || ''
  const match = /filename="([^"]+)"/.exec(disposition)
  const filename =
    match?.[1] ||
    `workspace.${format === 'markdown' ? 'md' : format}`
  const fingerprint = response.headers.get('X-Manifest-Fingerprint')
  const blob = await response.blob()
  return { blob, filename, fingerprint }
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
