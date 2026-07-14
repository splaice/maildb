import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

import type { TopicDetail, TopicMemberEnvelope } from '../api/types'
import { encodeState, toIsoSeconds } from '../workingset/urlState'
import { useWorkingSetStore } from '../workingset/store'
import { getTopic, listTopicMembers, patchTopic } from './api'
import { OriginBadge } from './originBadge'

export interface TopicCardProps {
  topicId: string
  onClose?: () => void
}

const RENAME_TOOLTIP =
  'Renaming an automatic topic flips its origin to curated. Manual changes take precedence over regeneration (Table 21 / TA-002).'

/**
 * Inspector topic card: rename (→ curated), hide/unhide, activity sparkline,
 * representative members → source selection, definitive member list (TA-004),
 * Open in Chronicle (viewport + topics lane).
 *
 * "Open sources in Research" is deferred: Research Desk topic scoping needs a
 * QueryScope.topics field (forbidden in scope.py this task). Instead, Open
 * sources opens the paginated member list inside the Atlas via
 * GET /api/topics/{id}/members (TA-004 definitive list). Phase 5 polish adds
 * Research-lens topic scoping.
 */
export function TopicCard({ topicId, onClose }: TopicCardProps) {
  const setSelection = useWorkingSetStore((s) => s.setSelection)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [labelDraft, setLabelDraft] = useState('')
  const [showMembers, setShowMembers] = useState(false)
  const [memberItems, setMemberItems] = useState<TopicMemberEnvelope[]>([])
  const [memberCursor, setMemberCursor] = useState<string | null>(null)
  const [membersLoading, setMembersLoading] = useState(false)

  const query = useQuery({
    queryKey: ['topics', topicId],
    queryFn: ({ signal }) => getTopic(topicId, signal),
    retry: false,
  })

  useEffect(() => {
    if (query.data) setLabelDraft(query.data.label)
  }, [query.data])

  const patchMut = useMutation({
    mutationFn: (body: { label?: string; hidden?: boolean; description?: string | null }) =>
      patchTopic(topicId, body),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ['topics'] })
      void qc.setQueryData(['topics', topicId], data)
      setEditing(false)
    },
  })

  const detail = query.data

  const loadMembers = useCallback(
    async (cursor: string | null, append: boolean) => {
      setMembersLoading(true)
      try {
        const res = await listTopicMembers(topicId, { cursor, limit: 25 })
        setMemberItems((prev) => (append ? [...prev, ...res.items] : res.items))
        setMemberCursor(res.next_cursor)
      } finally {
        setMembersLoading(false)
      }
    },
    [topicId],
  )

  const openMembers = useCallback(() => {
    setShowMembers(true)
    void loadMembers(null, false)
  }, [loadMembers])

  const openInChronicle = useCallback(
    (d: TopicDetail) => {
      // MVP: set viewport from activity date extent and enable topics lane.
      // Topics are not a QueryScope field yet.
      const activity = d.activity ?? []
      let fromMs: number
      let toMs: number
      if (activity.length > 0) {
        const times = activity
          .map((a) => Date.parse(a.bucket))
          .filter((t) => Number.isFinite(t))
        fromMs = Math.min(...times)
        toMs = Math.max(...times) + 30 * 24 * 3600 * 1000 // pad one month
      } else {
        const now = Date.now()
        fromMs = now - 365 * 24 * 3600 * 1000
        toMs = now
      }
      const scope = useWorkingSetStore.getState().scope
      const params = encodeState({
        scope,
        viewport: { fromMs, toMs },
        aggregation: 'auto',
        view: 'canvas',
        selection: null,
        lanes: ['messages', 'topics'],
      })
      // Ensure vf/vt present even if encode uses second precision.
      if (!params.get('vf')) params.set('vf', toIsoSeconds(fromMs))
      if (!params.get('vt')) params.set('vt', toIsoSeconds(toMs))
      const qs = params.toString()
      void navigate({ pathname: '/', search: qs ? `?${qs}` : '' })
    },
    [navigate],
  )

  const sparkline = useMemo(() => {
    if (!detail?.activity?.length) return null
    const counts = detail.activity.map((a) => a.count)
    const max = Math.max(...counts, 1)
    return counts.map((c, i) => ({
      i,
      h: Math.max(2, (c / max) * 28),
    }))
  }, [detail?.activity])

  if (query.isLoading) {
    return (
      <div className="space-y-2" data-testid="topic-card-skeleton" aria-busy="true">
        <div className="h-4 w-3/4 animate-pulse rounded bg-graphite-800" />
        <div className="h-3 w-full animate-pulse rounded bg-graphite-800" />
      </div>
    )
  }

  if (query.isError || !detail) {
    return (
      <div role="alert" className="text-conflict" data-testid="topic-card-error">
        <p className="mb-2">Failed to load topic</p>
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

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="topic-card">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="flex flex-wrap items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault()
                const label = labelDraft.trim()
                if (!label || label === detail.label) {
                  setEditing(false)
                  return
                }
                patchMut.mutate({ label })
              }}
              data-testid="topic-rename-form"
            >
              <input
                value={labelDraft}
                onChange={(e) => setLabelDraft(e.target.value)}
                className="min-w-0 flex-1 rounded border border-steel bg-graphite-900 px-1.5 py-0.5 text-sm text-text-primary"
                data-testid="topic-rename-input"
                autoFocus
                aria-label="Topic label"
              />
              <button
                type="submit"
                className="rounded border border-steel bg-graphite-800 px-1.5 py-0.5 text-[11px]"
                disabled={patchMut.isPending}
              >
                Save
              </button>
              <button
                type="button"
                className="rounded border border-steel px-1.5 py-0.5 text-[11px]"
                onClick={() => {
                  setLabelDraft(detail.label)
                  setEditing(false)
                }}
              >
                Cancel
              </button>
            </form>
          ) : (
            <button
              type="button"
              className="text-left text-sm font-medium text-text-primary hover:underline"
              onClick={() => setEditing(true)}
              title={detail.origin === 'automatic' ? RENAME_TOOLTIP : 'Rename topic'}
              data-testid="topic-label"
            >
              {detail.label}
            </button>
          )}
          {detail.origin === 'automatic' || detail.origin === 'curated' ? (
            <p
              className="mt-0.5 text-[10px] text-text-muted"
              title={RENAME_TOOLTIP}
              data-testid="topic-rename-tooltip"
            >
              {detail.origin === 'automatic'
                ? 'Rename flips origin to curated'
                : 'Curated — manual changes preserved on regenerate'}
            </p>
          ) : null}
        </div>
        <OriginBadge origin={detail.origin} />
      </div>

      {detail.description ? (
        <p className="text-[11px] text-text-muted" data-testid="topic-description">
          {detail.description}
        </p>
      ) : null}

      <p className="tabular-nums text-[11px] text-text-muted" data-testid="topic-card-count">
        {detail.member_count.toLocaleString()} members
      </p>

      {detail.top_terms?.length ? (
        <p className="text-[11px] text-text-muted" data-testid="topic-top-terms">
          Terms: {detail.top_terms.join(', ')}
        </p>
      ) : null}

      {sparkline ? (
        <div
          className="flex h-8 items-end gap-px"
          aria-label="Topic activity sparkline"
          data-testid="topic-activity-sparkline"
        >
          {sparkline.map((s) => (
            <div
              key={s.i}
              className="flex-1 rounded-sm bg-topic"
              style={{ height: s.h, backgroundColor: '#A78BFA', opacity: 0.8 }}
            />
          ))}
        </div>
      ) : null}

      <div>
        <p className="mb-1 text-[11px] font-medium text-text-muted">Representative members</p>
        <ul className="max-h-32 space-y-1 overflow-auto" data-testid="topic-representatives">
          {(detail.members ?? []).map((m) => (
            <li key={m.id}>
              <button
                type="button"
                className="w-full truncate rounded px-1 py-0.5 text-left text-[11px] text-text-primary hover:bg-graphite-800"
                onClick={() => setSelection({ kind: 'message', sid: m.id })}
                data-testid={`topic-member-${m.id}`}
              >
                <span className="font-medium">{m.subject || '(no subject)'}</span>
                <span className="ml-1 text-text-muted">
                  {m.sender_name || m.sender_address || ''}
                </span>
              </button>
            </li>
          ))}
          {(detail.members ?? []).length === 0 ? (
            <li className="text-[11px] text-text-muted">No members</li>
          ) : null}
        </ul>
      </div>

      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary"
          onClick={() => patchMut.mutate({ hidden: !detail.hidden })}
          disabled={patchMut.isPending}
          data-testid="topic-hide-toggle"
        >
          {detail.hidden ? 'Unhide' : 'Hide'}
        </button>
        <button
          type="button"
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary"
          onClick={() => openInChronicle(detail)}
          data-testid="topic-open-chronicle"
        >
          Open in Chronicle
        </button>
        <button
          type="button"
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary"
          onClick={openMembers}
          data-testid="topic-open-sources"
          title="Opens definitive member list in Atlas (TA-004). Research topic scoping is Phase 5."
        >
          Open sources
        </button>
        {onClose ? (
          <button
            type="button"
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary"
            onClick={onClose}
            data-testid="topic-close"
          >
            Close
          </button>
        ) : null}
      </div>

      {showMembers ? (
        <div
          className="mt-1 flex min-h-0 flex-1 flex-col gap-1 border-t border-steel pt-2"
          data-testid="topic-member-list"
        >
          <p className="text-[11px] font-medium text-text-muted">
            All members (definitive list · TA-004)
          </p>
          <ul className="max-h-48 space-y-1 overflow-auto">
            {memberItems.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  className="w-full truncate rounded px-1 py-0.5 text-left text-[11px] hover:bg-graphite-800"
                  onClick={() => setSelection({ kind: 'message', sid: m.id })}
                  data-testid={`topic-list-member-${m.id}`}
                >
                  {m.subject || '(no subject)'}
                </button>
              </li>
            ))}
          </ul>
          {memberCursor ? (
            <button
              type="button"
              className="rounded border border-steel px-2 py-1 text-[11px]"
              disabled={membersLoading}
              onClick={() => void loadMembers(memberCursor, true)}
              data-testid="topic-members-more"
            >
              {membersLoading ? 'Loading…' : 'Load more'}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
