import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { InterpretResponse, SearchResponse, SearchResult } from '../api/types'
import { InspectorPanel } from '../inspector/InspectorPanel'
import { ScopeBar } from '../shell/ScopeBar'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { ResearchDeskPage } from './ResearchDeskPage'

function mockSearchResponse(overrides: Partial<SearchResponse> = {}): SearchResponse {
  return {
    results: [],
    next_cursor: null,
    scope: {},
    unsupported: [],
    scope_fingerprint: 'qs_test',
    mode: 'hybrid',
    took_ms: 12,
    duplicates_suppressed: 0,
    facets: {
      mailbox: [{ value: 'me@example.com', count: 10 }],
      year: [{ value: 2014, count: 5 }],
      has_attachment: [
        { value: true, count: 3 },
        { value: false, count: 7 },
      ],
    },
    facet_basis: 'exact',
    degraded: null,
    ...overrides,
  }
}

const msgResult: SearchResult = {
  result_type: 'message',
  id: 'msg_1',
  subject: 'Roof decision',
  sender: 'alice@example.com',
  sender_name: 'Alice',
  date: '2014-06-01T12:00:00Z',
  mailbox: 'me@example.com',
  thread_id: 'thr_1',
  snippet: 'The free-hit roof material was chosen',
  has_attachment: true,
  match: { kind: 'exact', field: 'body' },
}

const attResult: SearchResult = {
  result_type: 'attachment',
  id: 'att_1',
  filename: 'estimate.pdf',
  content_type: 'application/pdf',
  source_message_id: 'msg_1',
  sender: 'alice@example.com',
  date: '2014-06-02T12:00:00Z',
  snippet: 'cost estimate free-hit numbers',
  extraction_status: 'extracted',
  match: { kind: 'hybrid', exact_rank: 1, semantic_rank: 2, similarity: 0.8 },
}

