/**
 * Workflow E — produce a defensible case file (spec §19.5).
 * Mocked-API: Chronicle period → workspace with scope → pin message,
 * attachment, event, answer → note + heading → redaction export review
 * → confirmed download with provenance manifest.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ChronicleBuckets,
  QueryScope,
  WorkspaceBlock,
  WorkspaceBlockContent,
} from '../api/types'
import { App } from '../App'
import {
  createTestQueryClient,
  mockArchiveSummary,
  mockSessionOk,
} from '../test/test-utils'
import { resetUrlSyncForTests, writeStoreToUrlNow } from '../workingset/useUrlSync'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'

const EXTENT_FROM = '2014-01-01T00:00:00.000Z'
const EXTENT_TO = '2016-01-01T00:00:00.000Z'
const PERIOD = {
  fromMs: Date.UTC(2015, 5, 1),
  toMs: Date.UTC(2015, 7, 1),
}
const WS_ID = 'ws-workflow-e'
const EVENT_ID = 'evt-roof-decision'
const MSG_ID = 'msg_metal_quote'
const ATT_ID = 'att_quote_pdf'
const ANSWER_ID = 'ans-workflow-e'
const CITATION_SOURCE = 'msg_metal_quote'
const EXTRA_CITATION = 'msg_slate_dissent'

function mockBuckets(): Response {
  const body: ChronicleBuckets = {
    scope_fingerprint: 'qs_workflow_e',
    aggregation: 'month',
    unit: 'month',
    viewport: { from: EXTENT_FROM, to: EXTENT_TO },
    lanes: {
      messages: [
        { bucket: '2015-06-01T00:00:00.000Z', count: 80 },
        { bucket: '2015-07-01T00:00:00.000Z', count: 40 },
      ],
      attachments: [{ bucket: '2015-06-01T00:00:00.000Z', count: 6 }],
      events: {
        events: [
          {
            event_id: EVENT_ID,
            title: 'Roof material decision',
            time_start: '2015-06-15T00:00:00Z',
            time_end: null,
            time_precision: 'day',
            origin: 'automatic',
            event_type: 'decision',
            status: 'confirmed',
            evidence_strength: 'high',
          },
        ],
        truncated: false,
      },
    },
    density: {
      unit: 'year',
      buckets: [{ bucket: '2015-01-01T00:00:00.000Z', count: 120 }],
    },
    extent: { from: EXTENT_FROM, to: EXTENT_TO },
    generated_at: '2026-01-01T00:00:00.000Z',
  }
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response
}

function sseBody(frames: { event: string; data: unknown }[]): string {
  return frames
    .map((f) => `event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`)
    .join('')
}

function streamResponse(body: string): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(body))
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function messageSource(sid: string) {
  return {
    kind: 'msg',
    envelope: {
      id: sid,
      thread_id: 'thr_roof',
      subject: 'Metal quote accepted',
      sender_name: 'Alice',
      sender_address: 'alice@example.com',
      recipients: { to: ['bob@example.com'], cc: [], bcc: [] },
      date: '2015-06-10T12:00:00Z',
      mailbox: 'me@example.com',
      labels: [],
      has_attachment: true,
      attachments: [
        {
          id: ATT_ID,
          filename: 'quote.pdf',
          content_type: 'application/pdf',
          size: 1024,
        },
      ],
    },
    body: {
      text: 'We go with metal roof.',
      html: null,
      remote_resources_blocked: 0,
      had_active_content: false,
    },
  }
}

function attachmentSource() {
  return {
    kind: 'att',
    id: ATT_ID,
    filename: 'quote.pdf',
    content_type: 'application/pdf',
    size: 1024,
    extraction_status: 'extracted',
    extraction_reason: null,
    markdown: 'Quote total $9,500',
    truncated: false,
    text_offset: 0,
    source_message_id: MSG_ID,
    source_envelope: messageSource(MSG_ID).envelope,
  }
}

function eventDetail() {
  return {
    id: EVENT_ID,
    title: 'Roof material decision',
    time_start: '2015-06-15T00:00:00Z',
    time_end: null,
    time_precision: 'day',
    origin: 'automatic',
    event_type: 'decision',
    status: 'confirmed',
    evidence_strength: 'high',
    current_version: 1,
    summary: 'Chose metal over slate.',
    derivation: {
      generated_at: '2026-07-13T12:00:00Z',
      process_version: 'event-v1',
      model_route: 'local-llama',
      scope_fingerprint: 'qs_workflow_e',
    },
    version: {
      version: 1,
      author: 'automatic',
      title: 'Roof material decision',
      summary: 'Chose metal over slate.',
      derivation: {
        generated_at: '2026-07-13T12:00:00Z',
        process_version: 'event-v1',
        model_route: 'local-llama',
        scope_fingerprint: 'qs_workflow_e',
      },
    },
    claims: [
      {
        id: 'c1',
        position: 0,
        text: 'Metal roof was selected',
        status: 'direct',
        citations: [
          {
            source_id: MSG_ID,
            source_type: 'message',
            subject: 'Metal quote accepted',
            sender: 'Alice',
            date: '2015-06-10T12:00:00Z',
            excerpt: 'we go with metal',
            excerpt_hash: 'hash-m1',
          },
        ],
      },
    ],
    conflicts: [],
  }
}

type BlockRecord = WorkspaceBlock & {
  answer?: {
    answer_id: string
    answer_text: string
    citations: {
      marker: string
      source_id: string
      source_type: string
      excerpt: string
      excerpt_hash?: string
    }[]
    model_route?: string
    policy_version?: string
    generated_at?: string
  }
}

function installWorkflowEFetch() {
  const createdWorkspaces: { name: string; scope: QueryScope }[] = []
  const blocks: BlockRecord[] = []
  const exportRequests: unknown[] = []
  let blockSeq = 0
  let workspaceVersion = 1

  const exportDocFixture = {
    id: WS_ID,
    name: 'Roof case file',
    scope: {} as QueryScope,
    version: 1,
    blocks: [] as BlockRecord[],
    manifest: [
      {
        source_id: MSG_ID,
        source_type: 'message',
        date: '2015-06-10T12:00:00Z',
        sender: 'Alice',
        subject_or_filename: 'Metal quote accepted',
        excerpt_hash: 'hash-m1',
      },
      {
        source_id: ATT_ID,
        source_type: 'attachment',
        date: '2015-06-10T12:00:00Z',
        sender: 'Alice',
        subject_or_filename: 'quote.pdf',
        excerpt_hash: 'hash-att',
      },
      {
        source_id: EVENT_ID,
        source_type: 'event',
        date: '2015-06-15T00:00:00Z',
        sender: null,
        subject_or_filename: 'Roof material decision',
        excerpt_hash: null,
      },
      {
        source_id: EXTRA_CITATION,
        source_type: 'message',
        date: '2015-06-14T12:00:00Z',
        sender: 'Carol',
        subject_or_filename: 'Prefer slate',
        excerpt_hash: 'hash-s1',
      },
    ],
    export: {
      generated_at: '2026-07-14T12:00:00Z',
      policy_versions: { ask: 'ask-v1' },
      fingerprint: 'a'.repeat(64),
      redactions: { email: 2, phone: 0 },
      redactions_by_source: { [MSG_ID]: 2 },
      generation: {
        answer_id: ANSWER_ID,
        model_route: 'ollama:llama3.2',
        policy_version: 'ask-v1',
        generated_at: '2026-07-13T12:00:00Z',
      },
    },
  }

  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const u = String(url)
    const method = (init?.method || 'GET').toUpperCase()

    if (u.includes('/api/auth/session')) return mockSessionOk()
    if (u.includes('/api/archive/summary')) return mockArchiveSummary()
    if (u.includes('/api/chronicle/buckets')) return mockBuckets()

    if (u.includes('/api/sources/list')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          items: [
            {
              id: MSG_ID,
              subject: 'Metal quote accepted',
              sender_name: 'Alice',
              sender_address: 'alice@example.com',
              date: '2015-06-10T12:00:00Z',
              mailbox: 'me@example.com',
              has_attachment: true,
              attachment_count: 1,
              thread_id: 'thr_roof',
            },
          ],
          next_cursor: null,
          scope_fingerprint: 'qs_workflow_e',
        }),
      } as Response
    }

    if (u.includes(`/api/sources/${ATT_ID}`) || u.includes(`/api/sources/${encodeURIComponent(ATT_ID)}`)) {
      return {
        ok: true,
        status: 200,
        json: async () => attachmentSource(),
      } as Response
    }

    if (u.includes('/api/sources/')) {
      const sid = decodeURIComponent(
        u.split('/api/sources/')[1]?.split('?')[0] ?? MSG_ID,
      )
      if (sid === ATT_ID || sid.startsWith('att_')) {
        return {
          ok: true,
          status: 200,
          json: async () => attachmentSource(),
        } as Response
      }
      return {
        ok: true,
        status: 200,
        json: async () => messageSource(sid),
      } as Response
    }

    if (u.includes(`/api/events/${EVENT_ID}`)) {
      return {
        ok: true,
        status: 200,
        json: async () => eventDetail(),
      } as Response
    }

    if (u.includes('/api/ask') && method === 'POST') {
      const body = sseBody([
        {
          event: 'retrieval',
          data: {
            count: 2,
            types: { message: 2 },
            degraded: null,
          },
        },
        { event: 'token', data: { text: 'Metal roof was selected [S1].' } },
        {
          event: 'citation',
          data: {
            marker: '[S1]',
            source_id: CITATION_SOURCE,
            source_type: 'message',
            excerpt: 'we go with metal',
            location: { char_start: 0, char_end: 16 },
          },
        },
        {
          event: 'citation',
          data: {
            marker: '[S2]',
            source_id: EXTRA_CITATION,
            source_type: 'message',
            excerpt: 'prefer slate',
            location: null,
          },
        },
        {
          event: 'done',
          data: {
            answer_id: ANSWER_ID,
            model_route: 'ollama:llama3.2',
            policy_version: 'ask-v1',
            generated_at: '2026-07-13T12:00:00Z',
            unmatched_markers: [],
          },
        },
      ])
      return streamResponse(body)
    }

    if (u.includes('/api/search') && method === 'POST') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          results: [
            {
              result_type: 'message',
              id: MSG_ID,
              subject: 'Metal quote accepted',
              sender: 'alice@example.com',
              sender_name: 'Alice',
              date: '2015-06-10T12:00:00Z',
              mailbox: 'me@example.com',
              thread_id: 'thr_roof',
              snippet: 'we go with metal',
              has_attachment: true,
              match: { kind: 'exact', field: 'body' },
            },
          ],
          next_cursor: null,
          scope: {},
          unsupported: [],
          scope_fingerprint: 'qs_workflow_e',
          mode: 'hybrid',
          took_ms: 10,
          duplicates_suppressed: 0,
          facets: null,
          facet_basis: null,
          degraded: null,
        }),
      } as Response
    }

    // Workspaces list
    if (
      u.includes('/api/workspaces') &&
      method === 'GET' &&
      !u.includes('/blocks') &&
      !u.match(/\/api\/workspaces\/[^/]+$/)
    ) {
      const items =
        createdWorkspaces.length > 0
          ? [
              {
                id: WS_ID,
                name: createdWorkspaces[0]!.name,
                updated_at: '2026-01-01T00:00:00Z',
                counts: {
                  blocks: blocks.length,
                  pins: blocks.filter((b) => b.block_type === 'pin').length,
                  notes: blocks.filter((b) => b.block_type === 'note').length,
                  answers: blocks.filter((b) => b.block_type === 'answer')
                    .length,
                  headings: blocks.filter((b) => b.block_type === 'heading')
                    .length,
                },
              },
            ]
          : []
      return {
        ok: true,
        status: 200,
        json: async () => ({ items }),
      } as Response
    }

    // Create workspace
    if (
      u.includes('/api/workspaces') &&
      method === 'POST' &&
      !u.includes('/blocks') &&
      !u.includes('/export')
    ) {
      const body = JSON.parse(String(init?.body || '{}')) as {
        name: string
        scope: QueryScope
      }
      createdWorkspaces.push(body)
      exportDocFixture.scope = body.scope
      exportDocFixture.name = body.name
      return {
        ok: true,
        status: 201,
        json: async () => ({
          id: WS_ID,
          name: body.name,
          scope: body.scope,
          version: workspaceVersion,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          counts: { blocks: 0, pins: 0, notes: 0, answers: 0, headings: 0 },
        }),
      } as Response
    }

    // Create block
    if (u.includes(`/api/workspaces/${WS_ID}/blocks`) && method === 'POST') {
      const body = JSON.parse(String(init?.body || '{}')) as {
        block_type: WorkspaceBlock['block_type']
        content: WorkspaceBlockContent
      }
      blockSeq += 1
      const id = `blk-${body.block_type}-${blockSeq}`
      const block: BlockRecord = {
        id,
        workspace_id: WS_ID,
        position: blocks.length,
        block_type: body.block_type,
        content: body.content as WorkspaceBlockContent & Record<string, unknown>,
      }
      if (body.block_type === 'answer') {
        block.answer = {
          answer_id: ANSWER_ID,
          answer_text: 'Metal roof was selected [S1].',
          citations: [
            {
              marker: 'S1',
              source_id: CITATION_SOURCE,
              source_type: 'message',
              excerpt: 'we go with metal',
              excerpt_hash: 'hash-m1',
            },
            {
              marker: 'S2',
              source_id: EXTRA_CITATION,
              source_type: 'message',
              excerpt: 'prefer slate',
              excerpt_hash: 'hash-s1',
            },
          ],
          model_route: 'ollama:llama3.2',
          policy_version: 'ask-v1',
          generated_at: '2026-07-13T12:00:00Z',
        }
      }
      blocks.push(block)
      exportDocFixture.blocks = [...blocks]
      return {
        ok: true,
        status: 201,
        json: async () => block,
      } as Response
    }

    // Patch block (note/heading edit)
    if (
      u.includes(`/api/workspaces/${WS_ID}/blocks/`) &&
      method === 'PATCH'
    ) {
      const bid = u.split('/blocks/')[1]?.split('?')[0] ?? ''
      const body = JSON.parse(String(init?.body || '{}')) as {
        content?: Record<string, unknown>
        position?: number
      }
      const block = blocks.find((b) => b.id === bid)
      if (!block) {
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: 'not found' }),
        } as Response
      }
      if (body.content) block.content = { ...block.content, ...body.content }
      if (body.position != null) block.position = body.position
      return {
        ok: true,
        status: 200,
        json: async () => block,
      } as Response
    }

    // Export
    if (u.includes(`/api/workspaces/${WS_ID}/export`) && method === 'POST') {
      const body = JSON.parse(String(init?.body || '{}')) as {
        format?: string
        redact?: {
          enabled?: boolean
          kinds?: string[]
          confirmed?: boolean
          custom_terms?: string[]
        }
      }
      exportRequests.push(body)
      if (body.redact?.enabled && !body.redact.confirmed) {
        return {
          ok: true,
          status: 200,
          headers: new Headers({ 'Content-Type': 'application/json' }),
          json: async () => ({
            review: true,
            counts: { email: 2, phone: 1 },
            samples: [
              {
                kind: 'email',
                value: 'alice@example.com',
                start: 0,
                end: 17,
                context: 'from alice@example.com about metal',
              },
              {
                kind: 'phone',
                value: '555-0100',
                start: 0,
                end: 8,
                context: 'call 555-0100',
              },
            ],
            format: body.format || 'json',
          }),
        } as Response
      }
      // Confirmed (or non-redacted) download
      const payload = {
        ...exportDocFixture,
        blocks: [...blocks],
        export: {
          ...exportDocFixture.export,
          redactions: body.redact?.enabled
            ? { email: 2, phone: 1 }
            : {},
        },
      }
      const text = JSON.stringify(payload)
      return {
        ok: true,
        status: 200,
        headers: new Headers({
          'Content-Type': 'application/json',
          'Content-Disposition': 'attachment; filename="Roof-case-file.json"',
          'X-Manifest-Fingerprint': payload.export.fingerprint,
          'X-Source-Count': String(payload.manifest.length),
        }),
        blob: async () => new Blob([text], { type: 'application/json' }),
        json: async () => payload,
      } as Response
    }

    // Get workspace detail
    if (
      (u === `/api/workspaces/${WS_ID}` ||
        u.endsWith(`/api/workspaces/${WS_ID}`)) &&
      method === 'GET'
    ) {
      const last = createdWorkspaces[createdWorkspaces.length - 1]
      return {
        ok: true,
        status: 200,
        json: async () => ({
          id: WS_ID,
          name: last?.name ?? 'Roof case file',
          description: null,
          scope: last?.scope ?? {},
          version: workspaceVersion,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          counts: {
            blocks: blocks.length,
            pins: blocks.filter((b) => b.block_type === 'pin').length,
            notes: blocks.filter((b) => b.block_type === 'note').length,
            answers: blocks.filter((b) => b.block_type === 'answer').length,
            headings: blocks.filter((b) => b.block_type === 'heading').length,
          },
          blocks: [...blocks],
        }),
      } as Response
    }

    throw new Error(`unexpected fetch: ${method} ${u}`)
  })

  vi.stubGlobal('fetch', fetchMock)
  return {
    fetchMock,
    createdWorkspaces,
    blocks,
    exportRequests,
    exportDocFixture,
  }
}

function renderFullApp() {
  const client = createTestQueryClient()
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('Workflow E — produce a defensible case file (§19.5)', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
    resetUrlSyncForTests()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
    resetUrlSyncForTests()
  })

  it('runs end-to-end: workspace scope → four pins → note/heading → redacted export manifest', async () => {
    const {
      createdWorkspaces,
      blocks,
      exportRequests,
      exportDocFixture,
    } = installWorkflowEFetch()

    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:mock-export')
    const revokeObjectURL = vi
      .spyOn(URL, 'revokeObjectURL')
      .mockImplementation(() => {})

    renderFullApp()
    expect(await screen.findByTestId('workstation-shell')).toBeInTheDocument()
    expect(await screen.findByTestId('timeline-toolbar')).toBeInTheDocument()

    // 1. Create workspace from a Chronicle period (brush → scope)
    useWorkingSetStore.getState().setFocus(PERIOD)
    writeStoreToUrlNow()
    expect(await screen.findByTestId('focus-mode')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('focus-set-scope-date'))
    await waitFor(() => {
      expect(screen.queryByTestId('focus-mode')).not.toBeInTheDocument()
    })
    expect(await screen.findByTestId('scope-chip-date')).toBeInTheDocument()
    const scopeSnapshot = structuredClone(
      useWorkingSetStore.getState().scope,
    )
    expect(scopeSnapshot.date).toBeTruthy()

    fireEvent.click(screen.getByRole('link', { name: 'Workspaces' }))
    expect(await screen.findByTestId('workspaces-list-page')).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('new-workspace-name'), {
      target: { value: 'Roof case file' },
    })
    expect(screen.getByTestId('use-current-scope')).toBeChecked()
    fireEvent.click(screen.getByTestId('create-workspace'))

    await waitFor(() => {
      expect(createdWorkspaces.length).toBe(1)
    })
    expect(createdWorkspaces[0]!.scope).toEqual(scopeSnapshot)
    // create navigates into the new workspace
    expect(await screen.findByTestId('workspace-page')).toBeInTheDocument()

    // Back to Chronicle to pin sources
    fireEvent.click(screen.getByRole('link', { name: 'Chronicle' }))
    expect(await screen.findByRole('heading', { name: 'Chronicle' })).toBeInTheDocument()

    // Pin message
    useWorkingSetStore.getState().setSelection({
      kind: 'message',
      sid: MSG_ID,
    })
    expect(await screen.findByTestId('message-card')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('pin-to-workspace-btn'))
    expect(await screen.findByTestId('pin-workspace-menu')).toBeInTheDocument()
    fireEvent.click(await screen.findByTestId(`pin-workspace-${WS_ID}`))
    await waitFor(() => {
      expect(
        blocks.some(
          (b) =>
            b.block_type === 'pin' &&
            (b.content as { source_id?: string }).source_id === MSG_ID,
        ),
      ).toBe(true)
    })

    // Pin attachment
    useWorkingSetStore.getState().setSelection({
      kind: 'attachment',
      sid: ATT_ID,
    })
    expect(await screen.findByTestId('attachment-card')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('pin-to-workspace-btn'))
    fireEvent.click(await screen.findByTestId(`pin-workspace-${WS_ID}`))
    await waitFor(() => {
      expect(
        blocks.some(
          (b) =>
            b.block_type === 'pin' &&
            (b.content as { source_id?: string }).source_id === ATT_ID,
        ),
      ).toBe(true)
    })

    // Pin event
    useWorkingSetStore.getState().setSelection({
      kind: 'event',
      eventId: EVENT_ID,
    })
    expect(await screen.findByTestId('event-card')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('pin-to-workspace-btn'))
    fireEvent.click(await screen.findByTestId(`pin-workspace-${WS_ID}`))
    await waitFor(() => {
      expect(
        blocks.some(
          (b) =>
            b.block_type === 'pin' &&
            (b.content as { source_id?: string }).source_id === EVENT_ID,
        ),
      ).toBe(true)
    })

    // Pin grounded answer from Research Desk Ask mode
    fireEvent.click(screen.getByRole('link', { name: 'Research' }))
    expect(await screen.findByTestId('research-desk')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('desk-mode-ask'))
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: 'What roof material was chosen?' },
    })
    fireEvent.submit(screen.getByTestId('query-row'))
    expect(await screen.findByTestId('pin-answer-btn')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('ask-answer-text')).toHaveTextContent(/Metal roof/)
    })
    fireEvent.click(screen.getByTestId('pin-answer-btn'))
    expect(await screen.findByTestId('pin-answer-menu')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId(`pin-answer-workspace-${WS_ID}`))
    await waitFor(() => {
      expect(blocks.some((b) => b.block_type === 'answer')).toBe(true)
    })

    // Open workspace list → case file
    fireEvent.click(screen.getByRole('link', { name: 'Workspaces' }))
    expect(await screen.findByTestId('workspaces-list-page')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('link', { name: 'Roof case file' }))
    expect(await screen.findByTestId('workspace-page')).toBeInTheDocument()

    // Four block types: 3 pins + 1 answer
    const pinBlocks = blocks.filter((b) => b.block_type === 'pin')
    const answerBlocks = blocks.filter((b) => b.block_type === 'answer')
    expect(pinBlocks.length).toBe(3)
    expect(answerBlocks.length).toBe(1)
    expect(screen.getByTestId('notebook-blocks')).toBeInTheDocument()
    const answerBlk = answerBlocks[0]!
    expect(screen.getByTestId(`answer-block-${answerBlk.id}`)).toBeInTheDocument()
    expect(
      screen.getByTestId(`answer-citations-${answerBlk.id}`),
    ).toHaveTextContent(CITATION_SOURCE)

    // 3. Add note + conclusion-style heading
    fireEvent.click(screen.getByTestId('add-heading'))
    await waitFor(() => {
      expect(blocks.some((b) => b.block_type === 'heading')).toBe(true)
    })
    const heading = blocks.find((b) => b.block_type === 'heading')!
    fireEvent.click(screen.getByTestId(`heading-view-${heading.id}`))
    const headingEdit = await screen.findByTestId(`heading-edit-${heading.id}`)
    fireEvent.change(headingEdit, { target: { value: 'Conclusion' } })
    fireEvent.blur(headingEdit)
    await waitFor(() => {
      expect((heading.content as { text?: string }).text).toBe('Conclusion')
    })

    fireEvent.click(screen.getByTestId('add-note'))
    await waitFor(() => {
      expect(blocks.some((b) => b.block_type === 'note')).toBe(true)
    })
    const note = blocks.find((b) => b.block_type === 'note')!
    fireEvent.click(screen.getByTestId(`note-view-${note.id}`))
    const noteEdit = await screen.findByTestId(`note-edit-${note.id}`)
    fireEvent.change(noteEdit, {
      target: { value: 'Evidence supports metal roof selection.' },
    })
    fireEvent.blur(noteEdit)
    await waitFor(() => {
      expect((note.content as { text?: string }).text).toMatch(/metal roof/i)
    })

    // 4. Export with redaction review → confirm download
    fireEvent.click(screen.getByText('Export'))
    fireEvent.click(screen.getByTestId('redact-enabled'))
    fireEvent.click(screen.getByTestId('export-json'))

    // UI surfaces review first
    expect(await screen.findByTestId('export-review-panel')).toBeInTheDocument()
    expect(screen.getByTestId('export-review-counts')).toHaveTextContent('email')
    expect(screen.getByTestId('export-review-counts')).toHaveTextContent('phone')
    expect(screen.getByTestId('export-review-samples')).toBeInTheDocument()

    // First request: review (confirmed: false)
    expect(exportRequests.length).toBeGreaterThanOrEqual(1)
    const reviewReq = exportRequests[0] as {
      redact?: { confirmed?: boolean; kinds?: string[] }
    }
    expect(reviewReq.redact?.confirmed).toBe(false)
    expect(reviewReq.redact?.kinds).toEqual(
      expect.arrayContaining(['email', 'phone']),
    )

    fireEvent.click(screen.getByTestId('export-confirm-download'))
    await waitFor(() => {
      expect(createObjectURL).toHaveBeenCalled()
    })

    // Confirmed request carries kinds + confirmed
    const confirmedReq = exportRequests.find(
      (r) =>
        (r as { redact?: { confirmed?: boolean } }).redact?.confirmed === true,
    ) as { redact?: { confirmed?: boolean; kinds?: string[] }; format?: string }
    expect(confirmedReq).toBeTruthy()
    expect(confirmedReq.redact?.confirmed).toBe(true)
    expect(confirmedReq.redact?.kinds).toEqual(
      expect.arrayContaining(['email', 'phone']),
    )
    expect(confirmedReq.format).toBe('json')

    // 5. Export record / mock manifest carries required fields
    const manifest = exportDocFixture.manifest
    expect(exportDocFixture.export.fingerprint).toMatch(/^[a-f0-9]{64}$/)
    expect(exportDocFixture.export.redactions).toBeTruthy()
    expect(exportDocFixture.export.generation).toMatchObject({
      answer_id: ANSWER_ID,
      model_route: 'ollama:llama3.2',
      policy_version: 'ask-v1',
    })
    for (const row of manifest) {
      expect(row).toHaveProperty('source_id')
      expect(row).toHaveProperty('excerpt_hash')
    }

    // Pass condition: every exported claim's citation source ⊆ manifest
    const answer = blocks.find((b) => b.block_type === 'answer')!
    const citationIds = new Set(
      (answer.answer?.citations ?? []).map((c) => c.source_id),
    )
    const manifestIds = new Set(manifest.map((m) => m.source_id))
    for (const sid of citationIds) {
      expect(manifestIds.has(sid)).toBe(true)
    }

    createObjectURL.mockRestore()
    revokeObjectURL.mockRestore()
  })
})
