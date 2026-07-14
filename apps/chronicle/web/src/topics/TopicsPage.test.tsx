import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { TopicsPage } from './TopicsPage'
import { HierarchyView } from './HierarchyView'
import { RiverView } from './RiverView'
import { MatrixView } from './MatrixView'
import { ProjectionView } from './ProjectionView'
import type {
  TopicMatrixResponse,
  TopicProjectionResponse,
  TopicRiverResponse,
  TopicTreeNode,
} from '../api/types'

const TOPIC_A = '11111111-1111-1111-1111-111111111111'
const TOPIC_B = '22222222-2222-2222-2222-222222222222'
const TOPIC_HIDDEN = '33333333-3333-3333-3333-333333333333'
const TOPIC_CHILD = '44444444-4444-4444-4444-444444444444'

const tree: TopicTreeNode[] = [
  {
    id: TOPIC_A,
    label: 'Kitchen remodel',
    origin: 'automatic',
    member_count: 42,
    hidden: false,
    top_terms: ['kitchen', 'remodel'],
    children: [
      {
        id: TOPIC_CHILD,
        label: 'Cabinets',
        origin: 'curated',
        member_count: 8,
        hidden: false,
        top_terms: ['cabinet'],
        children: [],
      },
    ],
  },
  {
    id: TOPIC_B,
    label: 'Travel plans',
    origin: 'manual',
    member_count: 12,
    hidden: false,
    top_terms: ['travel'],
    children: [],
  },
  {
    id: TOPIC_HIDDEN,
    label: 'Spam cluster',
    origin: 'automatic',
    member_count: 3,
    hidden: true,
    top_terms: ['spam'],
    children: [],
  },
]

function mockFetch(handlers: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      for (const [key, body] of Object.entries(handlers)) {
        if (url.includes(key)) {
          return {
            ok: true,
            status: 200,
            json: async () => body,
          } as Response
        }
      }
      return {
        ok: false,
        status: 404,
        json: async () => ({ detail: 'not found' }),
      } as Response
    }),
  )
}