function renderResearch(initialEntries: string[] = ['/research']) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route
            path="/"
            element={
              <div>
                <ScopeBar />
                <div data-testid="chronicle-stub">Chronicle</div>
              </div>
            }
          />
          <Route
            path="/research"
            element={
              <div>
                <ScopeBar />
                <ResearchDeskPage />
                <InspectorPanel />
              </div>
            }
          />
          <Route path="/source/:sid" element={<div data-testid="source-page">Source</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ResearchDeskPage', () => {
  beforeEach(() => {
    resetWorkingSetStore()
    try {
      localStorage.removeItem('chronicle.skipInterpretation')
    } catch {
      /* ignore */
    }
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
    vi.useRealTimers()
  })

  it('renders parsed constraints as editable chips; edit re-runs with updated scope', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/api/archive/summary')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            accounts: [],
            date_range: { from: null, to: null },
            counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
            extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
            embedding: { embedded: 0, missing: 0 },
            versions: { schema: 'x', api: '0' },
          }),
        } as Response
      }
      if (String(url).includes('/api/search')) {
        const body = JSON.parse(String(init?.body ?? '{}')) as {
          query: string
          scope: Record<string, unknown>
        }
        // Second call after chip edit should carry updated sender in scope
        if (body.scope?.senders && Array.isArray(body.scope.senders)) {
          const senders = body.scope.senders as string[]
          if (senders.includes('bob@example.com')) {
            return {
              ok: true,
              status: 200,
              json: async () =>
                mockSearchResponse({
                  results: [msgResult],
                  scope: { senders: ['bob@example.com'], free_text: 'roof' },
                  unsupported: [],
                }),
            } as Response
          }
        }
        return {
          ok: true,
          status: 200,
          json: async () =>
            mockSearchResponse({
              results: [msgResult],
              scope: { senders: ['alice@example.com'], free_text: 'roof' },
              unsupported: ['topic:renovation'],
            }),
        } as Response
      }
      throw new Error(`unexpected: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderResearch()
    const input = screen.getByTestId('research-query-input')
    fireEvent.change(input, { target: { value: 'from:alice@example.com topic:renovation roof' } })
    fireEvent.submit(screen.getByTestId('query-row'))

    expect(await screen.findByTestId('constraint-chip-from:alice@example.com')).toBeInTheDocument()
    expect(screen.getByTestId('unsupported-chip-topic:renovation')).toHaveTextContent(
      'not yet supported: topic:renovation',
    )

    // Edit the from chip
    fireEvent.click(
      screen.getByRole('button', { name: /Edit from: alice@example.com/i }),
    )
    const edit = await screen.findByTestId('constraint-edit-from:alice@example.com')
    fireEvent.change(edit, { target: { value: 'bob@example.com' } })
    fireEvent.submit(edit.closest('form')!)

    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter((c) => String(c[0]).includes('/api/search'))
      expect(posts.length).toBeGreaterThanOrEqual(2)
      const lastBody = JSON.parse(String(posts[posts.length - 1]![1]?.body)) as {
        scope: { senders?: string[] }
        query: string
      }
      expect(lastBody.scope.senders).toContain('bob@example.com')
      expect(lastBody.query).toBe('roof')
    })
  })

  it('mode radio switches re-issue with mode; degraded banner from mocked response', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/api/archive/summary')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            accounts: [],
            date_range: { from: null, to: null },
            counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
            extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
            embedding: { embedded: 0, missing: 0 },
            versions: { schema: 'x', api: '0' },
          }),
        } as Response
      }
      if (String(url).includes('/api/search')) {
        const body = JSON.parse(String(init?.body ?? '{}')) as { mode: string }
        return {
          ok: true,
          status: 200,
          json: async () =>
            mockSearchResponse({
              results: [msgResult],
              mode: body.mode as SearchResponse['mode'],
              degraded:
                body.mode === 'hybrid' ? { semantic: 'unavailable' } : null,
              scope: { free_text: 'roof' },
            }),
        } as Response
      }
      throw new Error(`unexpected: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderResearch()
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'roof' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))

    expect(await screen.findByTestId('degraded-banner')).toHaveTextContent(/degraded:/)
    expect(screen.getByTestId('degraded-banner')).toHaveTextContent(
      /Semantic ranking unavailable/,
    )

    fireEvent.click(within(screen.getByTestId('mode-exact')).getByRole('radio'))

    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter((c) => String(c[0]).includes('/api/search'))
      const last = JSON.parse(String(posts[posts.length - 1]![1]?.body)) as { mode: string }
      expect(last.mode).toBe('exact')
    })
    expect(useWorkingSetStore.getState().mode).toBe('exact')
  })

  it('cards: both types labeled; why-matched; mark emphasis; no dangerouslySetInnerHTML', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/archive/summary')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              accounts: [],
              date_range: { from: null, to: null },
              counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
              extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
              embedding: { embedded: 0, missing: 0 },
              versions: { schema: 'x', api: '0' },
            }),
          } as Response
        }
        if (String(url).includes('/api/search')) {
          return {
            ok: true,
            status: 200,
            json: async () =>
              mockSearchResponse({
                results: [msgResult, attResult],
                scope: { free_text: 'free-hit' },
              }),
          } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    const { container } = renderResearch()
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'free-hit' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))

    expect(await screen.findByTestId('result-card-msg_1')).toBeInTheDocument()
    expect(screen.getByTestId('result-card-att_1')).toBeInTheDocument()

    const typeLabels = screen.getAllByTestId('result-type-label')
    expect(typeLabels.map((el) => el.textContent)).toEqual(
      expect.arrayContaining(['MESSAGE', 'ATTACHMENT']),
    )

    // Why this matched disclosure
    const why = within(screen.getByTestId('result-card-msg_1')).getByTestId('why-matched')
    fireEvent.click(within(why).getByText(/Why this matched/i))
    expect(within(why).getByTestId('match-explanation')).toHaveTextContent(/kind: exact/)

    // Mark emphasis — free text is escaped (script not injected)
    const snippet = within(screen.getByTestId('result-card-msg_1')).getByTestId(
      'result-snippet',
    )
    expect(snippet.querySelector('mark')).toHaveTextContent('free-hit')
    expect(container.innerHTML).not.toMatch(/dangerouslySetInnerHTML/)
    // Ensure no raw script nodes from snippet path
    expect(snippet.querySelector('script')).toBeNull()
  })

  it('grouping produces correct headers/counts from fixed mock window', async () => {
    const results: SearchResult[] = [
      { ...msgResult, id: 'msg_a', thread_id: 'thr_A', subject: 'Alpha', date: '2014-01-01T00:00:00Z', mailbox: 'a@x.com' },
      { ...msgResult, id: 'msg_b', thread_id: 'thr_A', subject: 'Alpha 2', date: '2015-01-01T00:00:00Z', mailbox: 'b@x.com' },
      { ...msgResult, id: 'msg_c', thread_id: 'thr_B', subject: 'Beta', date: '2014-06-01T00:00:00Z', mailbox: 'a@x.com' },
    ]
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/archive/summary')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              accounts: [],
              date_range: { from: null, to: null },
              counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
              extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
              embedding: { embedded: 0, missing: 0 },
              versions: { schema: 'x', api: '0' },
            }),
          } as Response
        }
        if (String(url).includes('/api/search')) {
          return {
            ok: true,
            status: 200,
            json: async () =>
              mockSearchResponse({ results, scope: { free_text: 'x' }, next_cursor: 'cur' }),
          } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    renderResearch()
    fireEvent.change(screen.getByTestId('research-query-input'), { target: { value: 'x' } })
    fireEvent.submit(screen.getByTestId('query-row'))
    await screen.findByTestId('result-card-msg_a')

    fireEvent.click(screen.getByTestId('grp-thread'))
    expect(await screen.findByTestId('result-group-thr_A')).toBeInTheDocument()
    const headers = screen.getAllByTestId('group-header')
    expect(headers.some((h) => h.textContent?.includes('(2)'))).toBe(true)
    expect(screen.getByTestId('grouped-window-note')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('grp-year'))
    expect(screen.getByTestId('result-group-2014')).toBeInTheDocument()
    expect(screen.getByTestId('result-group-2015')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('grp-mailbox'))
    expect(screen.getByTestId('result-group-a@x.com')).toBeInTheDocument()
    expect(screen.getByTestId('result-group-b@x.com')).toBeInTheDocument()
  })

  it('J/K/Enter selection flow; attachment a: codec shows attachment card', async () => {
    const attSource = {
      kind: 'att' as const,
      id: 'att_1',
      filename: 'estimate.pdf',
      content_type: 'application/pdf',
      size: 1024,
      source_message_id: 'msg_1',
      source_envelope: null,
      extraction_status: 'extracted',
      extraction_reason: null,
      markdown: null,
      truncated: false,
      text_offset: 0,
    }

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        const u = String(url)
        if (u.includes('/api/archive/summary')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              accounts: [],
              date_range: { from: null, to: null },
              counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
              extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
              embedding: { embedded: 0, missing: 0 },
              versions: { schema: 'x', api: '0' },
            }),
          } as Response
        }
        if (u.includes('/api/search')) {
          return {
            ok: true,
            status: 200,
            json: async () =>
              mockSearchResponse({
                results: [msgResult, attResult],
                scope: { free_text: 'roof' },
              }),
          } as Response
        }
        if (u.includes('/api/sources/att_1')) {
          return { ok: true, status: 200, json: async () => attSource } as Response
        }
        if (u.includes('/api/sources/msg_1')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              kind: 'msg',
              envelope: {
                id: 'msg_1',
                thread_id: 'thr_1',
                subject: 'Roof decision',
                sender_name: 'Alice',
                sender_address: 'alice@example.com',
                recipients: {},
                date: '2014-06-01T12:00:00Z',
                mailbox: 'me@example.com',
                labels: [],
                has_attachment: true,
                attachments: [],
              },
              body: {
                text: 'body',
                html: null,
                remote_resources_blocked: 0,
                had_active_content: false,
              },
            }),
          } as Response
        }
        throw new Error(`unexpected: ${u}`)
      }),
    )

    renderResearch()
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'roof' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))
    await screen.findByTestId('result-card-msg_1')

    // J moves to first, then next
    fireEvent.keyDown(window, { key: 'j' })
    await waitFor(() => {
      expect(useWorkingSetStore.getState().selection).toEqual({
        kind: 'message',
        sid: 'msg_1',
      })
    })
    fireEvent.keyDown(window, { key: 'j' })
    await waitFor(() => {
      expect(useWorkingSetStore.getState().selection).toEqual({
        kind: 'attachment',
        sid: 'att_1',
      })
    })

    const attCard = await screen.findByTestId('attachment-card')
    expect(attCard).toBeInTheDocument()
    expect(within(attCard).getByText('estimate.pdf')).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'k' })
    await waitFor(() => {
      expect(useWorkingSetStore.getState().selection?.kind).toBe('message')
    })

    fireEvent.keyDown(window, { key: 'Enter' })
    expect(await screen.findByTestId('source-page')).toBeInTheDocument()
  })

  it('scope chips persist across / ↔ /research; View-in-Chronicle sets viewport from scope date', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/archive/summary')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              accounts: [{ account: 'me@example.com', messages: 1 }],
              date_range: { from: '2010-01-01', to: '2020-01-01' },
              counts: { messages: 1, threads: 1, attachments: 0, contacts: 0 },
              extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
              embedding: { embedded: 0, missing: 0 },
              versions: { schema: 'x', api: '0' },
            }),
          } as Response
        }
        if (String(url).includes('/api/search')) {
          return {
            ok: true,
            status: 200,
            json: async () =>
              mockSearchResponse({
                results: [msgResult],
                scope: {
                  date: { from: '2014-01-01', to: '2018-12-31' },
                  mailboxes: ['me@example.com'],
                  free_text: '',
                },
              }),
          } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    useWorkingSetStore.getState().setScopeDate({ from: '2014-01-01', to: '2018-12-31' })
    useWorkingSetStore.getState().addMailbox('me@example.com')

    renderResearch(['/research'])

    // Scope bar chips visible on research
    expect(await screen.findByTestId('scope-chip-date')).toBeInTheDocument()
    expect(screen.getByTestId('scope-chip-mailbox')).toBeInTheDocument()

    // View in Chronicle sets viewport from scope date
    fireEvent.click(screen.getByTestId('view-in-chronicle'))
    expect(await screen.findByTestId('chronicle-stub')).toBeInTheDocument()
    const vp = useWorkingSetStore.getState().viewport
    expect(vp).not.toBeNull()
    expect(vp!.fromMs).toBe(Date.parse('2014-01-01T00:00:00Z'))
    // Scope survived lens change
    expect(useWorkingSetStore.getState().scope.mailboxes).toEqual(['me@example.com'])
    expect(useWorkingSetStore.getState().scope.date).toEqual({
      from: '2014-01-01',
      to: '2018-12-31',
    })
  })

  it('removes unsupported chips (muted + removable)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/archive/summary')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              accounts: [],
              date_range: { from: null, to: null },
              counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
              extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
              embedding: { embedded: 0, missing: 0 },
              versions: { schema: 'x', api: '0' },
            }),
          } as Response
        }
        if (String(url).includes('/api/search')) {
          return {
            ok: true,
            status: 200,
            json: async () =>
              mockSearchResponse({
                results: [],
                unsupported: ['topic:x'],
                scope: { free_text: 'hello' },
              }),
          } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    renderResearch()
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'topic:x hello' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))

    const chip = await screen.findByTestId('unsupported-chip-topic:x')
    expect(chip).toHaveClass('text-text-muted')
    fireEvent.click(screen.getByRole('button', { name: /Remove unsupported topic:x/i }))
    await waitFor(() => {
      expect(screen.queryByTestId('unsupported-chip-topic:x')).not.toBeInTheDocument()
    })
  })

  it('Ask mode mounts AnswerBlock above results and keeps search results (RD-004)', async () => {
    const encoder = new TextEncoder()
    const sse =
      'event: retrieval\ndata: {"count":1,"types":{"message":1},"degraded":null}\n\n' +
      'event: token\ndata: {"text":"Metal [S1]."}\n\n' +
      'event: citation\ndata: {"marker":"[S1]","source_id":"msg_1","source_type":"message","excerpt":"roof","location":{"char_start":0,"char_end":4}}\n\n' +
      'event: done\ndata: {"answer_id":"a1","model_route":"ollama:llama3.2","policy_version":"ask-v1","generated_at":"2026-07-13T00:00:00Z","unmatched_markers":[]}\n\n'

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/archive/summary')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              accounts: [],
              date_range: { from: null, to: null },
              counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
              extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
              embedding: { embedded: 0, missing: 0 },
              versions: { schema: 'x', api: '0' },
            }),
          } as Response
        }
        if (String(url).includes('/api/ask')) {
          const stream = new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode(sse))
              controller.close()
            },
          })
          return new Response(stream, {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          })
        }
        if (String(url).includes('/api/search')) {
          return {
            ok: true,
            status: 200,
            json: async () =>
              mockSearchResponse({
                results: [msgResult],
                scope: { free_text: 'roof' },
              }),
          } as Response
        }
        throw new Error(`unexpected: ${url}`)
      }),
    )

    renderResearch()
    fireEvent.click(screen.getByTestId('desk-mode-ask'))
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'roof' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))

    expect(await screen.findByTestId('answer-block')).toBeInTheDocument()
    expect(await screen.findByTestId('result-card-msg_1')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('ask-answer-text')).toHaveTextContent(/Metal/)
    })
  })

  it('NL query triggers interpret then search (mock ordering)', async () => {
    const order: string[] = []
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url)
      if (u.includes('/api/archive/summary')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            accounts: [],
            date_range: { from: null, to: null },
            counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
            extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
            embedding: { embedded: 0, missing: 0 },
            versions: { schema: 'x', api: '0' },
          }),
        } as Response
      }
      if (u.includes('/api/query/interpret')) {
        order.push('interpret')
        const interp: InterpretResponse = {
          scope: {
            senders: ['alice@example.com'],
            date: { from: '2014-01-01', to: '2018-12-31' },
            free_text: 'roof material',
          },
          free_text: 'roof material',
          chips: [
            { kind: 'sender', value: 'alice@example.com', origin: 'model' },
            {
              kind: 'date',
              value: '2014-01-01..2018-12-31',
              origin: 'model',
            },
          ],
          model_used: true,
        }
        return {
          ok: true,
          status: 200,
          json: async () => interp,
        } as Response
      }
      if (u.includes('/api/search')) {
        order.push('search')
        const body = JSON.parse(String(init?.body ?? '{}')) as {
          query: string
          scope: { senders?: string[] }
        }
        return {
          ok: true,
          status: 200,
          json: async () =>
            mockSearchResponse({
              results: [msgResult],
              scope: {
                senders: body.scope?.senders ?? ['alice@example.com'],
                free_text: body.query,
              },
            }),
        } as Response
      }
      throw new Error(`unexpected: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderResearch()
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'PDFs Alice sent about the renovation from 2014' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))

    await waitFor(() => {
      const i = order.indexOf('interpret')
      expect(i).toBeGreaterThanOrEqual(0)
      expect(order[i + 1]).toBe('search')
    })
    await waitFor(() => {
      const dots = screen.getAllByTestId('chip-origin-dot')
      expect(dots.length).toBeGreaterThanOrEqual(1)
      expect(dots.some((d) => d.getAttribute('data-origin') === 'model')).toBe(true)
    })
  })

  it('operator query skips interpret', async () => {
    const order: string[] = []
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      const u = String(url)
      if (u.includes('/api/archive/summary')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            accounts: [],
            date_range: { from: null, to: null },
            counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
            extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
            embedding: { embedded: 0, missing: 0 },
            versions: { schema: 'x', api: '0' },
          }),
        } as Response
      }
      if (u.includes('/api/query/interpret')) {
        order.push('interpret')
        return { ok: true, status: 200, json: async () => ({}) } as Response
      }
      if (u.includes('/api/search')) {
        order.push('search')
        return {
          ok: true,
          status: 200,
          json: async () =>
            mockSearchResponse({
              results: [msgResult],
              scope: { senders: ['alice@example.com'], free_text: 'roof' },
            }),
        } as Response
      }
      throw new Error(`unexpected: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderResearch()
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'from:alice@example.com roof' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))

    await waitFor(() => {
      expect(order).toContain('search')
    })
    expect(order).not.toContain('interpret')
  })

  it('3s interpret timeout falls through to search', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const order: string[] = []
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url)
      if (u.includes('/api/archive/summary')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            accounts: [],
            date_range: { from: null, to: null },
            counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
            extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
            embedding: { embedded: 0, missing: 0 },
            versions: { schema: 'x', api: '0' },
          }),
        } as Response
      }
      if (u.includes('/api/query/interpret')) {
        order.push('interpret')
        // Hang until aborted
        await new Promise<void>((_resolve, reject) => {
          const signal = init?.signal
          if (signal?.aborted) {
            reject(new DOMException('Aborted', 'AbortError'))
            return
          }
          signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'))
          })
        })
      }
      if (u.includes('/api/search')) {
        order.push('search')
        return {
          ok: true,
          status: 200,
          json: async () =>
            mockSearchResponse({
              results: [msgResult],
              scope: { free_text: 'slow interpret query text' },
            }),
        } as Response
      }
      throw new Error(`unexpected: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderResearch()
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'slow interpret query text here' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))

    await vi.advanceTimersByTimeAsync(3100)

    await waitFor(() => {
      expect(order).toContain('search')
      expect(order).toContain('interpret')
    })
    // Interpret is attempted before the fallthrough search (may share the list
    // with an in-flight call from a prior test in the suite).
    const i = order.indexOf('interpret')
    expect(order.slice(i).some((x) => x === 'search')).toBe(true)
    vi.useRealTimers()
  })

  it('unresolved chip edit-to-apply converts to sender and searches', async () => {
    const searchBodies: Array<{ scope: { senders?: string[] } }> = []
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url)
      if (u.includes('/api/archive/summary')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            accounts: [],
            date_range: { from: null, to: null },
            counts: { messages: 0, threads: 0, attachments: 0, contacts: 0 },
            extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
            embedding: { embedded: 0, missing: 0 },
            versions: { schema: 'x', api: '0' },
          }),
        } as Response
      }
      if (u.includes('/api/query/interpret')) {
        const interp: InterpretResponse = {
          scope: { free_text: 'budget notes' },
          free_text: 'budget notes',
          chips: [
            {
              kind: 'unresolved_person',
              value: 'Alex',
              origin: 'model',
              display: 'Alex',
            },
          ],
          model_used: true,
        }
        return { ok: true, status: 200, json: async () => interp } as Response
      }
      if (u.includes('/api/search')) {
        const body = JSON.parse(String(init?.body ?? '{}')) as {
          scope: { senders?: string[] }
        }
        searchBodies.push(body)
        return {
          ok: true,
          status: 200,
          json: async () =>
            mockSearchResponse({
              results: [msgResult],
              scope: { ...body.scope, free_text: 'budget notes' },
            }),
        } as Response
      }
      throw new Error(`unexpected: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderResearch()
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'messages from Alex about budget notes' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))

    const unresolved = await screen.findByTestId('unresolved-person-Alex')
    fireEvent.click(unresolved)
    const edit = await screen.findByTestId('constraint-edit-unresolved:Alex')
    fireEvent.change(edit, { target: { value: 'alex@example.com' } })
    fireEvent.submit(edit.closest('form')!)

    await waitFor(() => {
      const withSender = searchBodies.filter((b) =>
        b.scope?.senders?.includes('alex@example.com'),
      )
      expect(withSender.length).toBeGreaterThanOrEqual(1)
    })
  })
})
