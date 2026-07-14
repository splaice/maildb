import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useInfiniteQuery } from '@tanstack/react-query'

import { apiPost } from '../api/client'
import type {
  AttachmentListItem,
  AttachmentListRequest,
  AttachmentListResponse,
  ContentTypeFamily,
} from '../api/types'
import { useWorkingSetStore } from '../workingset/store'
import type { FilesViewMode } from '../workingset/urlState'
import {
  contentTypeFamily,
  formatBytes,
  isImageFamily,
  previewUrl,
  truncateFilename,
} from './format'

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

const FAMILIES: { value: '' | ContentTypeFamily; label: string }[] = [
  { value: '', label: 'All types' },
  { value: 'pdf', label: 'PDF' },
  { value: 'image', label: 'Image' },
  { value: 'spreadsheet', label: 'Spreadsheet' },
  { value: 'document', label: 'Document' },
  { value: 'text', label: 'Text' },
  { value: 'other', label: 'Other' },
]

const STATUSES: { value: string; label: string }[] = [
  { value: '', label: 'All statuses' },
  { value: 'extracted', label: 'Extracted' },
  { value: 'failed', label: 'Failed' },
  { value: 'pending', label: 'Pending' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'extracting', label: 'Extracting' },
]

function ExtractionStatus({ item }: { item: AttachmentListItem }) {
  const status = item.extraction?.status || 'pending'
  const reason = item.extraction?.reason
  const failed = status === 'failed'
  return (
    <span
      className={failed ? 'text-conflict' : 'text-text-muted'}
      title={reason || status}
      data-testid={`extraction-${item.id}`}
    >
      {failed ? (
        <>
          <span className="font-medium">failed</span>
          {reason ? `: ${reason}` : ''}
        </>
      ) : (
        status
      )}
    </span>
  )
}