function renderPage(initial = '/topics') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/topics" element={<TopicsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('TopicsPage / Topic Atlas views', () => {
  beforeEach(() => {
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('defaults to hierarchy view (TA-001)', async () => {
    mockFetch({ '/api/topics': { topics: tree } })
    renderPage('/topics')
    expect(await screen.findByTestId('hierarchy-view')).toBeInTheDocument()
    expect(screen.getByTestId('topic-view-hierarchy')).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('hierarchy shows origin badges and member counts', async () => {
    mockFetch({ '/api/topics': { topics: tree } })
    renderPage('/topics')
    await screen.findByTestId('hierarchy-view')
    const badges = screen.getAllByTestId('topic-origin-badge')
    expect(badges.some((b) => b.textContent?.match(/Automatic/i))).toBe(true)
    expect(badges.some((b) => b.textContent?.match(/Manual/i))).toBe(true)
    expect(screen.getAllByTestId('topic-member-count').length).toBeGreaterThan(0)
  })

  it('hidden topics under collapsed Hidden (N) group', async () => {
    mockFetch({ '/api/topics': { topics: tree } })
    renderPage('/topics')
    await screen.findByTestId('topic-hidden-group')
    expect(screen.getByTestId('topic-hidden-group')).toHaveTextContent(/Hidden \(1\)/)
    expect(screen.queryByTestId(`topic-treeitem-${TOPIC_HIDDEN}`)).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('topic-hidden-group'))
    expect(screen.getByTestId(`topic-treeitem-${TOPIC_HIDDEN}`)).toBeInTheDocument()
  })

  it('keyboard arrow-right expands into children (WAI-ARIA tree)', async () => {
    const onSelect = vi.fn()
    render(
      <HierarchyView topics={tree} selectedId={null} onSelect={onSelect} />,
    )
    const treeEl = screen.getByTestId('topic-tree')
    treeEl.focus()
    // Focus first node (Kitchen) which has children
    fireEvent.keyDown(treeEl, { key: 'ArrowRight' })
    // Expanded — child should appear
    expect(await screen.findByTestId(`topic-treeitem-${TOPIC_CHILD}`)).toBeInTheDocument()
    fireEvent.keyDown(treeEl, { key: 'ArrowDown' })
    fireEvent.keyDown(treeEl, { key: 'Enter' })
    expect(onSelect).toHaveBeenCalled()
  })

  it('selecting a node sets selection kind topic', async () => {
    mockFetch({ '/api/topics': { topics: tree } })
    renderPage('/topics')
    await screen.findByTestId(`topic-treeitem-${TOPIC_A}`)
    fireEvent.click(screen.getByTestId(`topic-treeitem-${TOPIC_A}`))
    await waitFor(() => {
      const sel = useWorkingSetStore.getState().selection
      expect(sel).toEqual({ kind: 'topic', topicId: TOPIC_A })
    })
  })

  it('river normalize legend and absolute/normalized toggle', () => {
    const data: TopicRiverResponse = {
      unit: 'month',
      mode_hint: 'absolute',
      from: '2020-01-01T00:00:00Z',
      to: '2021-01-01T00:00:00Z',
      topics: [
        {
          topic_id: TOPIC_A,
          label: 'Kitchen',
          origin: 'automatic',
          buckets: [
            { bucket: '2020-01-01T00:00:00Z', count: 10 },
            { bucket: '2020-02-01T00:00:00Z', count: 5 },
          ],
        },
      ],
    }
    render(
      <RiverView data={data} selectedId={null} onSelect={vi.fn()} />,
    )
    expect(screen.getByTestId('river-legend')).toHaveTextContent(/absolute/i)
    fireEvent.click(screen.getByRole('button', { name: /Normalized/i }))
    expect(screen.getByTestId('river-legend')).toHaveTextContent(/share/i)
  })

  it('matrix sort and normalize-per-row', () => {
    const data: TopicMatrixResponse = {
      by: 'year',
      columns: ['2020', '2021'],
      rows: [
        {
          topic_id: TOPIC_A,
          label: 'Alpha',
          origin: 'automatic',
          cells: { '2020': 10, '2021': 0 },
          row_total: 10,
        },
        {
          topic_id: TOPIC_B,
          label: 'Beta',
          origin: 'curated',
          cells: { '2020': 2, '2021': 8 },
          row_total: 10,
        },
      ],
      column_totals: { '2020': 12, '2021': 8 },
      grand_total: 20,
    }
    render(
      <MatrixView data={data} selectedId={null} onSelect={vi.fn()} />,
    )
    expect(screen.getByTestId('matrix-view')).toBeInTheDocument()
    // Default sort row_total desc — both equal, labels stable enough
    fireEvent.click(screen.getByTestId('matrix-sort-label'))
    const rows = screen.getAllByTestId(/matrix-row-/)
    expect(rows[0]).toHaveTextContent('Alpha')
    fireEvent.click(screen.getByTestId('matrix-normalize-toggle'))
    // 10/10 = 1.00 for Alpha 2020
    expect(screen.getByTestId(`matrix-cell-${TOPIC_A}-2020`)).toHaveTextContent('1.00')
  })

  it('projection disclaimer and focusable points (TA-003)', () => {
    const data: TopicProjectionResponse = {
      points: [
        {
          topic_id: TOPIC_A,
          label: 'Kitchen',
          origin: 'automatic',
          member_count: 40,
          x: 0.5,
          y: -0.2,
        },
      ],
      excluded_without_centroid: 1,
      note: '1 topic(s) excluded; topic-level only (TA-003).',
    }
    render(
      <ProjectionView data={data} selectedId={null} onSelect={vi.fn()} />,
    )
    expect(screen.getByTestId('projection-disclaimer')).toHaveTextContent(
      /Exploratory projection/i,
    )
    const point = screen.getByTestId(`projection-point-${TOPIC_A}`)
    expect(point).toHaveAttribute('tabindex', '0')
    expect(point).toHaveAttribute('aria-label')
    // No source points — only topic circles.
    expect(screen.queryByTestId(/projection-source/)).not.toBeInTheDocument()
  })

  it('tv param selects river view', async () => {
    mockFetch({
      '/api/topics/river': {
        unit: 'month',
        mode_hint: 'absolute',
        from: null,
        to: null,
        topics: [],
      },
      '/api/topics': { topics: tree },
    })
    renderPage('/topics?tv=river')
    expect(await screen.findByTestId('river-view')).toBeInTheDocument()
  })
})

describe('HierarchyView filter', () => {
  it('filters tree client-side', () => {
    render(
      <HierarchyView topics={tree} selectedId={null} onSelect={vi.fn()} />,
    )
    fireEvent.change(screen.getByTestId('topic-tree-filter'), {
      target: { value: 'Travel' },
    })
    expect(screen.getByTestId(`topic-treeitem-${TOPIC_B}`)).toBeInTheDocument()
    expect(screen.queryByTestId(`topic-treeitem-${TOPIC_A}`)).not.toBeInTheDocument()
  })
})
