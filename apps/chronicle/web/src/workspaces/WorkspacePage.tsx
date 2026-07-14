/**
 * /workspaces/:id — notebook layout: blocks, reorder, export, open scope.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { ApiError } from '../api/client'
import type {
  PinBlockContent,
  WorkspaceBlock,
  WorkspaceExportFormat,
} from '../api/types'
import { renderAnswerWithCitations } from '../ask/citationText'
import type { AskCitationEvent } from '../ask/sseClient'
import { useWorkingSetStore } from '../workingset/store'
import {
  createBlock,
  deleteBlock,
  exportWorkspaceBlob,
  getWorkspace,
  patchBlock,
  patchWorkspace,
  triggerBlobDownload,
} from './api'

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

function isPinContent(c: unknown): c is PinBlockContent {
  return (
    typeof c === 'object' &&
    c != null &&
    'source_id' in c &&
    typeof (c as PinBlockContent).source_id === 'string'
  )
}

function NoteBlockView({
  block,
  workspaceId,
  onConflict,
}: {
  block: WorkspaceBlock
  workspaceId: string
  onConflict: () => void
}) {
  const text = String((block.content as { text?: string }).text ?? '')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(text)
  const qc = useQueryClient()

  const save = useCallback(async () => {
    if (draft === text) {
      setEditing(false)
      return
    }
    try {
      await patchBlock(workspaceId, block.id, { content: { text: draft } })
      void qc.invalidateQueries({ queryKey: ['workspace', workspaceId] })
      setEditing(false)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) onConflict()
    }
  }, [block.id, draft, onConflict, qc, text, workspaceId])

  if (editing) {
    return (
      <textarea
        className="w-full min-h-[4rem] rounded border border-steel bg-graphite-950 p-2 text-sm text-text-primary"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => void save()}
        autoFocus
        data-testid={`note-edit-${block.id}`}
      />
    )
  }

  return (
    <div
      className="cursor-text whitespace-pre-wrap rounded border border-transparent px-1 py-0.5 text-sm text-text-primary hover:border-steel"
      onClick={() => {
        setDraft(text)
        setEditing(true)
      }}
      data-testid={`note-view-${block.id}`}
    >
      {text || <span className="text-text-muted">Empty note</span>}
    </div>
  )
}

function HeadingBlockView({
  block,
  workspaceId,
}: {
  block: WorkspaceBlock
  workspaceId: string
}) {
  const text = String((block.content as { text?: string }).text ?? '')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(text)
  const qc = useQueryClient()

  if (editing) {
    return (
      <input
        className="w-full rounded border border-steel bg-graphite-950 px-2 py-1 text-base font-medium text-text-primary"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          void (async () => {
            if (draft !== text) {
              await patchBlock(workspaceId, block.id, {
                content: { text: draft },
              })
              void qc.invalidateQueries({ queryKey: ['workspace', workspaceId] })
            }
            setEditing(false)
          })()
        }}
        autoFocus
        data-testid={`heading-edit-${block.id}`}
      />
    )
  }

  return (
    <h2
      className="cursor-text text-base font-medium text-text-primary"
      onClick={() => {
        setDraft(text)
        setEditing(true)
      }}
      data-testid={`heading-view-${block.id}`}
    >
      {text || 'Heading'}
    </h2>
  )
}

function PinBlockView({ block }: { block: WorkspaceBlock }) {
  const c = isPinContent(block.content) ? block.content : null
  if (!c) return <p className="text-text-muted">Invalid pin</p>
  return (
    <div
      className="rounded border border-steel bg-graphite-950 px-2 py-1.5"
      data-testid={`pin-block-${block.id}`}
    >
      <Link
        to={`/source/${encodeURIComponent(c.source_id)}`}
        className="text-sm font-medium text-action hover:underline"
        data-testid={`pin-link-${block.id}`}
      >
        {c.title || c.source_id}
      </Link>
      <p className="font-mono text-[10px] text-text-muted">{c.source_id}</p>
      <p className="text-[11px] text-text-muted">
        {c.date || '—'} · {c.sender || '—'}
      </p>
      {c.excerpt ? (
        <blockquote className="mt-1 border-l-2 border-steel pl-2 text-[12px] text-text-muted whitespace-pre-wrap">
          {c.excerpt}
        </blockquote>
      ) : null}
    </div>
  )
}

function AnswerBlockView({ block }: { block: WorkspaceBlock }) {
  const answer = block.answer
  const text = answer?.answer_text || ''
  const citations: AskCitationEvent[] = (answer?.citations || []).map((c) => ({
    marker: c.marker.startsWith('[') ? c.marker : `[${c.marker}]`,
    source_id: c.source_id,
    source_type: c.source_type,
    excerpt: c.excerpt || '',
    location: c.location ?? null,
  }))

  return (
    <div
      className="rounded border border-steel bg-graphite-950 p-2"
      data-testid={`answer-block-${block.id}`}
    >
      <div
        className="whitespace-pre-wrap text-sm text-text-primary"
        data-testid={`answer-text-${block.id}`}
      >
        {text
          ? renderAnswerWithCitations(text, citations, () => {
              /* read-only chips */
            })
          : (
            <span className="text-text-muted">No answer text</span>
          )}
      </div>
      {citations.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1" data-testid={`answer-citations-${block.id}`}>
          {citations.map((c) => (
            <Link
              key={`${c.marker}-${c.source_id}`}
              to={`/source/${encodeURIComponent(c.source_id)}`}
              className="rounded border border-steel px-1.5 py-0.5 font-mono text-[10px] text-action"
            >
              {c.marker} {c.source_id}
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export function WorkspacePage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const setScope = useWorkingSetStore((s) => s.setScope)
  const [conflict, setConflict] = useState(false)
  const [nameDraft, setNameDraft] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

  const wsQuery = useQuery({
    queryKey: ['workspace', id],
    queryFn: ({ signal }) => getWorkspace(id!, signal),
    enabled: Boolean(id),
    retry: false,
  })

  const onConflict = useCallback(() => {
    setConflict(true)
  }, [])

  const reload = () => {
    setConflict(false)
    void wsQuery.refetch()
  }

  const addBlock = useMutation({
    mutationFn: (body: { block_type: 'heading' | 'note'; content: { text: string } }) =>
      createBlock(id!, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['workspace', id] }),
  })

  const delBlock = useMutation({
    mutationFn: (blockId: string) => deleteBlock(id!, blockId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['workspace', id] }),
  })

  const moveBlock = useMutation({
    mutationFn: async ({ block, dir }: { block: WorkspaceBlock; dir: -1 | 1 }) => {
      const blocks = wsQuery.data?.blocks ?? []
      const sorted = [...blocks].sort((a, b) => a.position - b.position)
      const idx = sorted.findIndex((b) => b.id === block.id)
      const target = idx + dir
      if (idx < 0 || target < 0 || target >= sorted.length) return
      const newPos = sorted[target].position
      await patchBlock(id!, block.id, { position: newPos })
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['workspace', id] }),
  })

  async function saveName() {
    if (!wsQuery.data || nameDraft == null) return
    const name = nameDraft.trim()
    if (!name || name === wsQuery.data.name) {
      setNameDraft(null)
      return
    }
    try {
      await patchWorkspace(id!, {
        version: wsQuery.data.version,
        name,
      })
      setNameDraft(null)
      void qc.invalidateQueries({ queryKey: ['workspace', id] })
      void qc.invalidateQueries({ queryKey: ['workspaces'] })
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        onConflict()
      }
    }
  }

  async function doExport(format: WorkspaceExportFormat) {
    if (!id) return
    setExportError(null)
    try {
      const { blob, filename } = await exportWorkspaceBlob(id, format)
      triggerBlobDownload(blob, filename)
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed')
    }
  }

  function openScopeInChronicle() {
    if (!wsQuery.data) return
    setScope(wsQuery.data.scope || {})
    void navigate('/')
  }

  if (!id) {
    return <p className="p-3 text-conflict">Missing workspace id</p>
  }

  if (wsQuery.isLoading) {
    return (
      <p className="p-3 text-text-muted" data-testid="workspace-loading">
        Loading workspace…
      </p>
    )
  }

  if (wsQuery.isError || !wsQuery.data) {
    return (
      <div className="p-3" role="alert">
        <p className="text-conflict">Failed to load workspace</p>
        <Link to="/workspaces" className="text-action text-sm">
          Back to list
        </Link>
      </div>
    )
  }

  const ws = wsQuery.data
  const blocks = [...(ws.blocks ?? [])].sort((a, b) => a.position - b.position)

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3" data-testid="workspace-page">
      {conflict ? (
        <div
          className="rounded border border-conflict bg-graphite-900 px-3 py-2 text-sm text-conflict"
          role="alert"
          data-testid="version-conflict-banner"
        >
          This workspace was modified elsewhere. Reload to continue.
          <button
            type="button"
            className={`${btnClass} ml-2`}
            onClick={reload}
            data-testid="conflict-reload"
          >
            Reload
          </button>
        </div>
      ) : null}

      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {nameDraft != null ? (
            <input
              className="w-full rounded border border-steel bg-graphite-950 px-2 py-1 text-base font-medium text-text-primary"
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onBlur={() => void saveName()}
              autoFocus
              data-testid="workspace-name-edit"
            />
          ) : (
            <h1
              className="cursor-text text-base font-medium text-text-primary"
              onClick={() => setNameDraft(ws.name)}
              data-testid="workspace-name"
            >
              {ws.name}
            </h1>
          )}
          {ws.description ? (
            <p className="mt-0.5 text-[12px] text-text-muted">{ws.description}</p>
          ) : null}
          <p className="mt-0.5 font-mono text-[10px] text-text-muted">
            v{ws.version}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            className={btnClass}
            onClick={openScopeInChronicle}
            data-testid="open-workspace-scope"
          >
            Open workspace scope in Chronicle
          </button>
          <div className="relative" data-testid="export-menu">
            <details>
              <summary className={`${btnClass} cursor-pointer list-none`}>
                Export
              </summary>
              <div className="absolute right-0 z-10 mt-1 flex flex-col rounded border border-steel bg-graphite-900 p-1 shadow">
                {(['markdown', 'json', 'csv'] as const).map((fmt) => (
                  <button
                    key={fmt}
                    type="button"
                    className="rounded px-2 py-1 text-left text-[12px] text-text-primary hover:bg-graphite-800"
                    data-testid={`export-${fmt}`}
                    onClick={() => void doExport(fmt)}
                  >
                    {fmt}
                  </button>
                ))}
              </div>
            </details>
          </div>
          <Link to="/workspaces" className={btnClass}>
            All workspaces
          </Link>
        </div>
      </header>
      {exportError ? (
        <p className="text-[11px] text-conflict" role="alert">
          {exportError}
        </p>
      ) : null}

      <div className="space-y-3" data-testid="notebook-blocks">
        {blocks.map((block, i) => (
          <article
            key={block.id}
            className="rounded-md border border-steel bg-graphite-900 p-2"
            data-testid={`block-${block.id}`}
            data-block-type={block.block_type}
          >
            <div className="mb-1 flex items-center justify-between gap-1">
              <span className="text-[10px] uppercase tracking-wide text-text-muted">
                {block.block_type}
              </span>
              <div className="flex gap-1">
                <button
                  type="button"
                  className={btnClass}
                  disabled={i === 0 || moveBlock.isPending}
                  onClick={() => moveBlock.mutate({ block, dir: -1 })}
                  data-testid={`block-up-${block.id}`}
                  aria-label="Move up"
                >
                  Up
                </button>
                <button
                  type="button"
                  className={btnClass}
                  disabled={i === blocks.length - 1 || moveBlock.isPending}
                  onClick={() => moveBlock.mutate({ block, dir: 1 })}
                  data-testid={`block-down-${block.id}`}
                  aria-label="Move down"
                >
                  Down
                </button>
                <button
                  type="button"
                  className={btnClass}
                  disabled={delBlock.isPending}
                  onClick={() => delBlock.mutate(block.id)}
                  data-testid={`block-delete-${block.id}`}
                >
                  Delete
                </button>
              </div>
            </div>
            {block.block_type === 'heading' ? (
              <HeadingBlockView block={block} workspaceId={id} />
            ) : null}
            {block.block_type === 'note' ? (
              <NoteBlockView
                block={block}
                workspaceId={id}
                onConflict={onConflict}
              />
            ) : null}
            {block.block_type === 'pin' ? <PinBlockView block={block} /> : null}
            {block.block_type === 'answer' ? (
              <AnswerBlockView block={block} />
            ) : null}
          </article>
        ))}
      </div>

      <div
        className="flex flex-wrap gap-2 border-t border-steel pt-3"
        data-testid="add-block-row"
      >
        <button
          type="button"
          className={btnClass}
          disabled={addBlock.isPending}
          data-testid="add-heading"
          onClick={() =>
            addBlock.mutate({
              block_type: 'heading',
              content: { text: 'Heading' },
            })
          }
        >
          Add heading
        </button>
        <button
          type="button"
          className={btnClass}
          disabled={addBlock.isPending}
          data-testid="add-note"
          onClick={() =>
            addBlock.mutate({
              block_type: 'note',
              content: { text: '' },
            })
          }
        >
          Add note
        </button>
      </div>
    </div>
  )
}
