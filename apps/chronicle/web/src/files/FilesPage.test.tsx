import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AttachmentListItem, AttachmentListResponse } from '../api/types'
import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { FilesPage } from './FilesPage'
import { PreviewPanel } from './PreviewPanel'

function item(overrides: Partial<AttachmentListItem> = {}): AttachmentListItem {
  return {
    id: 'att_1',
    filename: 'invoice.pdf',
    content_type: 'application/pdf',
    size: 2048,
    date: '2015-06-01T12:00:00Z',
    sender_name: 'Alice',
    sender_address: 'alice@example.com',
    source_message_id: 'msg_1',
    source_subject: 'Q2 invoice',
    extraction: { status: 'extracted', reason: null },
    sha256: 'abc',
    duplicate_count: 1,
    ...overrides,
  }
}

function listResponse(
  items: AttachmentListItem[],
  next: string | null = null,
): AttachmentListResponse {
  return {
    items,
    next_cursor: next,
    scope_fingerprint: 'qs_test',
  }
}

function renderFiles(initialEntries: string[] = ['/files']) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="/files" element={<FilesPage />} />
          <Route path="/data-health" element={<div>Data Health</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('FilesPage', () => {
  beforeEach(() => {
    resetWorkingSetStore()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetWorkingSetStore()
  })

  it('renders table rows with failed-status text prefix', async () => {
    const failed = item({
      id: 'att_fail',
      filename: 'broken.xlsx',
      content_type: 'application/vnd.ms-excel',
      extraction: { status: 'failed', reason: 'timeout' },
    })
    const ok = item({ id: 'att_ok', filename: 'ok.pdf' })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
        if (String(url).includes('/api/attachments/list')) {
          return {
            ok: true,
            status: 200,
            json: async () => listResponse([failed, ok]),
          } as Response
        }
        throw new Error(`unexpected ${url} ${init?.method}`)
      }),
    )

    renderFiles()
    expect(await screen.findByTestId('files-table')).toBeInTheDocument()
    const failCell = screen.getByTestId('extraction-att_fail')
    expect(failCell).toHaveTextContent(/failed/i)
    expect(failCell).toHaveTextContent(/timeout/i)
    expect(failCell.className).toMatch(/conflict/)
    expect(screen.getByTestId('data-health-link-att_fail')).toHaveAttribute(
      'href',
      '/data-health',
    )
    expect(screen.getByTestId('extraction-att_ok')).toHaveTextContent('extracted')
  })

  it('filters re-query on family/status change', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/api/attachments/list')) {
        const body = JSON.parse(String(init?.body ?? '{}')) as {
          filters?: { content_type_family?: string; status?: string }
        }
        const family = body.filters?.content_type_family
        const status = body.filters?.status
        const items =
          family === 'image'
            ? [item({ id: 'att_img', filename: 'photo.png', content_type: 'image/png' })]
            : status === 'failed'
              ? [item({ id: 'att_f', extraction: { status: 'failed', reason: 'x' } })]
              : [item()]
        return {
          ok: true,
          status: 200,
          json: async () => listResponse(items),
        } as Response
      }
      throw new Error(`unexpected ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderFiles()
    await screen.findByTestId('file-row-att_1')

    fireEvent.change(screen.getByTestId('files-family'), { target: { value: 'image' } })
    await waitFor(() => {
      expect(screen.getByTestId('file-row-att_img')).toBeInTheDocument()
    })
    const bodies = fetchMock.mock.calls
      .filter((c) => String(c[0]).includes('/api/attachments/list'))
      .map((c) => JSON.parse(String((c[1] as RequestInit).body)))
    expect(bodies.some((b) => b.filters?.content_type_family === 'image')).toBe(true)
  })

  it('duplicate expand shows occurrences', async () => {
    const dup = item({
      id: 'att_dup',
      filename: 'shared.pdf',
      duplicate_count: 3,
      occurrences: [
        {
          id: 'msg_10',
          subject: 'First',
          sender: 'a@x.com',
          date: '2015-01-01T00:00:00Z',
        },
        {
          id: 'msg_11',
          subject: 'Second',
          sender: 'b@x.com',
          date: '2015-02-01T00:00:00Z',
        },
      ],
    })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
        if (String(url).includes('/api/attachments/list')) {
          const body = JSON.parse(String(init?.body ?? '{}')) as {
            group_duplicates?: boolean
          }
          return {
            ok: true,
            status: 200,
            json: async () =>
              listResponse(
                body.group_duplicates
                  ? [dup]
                  : [item({ id: 'att_dup', duplicate_count: 3 })],
              ),
          } as Response
        }
        throw new Error(`unexpected ${url}`)
      }),
    )

    renderFiles()
    await screen.findByTestId('files-table')
    fireEvent.click(screen.getByTestId('files-group-dup'))
    await screen.findByTestId('file-row-att_dup')
    fireEvent.click(screen.getByTestId('dup-badge-att_dup'))
    const expand = await screen.findByTestId('dup-expand-att_dup')
    expect(within(expand).getByText('First')).toBeInTheDocument()
    expect(within(expand).getByText('Second')).toBeInTheDocument()
  })

  it('gallery renders only image family', async () => {
    const rows = [
      item({ id: 'att_img', filename: 'a.png', content_type: 'image/png' }),
      item({ id: 'att_pdf', filename: 'b.pdf', content_type: 'application/pdf' }),
      item({ id: 'att_jpg', filename: 'c.jpg', content_type: 'image/jpeg' }),
    ]
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/attachments/list')) {
          return {
            ok: true,
            status: 200,
            json: async () => listResponse(rows),
          } as Response
        }
        // gallery img preview — fail so placeholder can show
        return { ok: false, status: 415, json: async () => ({}) } as Response
      }),
    )

    renderFiles(['/files?fv=gallery'])
    expect(await screen.findByTestId('files-gallery')).toBeInTheDocument()
    expect(screen.getByTestId('gallery-card-att_img')).toBeInTheDocument()
    expect(screen.getByTestId('gallery-card-att_jpg')).toBeInTheDocument()
    expect(screen.queryByTestId('gallery-card-att_pdf')).not.toBeInTheDocument()
    expect(screen.getByTestId('gallery-excluded')).toHaveTextContent(/1 non-image/)
  })

  it('row click selects attachment in working set', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/attachments/list')) {
          return {
            ok: true,
            status: 200,
            json: async () => listResponse([item({ id: 'att_42' })]),
          } as Response
        }
        throw new Error(`unexpected ${url}`)
      }),
    )
    renderFiles()
    fireEvent.click(await screen.findByTestId('file-row-att_42'))
    expect(useWorkingSetStore.getState().selection).toEqual({
      kind: 'attachment',
      sid: 'att_42',
    })
  })

  it('fv and fq URL roundtrip via toolbar', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => listResponse([]),
      } as Response),
    )

    renderFiles(['/files?fq=invoice&fv=gallery'])
    expect(await screen.findByTestId('files-gallery')).toBeInTheDocument()
    expect(screen.getByTestId('files-filename')).toHaveValue('invoice')

    fireEvent.click(screen.getByTestId('files-view-table'))
    await waitFor(() => {
      expect(screen.getByTestId('files-table')).toBeInTheDocument()
    })
  })
})

describe('PreviewPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function renderPreview(sid = 'att_1', filename = 'doc.pdf') {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const onClose = vi.fn()
    render(
      <QueryClientProvider client={client}>
        <PreviewPanel attSid={sid} filename={filename} onClose={onClose} />
      </QueryClientProvider>,
    )
    return { onClose }
  }

  it('renders image preview', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/preview')) {
          return {
            ok: true,
            status: 200,
            headers: new Headers({ 'content-type': 'image/png' }),
            text: async () => '',
            json: async () => ({}),
          } as Response
        }
        if (String(url).includes('/api/sources/')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              kind: 'att',
              id: 'att_1',
              filename: 'a.png',
              content_type: 'image/png',
              size: 10,
              source_message_id: null,
              source_envelope: null,
              extraction_status: 'extracted',
              extraction_reason: null,
              markdown: null,
              truncated: false,
              text_offset: 0,
            }),
          } as Response
        }
        throw new Error(String(url))
      }),
    )
    renderPreview('att_1', 'a.png')
    expect(await screen.findByTestId('preview-image')).toBeInTheDocument()
  })

  it('renders pdf iframe sandboxed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/preview')) {
          return {
            ok: true,
            status: 200,
            headers: new Headers({ 'content-type': 'application/pdf' }),
            text: async () => '',
            json: async () => ({}),
          } as Response
        }
        if (String(url).includes('/api/sources/')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              kind: 'att',
              id: 'att_1',
              filename: 'a.pdf',
              content_type: 'application/pdf',
              size: 10,
              source_message_id: null,
              source_envelope: null,
              extraction_status: null,
              extraction_reason: null,
              markdown: null,
              truncated: false,
              text_offset: 0,
            }),
          } as Response
        }
        throw new Error(String(url))
      }),
    )
    renderPreview()
    const iframe = await screen.findByTestId('preview-pdf')
    expect(iframe.tagName).toBe('IFRAME')
    expect(iframe).toHaveAttribute('sandbox', '')
  })

  it('renders text preview body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/preview')) {
          return {
            ok: true,
            status: 200,
            headers: new Headers({ 'content-type': 'text/plain; charset=utf-8' }),
            text: async () => 'hello extracted plain',
            json: async () => ({}),
          } as Response
        }
        if (String(url).includes('/api/sources/')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              kind: 'att',
              id: 'att_1',
              filename: 'a.txt',
              content_type: 'text/plain',
              size: 10,
              source_message_id: null,
              source_envelope: null,
              extraction_status: 'extracted',
              extraction_reason: null,
              markdown: 'md',
              truncated: false,
              text_offset: 0,
            }),
          } as Response
        }
        throw new Error(String(url))
      }),
    )
    renderPreview('att_1', 'a.txt')
    expect(await screen.findByTestId('preview-text')).toHaveTextContent(
      'hello extracted plain',
    )
  })

  it('415 falls back to metadata + extracted text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/preview')) {
          return {
            ok: false,
            status: 415,
            headers: new Headers({ 'content-type': 'application/json' }),
            json: async () => ({ preview: false, reason: 'svg is not previewable' }),
            text: async () => '',
          } as Response
        }
        if (String(url).includes('/api/sources/')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              kind: 'att',
              id: 'att_1',
              filename: 'a.svg',
              content_type: 'image/svg+xml',
              size: 99,
              source_message_id: null,
              source_envelope: null,
              extraction_status: 'extracted',
              extraction_reason: null,
              markdown: 'fallback markdown body',
              truncated: false,
              text_offset: 0,
            }),
          } as Response
        }
        throw new Error(String(url))
      }),
    )
    renderPreview('att_1', 'a.svg')
    const fallback = await screen.findByTestId('preview-fallback')
    expect(fallback).toHaveTextContent(/svg is not previewable/i)
    expect(fallback).toHaveTextContent(/fallback markdown body/)
  })

  it('Esc closes preview', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/preview')) {
          return {
            ok: true,
            status: 200,
            headers: new Headers({ 'content-type': 'image/png' }),
            text: async () => '',
            json: async () => ({}),
          } as Response
        }
        if (String(url).includes('/api/sources/')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              kind: 'att',
              id: 'att_1',
              filename: 'a.png',
              content_type: 'image/png',
              size: 1,
              source_message_id: null,
              source_envelope: null,
              extraction_status: null,
              extraction_reason: null,
              markdown: null,
              truncated: false,
              text_offset: 0,
            }),
          } as Response
        }
        throw new Error(String(url))
      }),
    )
    const { onClose } = renderPreview()
    await screen.findByTestId('preview-panel')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })
})
