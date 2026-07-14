/**
 * Workflow C — find a vaguely remembered file (spec §19.3).
 * Mocked-API: Research NL interpret → attachment results → preview 415
 * fallback → source message → Files family compare with amount changes.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { BrowserRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { InterpretResponse, SearchResult } from '../api/types'
import { App } from '../App'
import {
  createTestQueryClient,
  mockArchiveSummary,
  mockSessionOk,
} from '../test/test-utils'
import { resetUrlSyncForTests } from '../workingset/useUrlSync'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'

const MSG_BOB = 'msg_bob_expenses_2012'
const ATT_V1 = 'att_expenses_v1'
const ATT_V2 = 'att_expenses_v2'
const ATT_FAILED = 'att_expenses_failed'
const NL_QUERY =
  'the spreadsheet Bob sent with projected expenses around 2012'

const extractedAtt: SearchResult = {
  result_type: 'attachment',
  id: ATT_V1,
  filename: 'projected_expenses_2012.xlsx',
  content_type:
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  source_message_id: MSG_BOB,
  sender: 'bob@example.com',
  date: '2012-03-15T12:00:00Z',
  snippet: 'projected expenses Q1 spreadsheet',
  extraction_status: 'extracted',
  match: { kind: 'hybrid', exact_rank: 1, semantic_rank: 2, similarity: 0.9 },
}

const failedAtt: SearchResult = {
  result_type: 'attachment',
  id: ATT_FAILED,
  filename: 'projected_expenses_scan.xlsx',
  content_type:
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  source_message_id: 'msg_bob_scan_2012',
  sender: 'bob@example.com',
  date: '2012-04-01T12:00:00Z',
  snippet: 'scan of expenses workbook',
  extraction_status: 'failed',
  match: { kind: 'exact', field: 'filename' },
}

const laterAtt: SearchResult = {
  result_type: 'attachment',
  id: ATT_V2,
  filename: 'Projected_Expenses_2012_v2.xlsx',
  content_type:
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  source_message_id: 'msg_bob_expenses_v2',
  sender: 'bob@example.com',
  date: '2012-06-01T12:00:00Z',
  snippet: 'updated projected expenses',
  extraction_status: 'extracted',
  match: { kind: 'hybrid', exact_rank: 2, semantic_rank: 3, similarity: 0.85 },
}

function fileListItem(
  id: string,
  filename: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    filename,
    content_type:
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    size: 4096,
    date: '2012-03-15T12:00:00Z',
    sender_name: 'Bob',
    sender_address: 'bob@example.com',
    source_message_id: MSG_BOB,
    source_subject: 'Projected expenses',
    extraction: { status: 'extracted', reason: null },
    sha256: `sha-${id}`,
    duplicate_count: 1,
    family_count: 2,
    ...overrides,
  }
}

function attachmentSource(sid: string, markdown: string | null) {
  return {
    kind: 'att',
    id: sid,
    filename:
      sid === ATT_V2
        ? 'Projected_Expenses_2012_v2.xlsx'
        : 'projected_expenses_2012.xlsx',
    content_type:
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    size: 4096,
    extraction_status: sid === ATT_FAILED ? 'failed' : 'extracted',
    extraction_reason: sid === ATT_FAILED ? 'timeout' : null,
    markdown,
    truncated: false,
    text_offset: 0,
    source_message_id: MSG_BOB,
    source_envelope: {
      id: MSG_BOB,
      thread_id: 'thr_bob_expenses',
      subject: 'Projected expenses',
      sender_name: 'Bob',
      sender_address: 'bob@example.com',
      recipients: { to: ['me@example.com'], cc: [], bcc: [] },
      date: '2012-03-15T12:00:00Z',
      mailbox: 'me@example.com',
      labels: [],
      has_attachment: true,
      attachments: [],
    },
  }
}

function messageSource(sid: string) {
  return {
    kind: 'msg',
    envelope: {
      id: sid,
      thread_id: 'thr_bob_expenses',
      subject: 'Projected expenses',
      sender_name: 'Bob',
      sender_address: 'bob@example.com',
      recipients: { to: ['me@example.com'], cc: [], bcc: [] },
      date: '2012-03-15T12:00:00Z',
      mailbox: 'me@example.com',
      labels: [],
      has_attachment: true,
      attachments: [
        {
          id: ATT_V1,
          filename: 'projected_expenses_2012.xlsx',
          content_type:
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          size: 4096,
        },
      ],
    },
    body: {
      text: 'Attached the spreadsheet with projected expenses for 2012.',
      html: null,
      remote_resources_blocked: 0,
      had_active_content: false,
    },
  }
}

function installWorkflowCFetch() {
  const searchResults = [extractedAtt, laterAtt, failedAtt]

  const familyBody = {
    id: ATT_V1,
    stem: 'projected_expenses_2012',
    candidates: [
      {
        id: ATT_V1,
        filename: 'projected_expenses_2012.xlsx',
        date: '2012-03-15T12:00:00Z',
        sender: 'Bob',
        size: 4096,
        sha256: 'sha-v1',
        confidence: 'exact-duplicate',
        signals: ['stem', 'sha256'],
      },
      {
        id: ATT_V2,
        filename: 'Projected_Expenses_2012_v2.xlsx',
        date: '2012-06-01T12:00:00Z',
        sender: 'Bob',
        size: 5120,
        sha256: 'sha-v2',
        confidence: 'probable-version',
        signals: ['stem', 'sender'],
      },
    ],
  }

  const compareBody = {
    a: {
      id: ATT_V1,
      filename: 'projected_expenses_2012.xlsx',
      content_type:
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      size: 4096,
      date: '2012-03-15T12:00:00Z',
      sender: 'Bob',
      sha256: 'sha-v1',
      source_message_id: MSG_BOB,
    },
    b: {
      id: ATT_V2,
      filename: 'Projected_Expenses_2012_v2.xlsx',
      content_type:
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      size: 5120,
      date: '2012-06-01T12:00:00Z',
      sender: 'Bob',
      sha256: 'sha-v2',
      source_message_id: 'msg_bob_expenses_v2',
    },
    hunks: [
      {
        a_start: 0,
        b_start: 0,
        lines: [
          { kind: 'same', text: 'Projected expenses 2012' },
          { kind: 'del', text: 'Total: $45,000' },
          { kind: 'add', text: 'Total: $52,500' },
        ],
      },
    ],
    truncated: false,
    amount_changes: [
      { kind: 'del', text: 'Total: $45,000', amounts: ['$45,000'] },
      { kind: 'add', text: 'Total: $52,500', amounts: ['$52,500'] },
    ],
  }

  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const u = String(url)
    const method = (init?.method || 'GET').toUpperCase()

    if (u.includes('/api/auth/session')) return mockSessionOk()
    if (u.includes('/api/archive/summary')) return mockArchiveSummary()

    if (u.includes('/api/query/interpret') && method === 'POST') {
      const body = JSON.parse(String(init?.body || '{}')) as { text?: string }
      expect(body.text).toMatch(/Bob|spreadsheet|2012|expenses/i)
      const interp: InterpretResponse = {
        scope: {
          senders: ['bob@example.com'],
          file_types: ['spreadsheet'],
          date: { from: '2012-01-01', to: '2012-12-31' },
          free_text: 'projected expenses',
        },
        free_text: 'projected expenses',
        chips: [
          {
            kind: 'sender',
            value: 'bob@example.com',
            origin: 'model',
            display: 'Bob',
          },
          { kind: 'file_type', value: 'spreadsheet', origin: 'model' },
          {
            kind: 'date',
            value: '2012-01-01..2012-12-31',
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

    if (u.includes('/api/search') && method === 'POST') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          results: searchResults,
          next_cursor: null,
          scope: {
            senders: ['bob@example.com'],
            file_types: ['spreadsheet'],
            date: { from: '2012-01-01', to: '2012-12-31' },
            free_text: 'projected expenses',
          },
          unsupported: [],
          scope_fingerprint: 'qs_workflow_c',
          mode: 'hybrid',
          took_ms: 42,
          duplicates_suppressed: 0,
          facets: {
            year: [{ value: 2012, count: 3 }],
            has_attachment: [{ value: true, count: 3 }],
          },
          facet_basis: 'exact',
          degraded: null,
        }),
      } as Response
    }

    if (u.includes('/api/attachments/list') && method === 'POST') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          items: [
            fileListItem(ATT_V1, 'projected_expenses_2012.xlsx'),
            fileListItem(ATT_V2, 'Projected_Expenses_2012_v2.xlsx', {
              date: '2012-06-01T12:00:00Z',
              source_message_id: 'msg_bob_expenses_v2',
              size: 5120,
            }),
            fileListItem(ATT_FAILED, 'projected_expenses_scan.xlsx', {
              extraction: { status: 'failed', reason: 'timeout' },
              family_count: 1,
              source_message_id: 'msg_bob_scan_2012',
            }),
          ],
          next_cursor: null,
          scope_fingerprint: 'qs_workflow_c',
        }),
      } as Response
    }

    if (u.includes('/family')) {
      return {
        ok: true,
        status: 200,
        json: async () => familyBody,
      } as Response
    }

    if (u.includes('/compare')) {
      return {
        ok: true,
        status: 200,
        json: async () => compareBody,
      } as Response
    }

    if (u.includes('/preview')) {
      // Spreadsheets are not binary-previewable → 415 + extracted-text path
      return {
        ok: false,
        status: 415,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({
          preview: false,
          reason: 'spreadsheet is not previewable',
        }),
        text: async () => '',
      } as Response
    }

    if (u.includes('/api/sources/')) {
      const sid = decodeURIComponent(
        u.split('/api/sources/')[1]?.split('?')[0] ?? '',
      )
      if (sid.startsWith('msg_') || sid === MSG_BOB) {
        return {
          ok: true,
          status: 200,
          json: async () => messageSource(sid),
        } as Response
      }
      return {
        ok: true,
        status: 200,
        json: async () =>
          attachmentSource(
            sid,
            sid === ATT_FAILED
              ? null
              : 'Sheet1\nCategory,Amount\nTravel,$45,000\n',
          ),
      } as Response
    }

    if (u.includes('/api/chronicle/buckets')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          scope_fingerprint: 'qs_workflow_c',
          aggregation: 'year',
          unit: 'year',
          viewport: {
            from: '2010-01-01T00:00:00.000Z',
            to: '2015-01-01T00:00:00.000Z',
          },
          lanes: {
            messages: [{ bucket: '2012-01-01T00:00:00.000Z', count: 40 }],
            attachments: [{ bucket: '2012-01-01T00:00:00.000Z', count: 8 }],
            events: { events: [], truncated: false },
          },
          density: {
            unit: 'year',
            buckets: [{ bucket: '2012-01-01T00:00:00.000Z', count: 48 }],
          },
          extent: {
            from: '2010-01-01T00:00:00.000Z',
            to: '2015-01-01T00:00:00.000Z',
          },
          generated_at: '2026-01-01T00:00:00.000Z',
        }),
      } as Response
    }

    if (u.includes('/api/sources/list')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          items: [],
          next_cursor: null,
          scope_fingerprint: 'qs_workflow_c',
        }),
      } as Response
    }

    throw new Error(`unexpected fetch: ${method} ${u}`)
  })

  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, searchResults }
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

describe('Workflow C — find a vaguely remembered file (§19.3)', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
    resetUrlSyncForTests()
    try {
      localStorage.removeItem('chronicle.skipInterpretation')
    } catch {
      /* ignore */
    }
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
    resetUrlSyncForTests()
  })

  it('runs end-to-end: interpret chips → attachment results → preview 415 → source → family compare', async () => {
    const { searchResults } = installWorkflowCFetch()
    renderFullApp()

    expect(await screen.findByTestId('workstation-shell')).toBeInTheDocument()

    // 1. Research Desk — NL query → interpret person/filetype/date chips
    fireEvent.click(screen.getByRole('link', { name: 'Research' }))
    expect(await screen.findByTestId('research-desk')).toBeInTheDocument()

    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: NL_QUERY },
    })
    fireEvent.submit(screen.getByTestId('query-row'))

    // Chips visible (person / filetype / date)
    expect(
      await screen.findByTestId('constraint-chip-sender:bob@example.com:0'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('constraint-chip-file_type:spreadsheet:1'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('constraint-chip-date:2012-01-01..2012-12-31:2'),
    ).toBeInTheDocument()

    // Chips editable — edit person chip
    fireEvent.click(
      screen.getByRole('button', {
        name: /Edit from: bob@example.com|Edit from: Bob/i,
      }),
    )
    const edit = await screen.findByTestId(
      'constraint-edit-sender:bob@example.com:0',
    )
    fireEvent.change(edit, { target: { value: 'robert@example.com' } })
    fireEvent.submit(edit.closest('form')!)
    await waitFor(() => {
      // Re-search after chip edit (chips cleared to scope-derived after re-run)
      expect(screen.getByTestId('results-list')).toBeInTheDocument()
    })

    // Re-submit NL to restore interpret chips for visibility assertions after edit path
    fireEvent.change(screen.getByTestId('research-query-input'), {
      target: { value: NL_QUERY },
    })
    // Clear free-text operators so interpret fires again
    fireEvent.submit(screen.getByTestId('query-row'))
    await screen.findByTestId('constraint-chip-sender:bob@example.com:0')

    // 2. Attachment results — filename, source message provenance, extraction status
    expect(await screen.findByTestId(`result-card-${ATT_V1}`)).toBeInTheDocument()
    expect(screen.getByTestId(`result-card-${ATT_V1}`)).toHaveTextContent(
      'projected_expenses_2012.xlsx',
    )
    expect(screen.getByTestId(`result-card-${ATT_V1}`)).toHaveTextContent(MSG_BOB)
    expect(screen.getByTestId(`result-card-${ATT_V1}`)).toHaveTextContent(
      /Extraction:\s*extracted/i,
    )

    // Pass: failed extraction remains discoverable with metadata
    const failedCard = screen.getByTestId(`result-card-${ATT_FAILED}`)
    expect(failedCard).toBeInTheDocument()
    expect(failedCard).toHaveTextContent('projected_expenses_scan.xlsx')
    expect(failedCard).toHaveTextContent(/Extraction:\s*failed/i)
    expect(failedCard).toHaveTextContent('msg_bob_scan_2012')

    // Pass: source-message provenance on every attachment result
    for (const r of searchResults) {
      if (r.result_type !== 'attachment') continue
      const card = screen.getByTestId(`result-card-${r.id}`)
      expect(card).toHaveTextContent(r.source_message_id!)
      expect(r.source_message_id).toBeTruthy()
    }

    // 3a. Preview spreadsheet — 415 → extracted-text fallback
    fireEvent.click(screen.getByTestId(`result-card-${ATT_V1}`))
    expect(await screen.findByTestId('attachment-card')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('attachment-preview'))
    expect(await screen.findByTestId('preview-panel')).toBeInTheDocument()
    const fallback = await screen.findByTestId('preview-fallback')
    expect(fallback).toHaveTextContent(/spreadsheet is not previewable/i)
    expect(fallback).toHaveTextContent(/Travel|\$45,000|Sheet1/i)
    fireEvent.click(screen.getByTestId('preview-close'))

    // 3b. Open the source message
    useWorkingSetStore.getState().setSelection({ kind: 'message', sid: MSG_BOB })
    expect(await screen.findByTestId('message-card')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('open-full-source'))
    expect(await screen.findByTestId('source-page')).toBeInTheDocument()
    expect(window.location.pathname).toMatch(new RegExp(MSG_BOB))
    const sourcePage = screen.getByTestId('source-page')
    expect(within(sourcePage).getByRole('heading', { name: /Projected expenses/i })).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('source-back'))

    // 3c. Files lens — probable-version indicator + compare later version
    fireEvent.click(screen.getByRole('link', { name: 'Files' }))
    expect(await screen.findByTestId('files-page')).toBeInTheDocument()
    expect(await screen.findByTestId(`file-row-${ATT_V1}`)).toBeInTheDocument()
    expect(screen.getByTestId(`family-badge-${ATT_V1}`)).toHaveTextContent(
      /2 versions/i,
    )
    // Failed extraction still listed with metadata in Files
    expect(screen.getByTestId(`extraction-${ATT_FAILED}`)).toHaveTextContent(
      /failed/i,
    )

    fireEvent.click(screen.getByTestId(`family-badge-${ATT_V1}`))
    expect(await screen.findByTestId('family-panel')).toBeInTheDocument()
    expect(
      await screen.findByTestId(`family-confidence-${ATT_V2}`),
    ).toHaveTextContent(/probable version/i)

    fireEvent.click(screen.getByTestId(`family-compare-${ATT_V1}-${ATT_V2}`))
    expect(await screen.findByTestId('version-compare-view')).toBeInTheDocument()
    // Wait for compare payload (not just the shell)
    expect(
      await screen.findByTestId('compare-metadata', {}, { timeout: 3000 }),
    ).toBeInTheDocument()
    const amounts = await screen.findByTestId('compare-amount-changes')
    expect(amounts).toHaveTextContent('$45,000')
    expect(amounts).toHaveTextContent('$52,500')
  })
})
