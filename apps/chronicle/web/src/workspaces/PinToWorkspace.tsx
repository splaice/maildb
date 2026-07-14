/**
 * Pin a source (message/attachment) to a workspace — menu of workspaces + create-inline.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { apiGet } from '../api/client'
import type { SourceResponse, WorkspaceListItem } from '../api/types'
import { createBlock, createWorkspace, listWorkspaces } from './api'

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

export interface PinToWorkspaceProps {
  sourceId: string
  sourceType: 'message' | 'attachment'
  /** Optional pre-filled metadata; when omitted, loaded from /api/sources/:id */
  title?: string | null
  date?: string | null
  sender?: string | null
  excerpt?: string | null
}

function metaFromSource(src: SourceResponse): {
  title: string
  date: string | null
  sender: string | null
} {
  if (src.kind === 'msg') {
    return {
      title: src.envelope.subject || '(no subject)',
      date: src.envelope.date,
      sender: src.envelope.sender_name || src.envelope.sender_address,
    }
  }
  return {
    title: src.filename || src.id,
    date: src.source_envelope?.date ?? null,
    sender:
      src.source_envelope?.sender_name ||
      src.source_envelope?.sender_address ||
      null,
  }
}

export function PinToWorkspace({
  sourceId,
  sourceType,
  title: titleProp,
  date: dateProp,
  sender: senderProp,
  excerpt = null,
}: PinToWorkspaceProps) {
  const [open, setOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()

  const listQuery = useQuery({
    queryKey: ['workspaces'],
    queryFn: ({ signal }) => listWorkspaces(signal),
    enabled: open,
    retry: false,
  })

  const sourceQuery = useQuery({
    queryKey: ['sources', sourceId],
    queryFn: ({ signal }) => apiGet<SourceResponse>(`/api/sources/${sourceId}`, signal),
    enabled: open && (titleProp == null || dateProp == null || senderProp == null),
    retry: false,
  })

  async function resolveMeta(): Promise<{
    title: string
    date: string | null
    sender: string | null
  }> {
    if (titleProp != null && dateProp !== undefined && senderProp !== undefined) {
      return {
        title: titleProp || sourceId,
        date: dateProp ?? null,
        sender: senderProp ?? null,
      }
    }
    if (sourceQuery.data) return metaFromSource(sourceQuery.data)
    const src = await apiGet<SourceResponse>(`/api/sources/${sourceId}`)
    return metaFromSource(src)
  }

  async function pinTo(workspaceId: string) {
    setBusy(true)
    setError(null)
    setStatus(null)
    try {
      const meta = await resolveMeta()
      await createBlock(workspaceId, {
        block_type: 'pin',
        content: {
          source_id: sourceId,
          source_type: sourceType,
          title: meta.title,
          date: meta.date,
          sender: meta.sender,
          excerpt,
        },
      })
      setStatus('Pinned')
      void qc.invalidateQueries({ queryKey: ['workspaces'] })
      void qc.invalidateQueries({ queryKey: ['workspace', workspaceId] })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Pin failed')
    } finally {
      setBusy(false)
    }
  }

  async function createAndPin() {
    const name = newName.trim()
    if (!name) return
    setBusy(true)
    setError(null)
    try {
      const ws = await createWorkspace({ name, scope: {} })
      await pinTo(ws.id)
      setNewName('')
      void listQuery.refetch()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Create failed')
      setBusy(false)
    }
  }

  const items: WorkspaceListItem[] = listQuery.data?.items ?? []

  return (
    <div className="relative" data-testid="pin-to-workspace">
      <button
        type="button"
        className={btnClass}
        onClick={() => {
          setOpen((v) => !v)
          setStatus(null)
          setError(null)
        }}
        data-testid="pin-to-workspace-btn"
      >
        Pin to workspace
      </button>
      {open ? (
        <div
          className="absolute left-0 z-20 mt-1 min-w-[14rem] rounded-md border border-steel bg-graphite-900 p-2 shadow-lg"
          data-testid="pin-workspace-menu"
          role="menu"
        >
          {listQuery.isLoading ? (
            <p className="text-[11px] text-text-muted">Loading…</p>
          ) : items.length === 0 ? (
            <p className="mb-1 text-[11px] text-text-muted">No workspaces yet</p>
          ) : (
            <ul className="mb-2 max-h-40 space-y-0.5 overflow-auto">
              {items.map((w) => (
                <li key={w.id}>
                  <button
                    type="button"
                    className="w-full rounded px-1.5 py-1 text-left text-[12px] text-text-primary hover:bg-graphite-800"
                    disabled={busy}
                    onClick={() => void pinTo(w.id)}
                    data-testid={`pin-workspace-${w.id}`}
                    role="menuitem"
                  >
                    {w.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex gap-1 border-t border-steel pt-2">
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="New workspace"
              className="min-w-0 flex-1 rounded border border-steel bg-graphite-950 px-1.5 py-0.5 text-[12px] text-text-primary"
              data-testid="pin-new-workspace-name"
              disabled={busy}
            />
            <button
              type="button"
              className={btnClass}
              disabled={busy || !newName.trim()}
              onClick={() => void createAndPin()}
              data-testid="pin-create-workspace"
            >
              Create
            </button>
          </div>
          {status ? (
            <p className="mt-1 text-[11px] text-action" data-testid="pin-status">
              {status}
            </p>
          ) : null}
          {error ? (
            <p className="mt-1 text-[11px] text-conflict" role="alert">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
