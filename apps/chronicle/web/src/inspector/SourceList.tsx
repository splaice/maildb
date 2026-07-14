import { useInfiniteQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { apiPost } from '../api/client'
import type { QueryScope, SourceListItem, SourceListResponse } from '../api/types'

export interface SourceListProps {
  scope: QueryScope
  dateFrom: string
  dateTo: string
  onSelectMessage: (sid: string) => void
}

function formatListDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return iso
  return d.toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, 'Z')
}

function senderLabel(item: SourceListItem): string {
  if (item.sender_name && item.sender_address) {
    return `${item.sender_name} <${item.sender_address}>`
  }
  return item.sender_name || item.sender_address || '—'
}

export function SourceList({
  scope,
  dateFrom,
  dateTo,
  onSelectMessage,
}: SourceListProps) {
  const query = useInfiniteQuery({
    queryKey: ['sources', 'list', dateFrom, dateTo, JSON.stringify(scope)],
    queryFn: ({ pageParam, signal }) =>
      apiPost<SourceListResponse>(
        '/api/sources/list',
        {
          scope,
          date_from: dateFrom,
          date_to: dateTo,
          cursor: pageParam ?? null,
          limit: 50,
        },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: false,
  })

  const items = useMemo(
    () => query.data?.pages.flatMap((p) => p.items) ?? [],
    [query.data?.pages],
  )

  if (query.isLoading) {
    return (
      <div className="space-y-2" data-testid="source-list-skeleton" aria-busy="true">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-10 animate-pulse rounded bg-graphite-800" />
        ))}
      </div>
    )
  }

  if (query.isError) {
    return (
      <div role="alert" className="text-conflict">
        <p className="mb-2">Failed to load sources</p>
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

  if (items.length === 0) {
    return <p className="text-text-muted">No messages in this period</p>
  }

  return (
    <div data-testid="source-list" className="flex min-h-0 flex-1 flex-col">
      <ul className="min-h-0 flex-1 space-y-0.5 overflow-auto">
        {items.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              onClick={() => onSelectMessage(item.id)}
              className="flex w-full flex-col gap-0.5 rounded px-1.5 py-1.5 text-left hover:bg-graphite-800 focus-visible:bg-graphite-800"
              data-testid={`source-row-${item.id}`}
            >
              <div className="flex items-center gap-2">
                <span className="tabular-nums font-mono text-[11px] text-text-muted">
                  {formatListDate(item.date)}
                </span>
                {item.has_attachment || item.attachment_count > 0 ? (
                  <span
                    className="rounded bg-attachment/20 px-1 text-[10px] text-attachment"
                    title={`${item.attachment_count} attachment(s)`}
                  >
                    att{item.attachment_count > 0 ? ` · ${item.attachment_count}` : ''}
                  </span>
                ) : null}
              </div>
              <span className="truncate text-text-primary">
                {item.subject || '(no subject)'}
              </span>
              <span className="truncate text-[11px] text-text-muted">
                {senderLabel(item)}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {query.hasNextPage ? (
        <button
          type="button"
          onClick={() => void query.fetchNextPage()}
          disabled={query.isFetchingNextPage}
          className="mt-2 w-full rounded-md border border-steel bg-graphite-800 px-2 py-1.5 text-text-primary"
          data-testid="source-list-load-more"
        >
          {query.isFetchingNextPage ? 'Loading…' : 'Load more'}
        </button>
      ) : null}
    </div>
  )
}
