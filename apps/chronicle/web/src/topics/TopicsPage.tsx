import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router'

import type { QueryScope } from '../api/types'
import { toIsoSeconds } from '../workingset/urlState'
import type { TopicViewMode } from '../workingset/urlState'
import { parseTopicView } from '../workingset/urlState'
import { useWorkingSetStore } from '../workingset/store'
import {
  getTopicMatrix,
  getTopicProjection,
  getTopicRiver,
  listTopics,
} from './api'
import { HierarchyView } from './HierarchyView'
import { MatrixView } from './MatrixView'
import { ProjectionView } from './ProjectionView'
import { RiverView } from './RiverView'

const VIEWS: { id: TopicViewMode; label: string }[] = [
  { id: 'hierarchy', label: 'Hierarchy' },
  { id: 'river', label: 'River' },
  { id: 'matrix', label: 'Matrix' },
  { id: 'projection', label: 'Projection' },
]

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[12px] text-text-primary enabled:hover:bg-graphite-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

/**
 * Topic Atlas lens (config rail + canvas + inspector selection).
 * Working set (scope bar) is untouched — G-002.
 */
export function TopicsPage() {
  const scope = useWorkingSetStore((s) => s.scope)
  const viewport = useWorkingSetStore((s) => s.viewport)
  const setSelection = useWorkingSetStore((s) => s.setSelection)
  const [searchParams, setSearchParams] = useSearchParams()

  const topicView = parseTopicView(searchParams.get('tv'))
  const tsel = searchParams.get('tsel')

  const setTopicView = useCallback(
    (view: TopicViewMode) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (view === 'hierarchy') next.delete('tv')
          else next.set('tv', view)
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const selectTopic = useCallback(
    (id: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set('tsel', id)
          return next
        },
        { replace: true },
      )
      setSelection({ kind: 'topic', topicId: id })
    },
    [setSearchParams, setSelection],
  )

  const listQuery = useQuery({
    queryKey: ['topics', 'list', true],
    queryFn: ({ signal }) => listTopics(true, signal),
    retry: false,
  })

  const riverRange = useMemo(() => {
    if (viewport && viewport.toMs > viewport.fromMs) {
      return {
        from: toIsoSeconds(viewport.fromMs),
        to: toIsoSeconds(viewport.toMs),
      }
    }
    // Fall back to scope date or last decade.
    const from = scope.date?.from
      ? `${scope.date.from}T00:00:00Z`
      : toIsoSeconds(Date.UTC(2010, 0, 1))
    const to = scope.date?.to
      ? `${scope.date.to}T00:00:00Z`
      : toIsoSeconds(Date.now())
    return { from, to }
  }, [viewport, scope.date?.from, scope.date?.to])

  const riverQuery = useQuery({
    queryKey: ['topics', 'river', riverRange, scope],
    queryFn: ({ signal }) =>
      getTopicRiver(
        {
          from: riverRange.from,
          to: riverRange.to,
          unit: 'auto',
          top: 8,
          scope: scope as QueryScope,
        },
        signal,
      ),
    enabled: topicView === 'river',
    retry: false,
  })

  const matrixQuery = useQuery({
    queryKey: ['topics', 'matrix', scope],
    queryFn: ({ signal }) => getTopicMatrix({ by: 'year', scope }, signal),
    enabled: topicView === 'matrix',
    retry: false,
  })

  const projectionQuery = useQuery({
    queryKey: ['topics', 'projection'],
    queryFn: ({ signal }) => getTopicProjection(signal),
    enabled: topicView === 'projection',
    retry: false,
  })

  return (
    <div
      className="flex h-full min-h-0 flex-col gap-3"
      data-testid="topics-page"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="text-sm font-medium text-text-primary">Topic Atlas</h1>
        <div
          className="inline-flex flex-wrap gap-1"
          role="tablist"
          aria-label="Topic Atlas view"
          data-testid="topic-view-switch"
        >
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              role="tab"
              aria-selected={topicView === v.id}
              className={`${btnClass} ${
                topicView === v.id ? 'ring-1 ring-action' : ''
              }`}
              onClick={() => setTopicView(v.id)}
              data-testid={`topic-view-${v.id}`}
            >
              {v.label}
            </button>
          ))}
        </div>
      </header>

      <div className="min-h-0 flex-1">
        {topicView === 'hierarchy' && (
          listQuery.isLoading ? (
            <p className="text-[12px] text-text-muted">Loading topics…</p>
          ) : listQuery.isError ? (
            <p role="alert" className="text-conflict">
              Failed to load topics
            </p>
          ) : (
            <HierarchyView
              topics={listQuery.data?.topics ?? []}
              selectedId={tsel}
              onSelect={selectTopic}
            />
          )
        )}
        {topicView === 'river' && (
          <RiverView
            data={riverQuery.data}
            loading={riverQuery.isLoading}
            selectedId={tsel}
            onSelect={selectTopic}
          />
        )}
        {topicView === 'matrix' && (
          <MatrixView
            data={matrixQuery.data}
            loading={matrixQuery.isLoading}
            selectedId={tsel}
            onSelect={selectTopic}
          />
        )}
        {topicView === 'projection' && (
          <ProjectionView
            data={projectionQuery.data}
            loading={projectionQuery.isLoading}
            selectedId={tsel}
            onSelect={selectTopic}
          />
        )}
      </div>
    </div>
  )
}
