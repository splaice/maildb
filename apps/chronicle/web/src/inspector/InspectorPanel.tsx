import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { apiGet } from '../api/client'
import type { AttachmentSource, SourceResponse } from '../api/types'
import { downloadUrl } from '../files/format'
import { PreviewPanel } from '../files/PreviewPanel'
import { useWorkingSetStore } from '../workingset/store'
import { formatPeriodLabel, UNIT_MS, type Unit } from '../chronicle/timeScale'
import { MessageCard } from './MessageCard'
import { SourceList } from './SourceList'

export interface InspectorPanelProps {
  /** Message count for the selected bucket when known. */
  bucketCount?: number | null
}

function unitMs(unit: string | null | undefined): number {
  if (unit && unit in UNIT_MS) return UNIT_MS[unit as Unit]
  return UNIT_MS.month
}

function bucketLabel(bucketIso: string): string {
  const ms = Date.parse(bucketIso)
  if (!Number.isFinite(ms)) return bucketIso
  return formatPeriodLabel(ms)
}

function isAttachment(src: SourceResponse): src is AttachmentSource {
  return src.kind === 'att'
}

function AttachmentCard({ sid, onClose }: { sid: string; onClose: () => void }) {
  const [showPreview, setShowPreview] = useState(false)
  const query = useQuery({
    queryKey: ['sources', sid],
    queryFn: ({ signal }) => apiGet<SourceResponse>(`/api/sources/${sid}`, signal),
    retry: false,
  })

  if (query.isLoading) {
    return (
      <div className="space-y-2" data-testid="attachment-card-skeleton" aria-busy="true">
        <div className="h-4 w-3/4 animate-pulse rounded bg-graphite-800" />
        <div className="h-3 w-full animate-pulse rounded bg-graphite-800" />
      </div>
    )
  }

  if (query.isError || !query.data) {
    return (
      <div role="alert" className="text-conflict">
        <p className="mb-2">Failed to load attachment</p>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
        >
          Retry
        </button>
      </div>
    )
  }

  if (!isAttachment(query.data)) {
    return <p className="text-text-muted">Not an attachment source</p>
  }

  const att = query.data
  const env = att.source_envelope

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="attachment-card">
      <div>
        <h3 className="text-sm font-medium text-text-primary">{att.filename}</h3>
        <p className="mt-1 font-mono text-[11px] text-text-muted">{att.id}</p>
        <p className="text-[11px] text-text-muted">
          Type: {att.content_type || '—'}
        </p>
        <p className="tabular-nums text-[11px] text-text-muted">
          Size: {att.size != null ? att.size.toLocaleString() : '—'}
        </p>
        <p className="text-[11px] text-text-muted">
          Extraction: {att.extraction_status || '—'}
          {att.extraction_reason ? ` (${att.extraction_reason})` : ''}
        </p>
        {att.markdown ? (
          <pre
            className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-steel bg-graphite-900 p-1.5 font-mono text-[11px] text-text-primary"
            data-testid="attachment-extracted"
          >
            {att.markdown}
          </pre>
        ) : null}
        {env ? (
          <p className="mt-1 text-[11px] text-text-muted">
            Source: {env.subject || '(no subject)'} ·{' '}
            {env.sender_name || env.sender_address || '—'}
          </p>
        ) : att.source_message_id ? (
          <p className="mt-1 text-[11px] text-text-muted">
            Source message: {att.source_message_id}
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={() => setShowPreview(true)}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="attachment-preview"
        >
          Preview
        </button>
        <a
          href={downloadUrl(sid)}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="attachment-download"
        >
          Download original
        </a>
        <Link
          to={`/source/${encodeURIComponent(sid)}`}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="open-full-source"
        >
          Open full source
        </Link>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
          data-testid="attachment-close"
        >
          Close
        </button>
      </div>
      {showPreview ? (
        <div className="fixed inset-0 z-50" data-testid="inspector-preview-host">
          <PreviewPanel
            attSid={sid}
            filename={att.filename}
            onClose={() => setShowPreview(false)}
          />
        </div>
      ) : null}
    </div>
  )
}

export function InspectorPanel({ bucketCount }: InspectorPanelProps) {
  const selection = useWorkingSetStore((s) => s.selection)
  const scope = useWorkingSetStore((s) => s.scope)
  const unit = useWorkingSetStore((s) => s.timelineUnit)
  const setSelection = useWorkingSetStore((s) => s.setSelection)

  if (!selection) {
    return (
      <p className="text-text-muted" data-testid="inspector-empty">
        Select a mark to inspect evidence
      </p>
    )
  }

  if (selection.kind === 'message') {
    return (
      <MessageCard
        sid={selection.sid}
        onClose={() => {
          // Spec: Close clears back to the parent bucket selection.
          useWorkingSetStore.getState().clearMessageToBucket()
        }}
      />
    )
  }

  if (selection.kind === 'attachment') {
    return (
      <AttachmentCard
        sid={selection.sid}
        onClose={() => setSelection(null)}
      />
    )
  }

  // Bucket selection
  const { bucketIso, lane } = selection
  const dateFrom = bucketIso
  const dateTo = new Date(Date.parse(bucketIso) + unitMs(unit)).toISOString()

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="inspector-bucket">
      <div>
        <h3 className="text-sm font-medium text-text-primary">
          {lane} · {bucketLabel(bucketIso)}
        </h3>
        {bucketCount != null ? (
          <p className="tabular-nums text-[11px] text-text-muted">
            {bucketCount.toLocaleString()} in bucket
          </p>
        ) : null}
      </div>
      <p className="text-[11px] font-medium text-text-muted">Open as list</p>
      <SourceList
        scope={scope}
        dateFrom={dateFrom}
        dateTo={dateTo}
        onSelectMessage={(sid) => setSelection({ kind: 'message', sid })}
      />
    </div>
  )
}