function FilesTable({
  items,
  groupDuplicates,
  expanded,
  onToggleExpand,
  onSelect,
}: {
  items: AttachmentListItem[]
  groupDuplicates: boolean
  expanded: Set<string>
  onToggleExpand: (id: string) => void
  onSelect: (item: AttachmentListItem) => void
}) {
  return (
    <div className="overflow-auto" data-testid="files-table">
      <table className="w-full border-collapse text-left text-[12px]">
        <thead className="sticky top-0 bg-graphite-900 text-text-muted">
          <tr className="border-b border-steel">
            <th className="px-2 py-1.5 font-medium">Filename</th>
            <th className="px-2 py-1.5 font-medium">Type</th>
            <th className="px-2 py-1.5 font-medium">Size</th>
            <th className="px-2 py-1.5 font-medium">Date</th>
            <th className="px-2 py-1.5 font-medium">Sender</th>
            <th className="px-2 py-1.5 font-medium">Source</th>
            <th className="px-2 py-1.5 font-medium">Extraction</th>
            <th className="px-2 py-1.5 font-medium">Dup</th>
          </tr>
        </thead>
        <tbody className="tabular-nums font-mono text-text-primary">
          {items.map((item) => {
            const isExpanded = expanded.has(item.id)
            const failed = item.extraction?.status === 'failed'
            return (
              <FragmentRow
                key={item.id}
                item={item}
                groupDuplicates={groupDuplicates}
                isExpanded={isExpanded}
                failed={failed}
                onToggleExpand={onToggleExpand}
                onSelect={onSelect}
              />
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function FragmentRow({
  item,
  groupDuplicates,
  isExpanded,
  failed,
  onToggleExpand,
  onSelect,
}: {
  item: AttachmentListItem
  groupDuplicates: boolean
  isExpanded: boolean
  failed: boolean
  onToggleExpand: (id: string) => void
  onSelect: (item: AttachmentListItem) => void
}) {
  const sender = item.sender_name || item.sender_address || '—'
  return (
    <>
      <tr
        className="cursor-pointer border-b border-steel/60 hover:bg-graphite-900"
        data-testid={`file-row-${item.id}`}
        onClick={() => onSelect(item)}
      >
        <td className="max-w-[14rem] px-2 py-1 font-sans" title={item.filename}>
          <span className="text-text-primary">
            {truncateFilename(item.filename)}
          </span>
          {failed ? (
            <Link
              to="/data-health"
              className="ml-2 text-[10px] text-action underline"
              onClick={(e) => e.stopPropagation()}
              data-testid={`data-health-link-${item.id}`}
            >
              Open in Data Health
            </Link>
          ) : null}
        </td>
        <td className="px-2 py-1 font-sans text-text-muted">
          {contentTypeFamily(item.content_type)}
        </td>
        <td className="px-2 py-1">{formatBytes(item.size)}</td>
        <td className="px-2 py-1 tabular-nums">
          {item.date ? item.date.slice(0, 10) : '—'}
        </td>
        <td className="max-w-[8rem] truncate px-2 py-1 font-sans" title={sender}>
          {sender}
        </td>
        <td className="max-w-[12rem] truncate px-2 py-1 font-sans">
          <button
            type="button"
            className="text-left text-action underline"
            title={item.source_subject || '(no subject)'}
            onClick={(e) => {
              e.stopPropagation()
              onSelect(item)
            }}
          >
            {item.source_subject || '(no subject)'}
          </button>
        </td>
        <td className="px-2 py-1 font-sans">
          <ExtractionStatus item={item} />
        </td>
        <td className="px-2 py-1">
          {item.duplicate_count > 1 ? (
            <button
              type="button"
              className={btnClass}
              data-testid={`dup-badge-${item.id}`}
              onClick={(e) => {
                e.stopPropagation()
                if (groupDuplicates) onToggleExpand(item.id)
                else onSelect(item)
              }}
            >
              ×{item.duplicate_count}
            </button>
          ) : (
            <span className="text-text-muted">—</span>
          )}
        </td>
      </tr>
      {groupDuplicates && isExpanded && item.occurrences ? (
        <tr data-testid={`dup-expand-${item.id}`}>
          <td colSpan={8} className="bg-graphite-900 px-4 py-2 font-sans">
            <p className="mb-1 text-[11px] font-medium text-text-muted">
              Occurrences (exact duplicates)
            </p>
            <ul className="space-y-1">
              {item.occurrences.map((occ) => (
                <li key={occ.id} className="text-[11px]">
                  <button
                    type="button"
                    className="text-action underline"
                    data-testid={`occ-${occ.id}`}
                    onClick={() =>
                      useWorkingSetStore.getState().setSelection({
                        kind: 'message',
                        sid: occ.id,
                      })
                    }
                  >
                    {occ.subject || '(no subject)'}
                  </button>
                  <span className="text-text-muted">
                    {' '}
                    · {occ.sender || '—'} · {occ.date?.slice(0, 10) || '—'}
                  </span>
                </li>
              ))}
            </ul>
          </td>
        </tr>
      ) : null}
    </>
  )
}

function FilesGallery({
  items,
  onSelect,
}: {
  items: AttachmentListItem[]
  onSelect: (item: AttachmentListItem) => void
}) {
  const images = items.filter((it) => isImageFamily(it.content_type))
  const excluded = items.length - images.length

  return (
    <div data-testid="files-gallery">
      {excluded > 0 ? (
        <p className="mb-2 text-[11px] text-text-muted" data-testid="gallery-excluded">
          Showing {images.length} image{images.length === 1 ? '' : 's'}; {excluded}{' '}
          non-image file{excluded === 1 ? '' : 's'} excluded from gallery.
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
        {images.map((item) => (
          <GalleryCard key={item.id} item={item} onSelect={onSelect} />
        ))}
      </div>
      {images.length === 0 ? (
        <p className="text-text-muted" data-testid="gallery-empty">
          No image attachments in this view
        </p>
      ) : null}
    </div>
  )
}

function GalleryCard({
  item,
  onSelect,
}: {
  item: AttachmentListItem
  onSelect: (item: AttachmentListItem) => void
}) {
  const [broken, setBroken] = useState(false)
  return (
    <button
      type="button"
      className="flex flex-col overflow-hidden rounded border border-steel bg-graphite-900 text-left"
      data-testid={`gallery-card-${item.id}`}
      onClick={() => onSelect(item)}
    >
      <div className="flex h-28 w-full items-center justify-center bg-graphite-950 p-1">
        {broken ? (
          <span
            className="text-2xl text-text-muted"
            aria-hidden
            data-testid={`gallery-placeholder-${item.id}`}
          >
            📄
          </span>
        ) : (
          <img
            loading="lazy"
            src={previewUrl(item.id)}
            alt={item.filename}
            className="h-full w-full object-contain"
            onError={() => setBroken(true)}
          />
        )}
      </div>
      <div className="truncate px-1.5 py-1 font-sans text-[11px] text-text-primary" title={item.filename}>
        {truncateFilename(item.filename, 28)}
      </div>
      <div className="px-1.5 pb-1.5 font-mono text-[10px] tabular-nums text-text-muted">
        {item.date ? item.date.slice(0, 10) : '—'}
      </div>
    </button>
  )
}

export function FilesPage() {
  const scope = useWorkingSetStore((s) => s.scope)
  const setSelection = useWorkingSetStore((s) => s.setSelection)
  const [searchParams, setSearchParams] = useSearchParams()

  const filesView: FilesViewMode =
    searchParams.get('fv') === 'gallery' ? 'gallery' : 'table'
  const filesQuery = searchParams.get('fq') ?? ''

  const [filenameDraft, setFilenameDraft] = useState(filesQuery)
  const [family, setFamily] = useState<'' | ContentTypeFamily>('')
  const [status, setStatus] = useState('')
  const [groupDuplicates, setGroupDuplicates] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  // Keep draft in sync when URL fq changes (back/forward).
  useEffect(() => {
    setFilenameDraft(filesQuery)
  }, [filesQuery])

  const setFilesView = useCallback(
    (view: FilesViewMode) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (view === 'gallery') next.set('fv', 'gallery')
          else next.delete('fv')
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const commitFilename = useCallback(() => {
    const q = filenameDraft.trim()
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (q) next.set('fq', q)
        else next.delete('fq')
        return next
      },
      { replace: true },
    )
  }, [filenameDraft, setSearchParams])

  const listBody = useMemo((): AttachmentListRequest => {
    return {
      scope,
      filters: {
        filename: filesQuery || null,
        content_type_family: family || null,
        status: status || null,
      },
      limit: 50,
      group_duplicates: groupDuplicates,
    }
  }, [scope, filesQuery, family, status, groupDuplicates])

  const query = useInfiniteQuery({
    queryKey: ['attachments', 'list', listBody],
    queryFn: async ({ pageParam, signal }) => {
      const body: AttachmentListRequest = {
        ...listBody,
        cursor: pageParam ?? null,
      }
      return apiPost<AttachmentListResponse>('/api/attachments/list', body, signal)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  })

  const items = useMemo(
    () => query.data?.pages.flatMap((p) => p.items) ?? [],
    [query.data],
  )

  const onSelect = useCallback(
    (item: AttachmentListItem) => {
      setSelection({ kind: 'attachment', sid: item.id })
    },
    [setSelection],
  )

  const onToggleExpand = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  return (
    <div className="relative flex h-full min-h-0 flex-col gap-3" data-testid="files-page">
      <header className="flex flex-wrap items-end gap-2" data-testid="files-toolbar">
        <label className="flex flex-col gap-0.5 text-[11px] text-text-muted">
          Filename
          <input
            type="search"
            value={filenameDraft}
            onChange={(e) => setFilenameDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitFilename()
            }}
            onBlur={commitFilename}
            placeholder="Search filename…"
            className="min-w-[10rem] rounded-md border border-steel bg-graphite-900 px-2 py-1 text-text-primary"
            data-testid="files-filename"
          />
        </label>
        <label className="flex flex-col gap-0.5 text-[11px] text-text-muted">
          Type
          <select
            value={family}
            onChange={(e) => setFamily(e.target.value as '' | ContentTypeFamily)}
            className="rounded-md border border-steel bg-graphite-900 px-2 py-1 text-text-primary"
            data-testid="files-family"
          >
            {FAMILIES.map((f) => (
              <option key={f.value || 'all'} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5 text-[11px] text-text-muted">
          Status
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-steel bg-graphite-900 px-2 py-1 text-text-primary"
            data-testid="files-status"
          >
            {STATUSES.map((s) => (
              <option key={s.value || 'all'} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-[12px] text-text-primary">
          <input
            type="checkbox"
            checked={groupDuplicates}
            onChange={(e) => {
              setGroupDuplicates(e.target.checked)
              setExpanded(new Set())
            }}
            data-testid="files-group-dup"
          />
          Group duplicates
        </label>
        <div className="ml-auto flex gap-1" role="group" aria-label="View mode">
          <button
            type="button"
            className={btnClass}
            aria-pressed={filesView === 'table'}
            data-testid="files-view-table"
            onClick={() => setFilesView('table')}
          >
            Table
          </button>
          <button
            type="button"
            className={btnClass}
            aria-pressed={filesView === 'gallery'}
            data-testid="files-view-gallery"
            onClick={() => setFilesView('gallery')}
          >
            Gallery
          </button>
        </div>
      </header>

      {query.isLoading ? (
        <div
          className="animate-pulse space-y-2"
          data-testid="files-skeleton"
          aria-busy="true"
        >
          <div className="h-4 w-1/2 rounded bg-graphite-800" />
          <div className="h-24 rounded bg-graphite-800" />
        </div>
      ) : query.isError ? (
        <div role="alert" className="text-conflict" data-testid="files-error">
          Failed to load attachments
          <button type="button" className={`${btnClass} ml-2`} onClick={() => void query.refetch()}>
            Retry
          </button>
        </div>
      ) : filesView === 'gallery' ? (
        <FilesGallery items={items} onSelect={onSelect} />
      ) : (
        <FilesTable
          items={items}
          groupDuplicates={groupDuplicates}
          expanded={expanded}
          onToggleExpand={onToggleExpand}
          onSelect={onSelect}
        />
      )}

      {query.hasNextPage ? (
        <div>
          <button
            type="button"
            className={btnClass}
            data-testid="files-load-more"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {query.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </button>
        </div>
      ) : null}
    </div>
  )
}

export default FilesPage
