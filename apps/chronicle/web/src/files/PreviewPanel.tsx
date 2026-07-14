import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiGet, ApiError } from '../api/client'
import type { AttachmentSource, SourceResponse } from '../api/types'
import { downloadUrl, previewUrl } from './format'

export interface PreviewPanelProps {
  attSid: string
  filename: string
  onClose: () => void
}

function isAttachment(src: SourceResponse): src is AttachmentSource {
  return src.kind === 'att'
}

export function PreviewPanel({ attSid, filename, onClose }: PreviewPanelProps) {
  const [wide, setWide] = useState(
    () => typeof window !== 'undefined' && window.innerWidth >= 900,
  )
  const [previewKind, setPreviewKind] = useState<
    'image' | 'pdf' | 'text' | 'denied' | 'loading'
  >('loading')
  const [denyReason, setDenyReason] = useState<string | null>(null)
  const [textBody, setTextBody] = useState<string | null>(null)

  const sourceQuery = useQuery({
    queryKey: ['sources', attSid],
    queryFn: ({ signal }) => apiGet<SourceResponse>(`/api/sources/${attSid}`, signal),
    retry: false,
  })

  useEffect(() => {
    const onResize = () => setWide(window.innerWidth >= 900)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    let cancelled = false
    const url = previewUrl(attSid)
    setPreviewKind('loading')
    setDenyReason(null)
    setTextBody(null)

    void (async () => {
      try {
        const res = await fetch(url, { credentials: 'include' })
        if (cancelled) return
        if (res.status === 415) {
          const body = (await res.json()) as { reason?: string }
          setPreviewKind('denied')
          setDenyReason(body.reason ?? 'Preview unavailable')
          return
        }
        if (!res.ok) {
          setPreviewKind('denied')
          setDenyReason(`HTTP ${res.status}`)
          return
        }
        const ct = (res.headers.get('content-type') || '').toLowerCase()
        if (ct.startsWith('image/')) {
          setPreviewKind('image')
          return
        }
        if (ct.includes('pdf')) {
          setPreviewKind('pdf')
          return
        }
        if (ct.startsWith('text/')) {
          const text = await res.text()
          if (!cancelled) {
            setTextBody(text)
            setPreviewKind('text')
          }
          return
        }
        setPreviewKind('denied')
        setDenyReason('Unsupported preview type')
      } catch {
        if (!cancelled) {
          setPreviewKind('denied')
          setDenyReason('Failed to load preview')
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [attSid])

  const att =
    sourceQuery.data && isAttachment(sourceQuery.data) ? sourceQuery.data : null
  const extracted = att?.markdown ?? null
  const url = previewUrl(attSid)

  return (
    <div
      className="absolute inset-0 z-20 flex flex-col bg-graphite-950/95 p-3"
      data-testid="preview-panel"
      role="dialog"
      aria-label={`Preview ${filename}`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="truncate text-sm font-medium text-text-primary">{filename}</h2>
        <div className="flex gap-1.5">
          <a
            href={downloadUrl(attSid)}
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
            data-testid="preview-download"
          >
            Download original
          </a>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
            data-testid="preview-close"
          >
            Close
          </button>
        </div>
      </div>

      <div
        className={
          wide && extracted
            ? 'grid min-h-0 flex-1 grid-cols-2 gap-3'
            : 'flex min-h-0 flex-1 flex-col gap-3'
        }
      >
        <div
          className="min-h-0 flex-1 overflow-auto rounded border border-steel bg-graphite-900 p-2"
          data-testid="preview-canvas"
        >
          {previewKind === 'loading' ? (
            <p className="text-text-muted">Loading preview…</p>
          ) : previewKind === 'image' ? (
            <img
              src={url}
              alt={filename}
              className="max-h-full max-w-full object-contain"
              data-testid="preview-image"
            />
          ) : previewKind === 'pdf' ? (
            <iframe
              sandbox=""
              src={url}
              title={filename}
              className="h-full min-h-[320px] w-full border-0"
              data-testid="preview-pdf"
            />
          ) : previewKind === 'text' ? (
            <pre
              className="whitespace-pre-wrap font-mono text-[12px] text-text-primary"
              data-testid="preview-text"
            >
              {textBody}
            </pre>
          ) : (
            <div data-testid="preview-fallback" className="space-y-2">
              <p className="text-conflict">
                Preview unavailable{denyReason ? `: ${denyReason}` : ''}
              </p>
              <p className="text-[11px] text-text-muted">
                Type: {att?.content_type || '—'} · Size:{' '}
                {att?.size != null ? att.size.toLocaleString() : '—'}
              </p>
              {extracted ? (
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-text-primary">
                  {extracted}
                </pre>
              ) : sourceQuery.isError ? (
                <p className="text-text-muted">
                  {(sourceQuery.error as ApiError)?.message ||
                    'Could not load extracted text'}
                </p>
              ) : (
                <p className="text-text-muted">No extracted text available</p>
              )}
            </div>
          )}
        </div>

        {wide && extracted && previewKind !== 'denied' ? (
          <div
            className="min-h-0 overflow-auto rounded border border-steel bg-graphite-900 p-2"
            data-testid="preview-extracted-side"
          >
            <h3 className="mb-1 text-[11px] font-medium text-text-muted">
              Extracted text
            </h3>
            <pre className="whitespace-pre-wrap font-mono text-[11px] text-text-primary">
              {extracted}
            </pre>
          </div>
        ) : null}
      </div>
    </div>
  )
}
