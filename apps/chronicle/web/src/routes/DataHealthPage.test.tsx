import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ArchiveHealth } from '../api/types'
import {
  mockSessionOk,
  renderWithProviders,
} from '../test/test-utils'
import { DataHealthPage } from './DataHealthPage'

const mockHealth: ArchiveHealth = {
  coverage: {
    accounts: [{ account: 'me@example.com', messages: 100 }],
    date_range: { from: '2010-01-01T00:00:00', to: '2024-12-31T00:00:00' },
    messages: 1280000,
    threads: 400000,
    attachments: 50000,
    contacts: 12000,
  },
  threading: {
    single_message_threads: 300000,
    max_thread_size: 42,
    null_date_messages: 3,
  },
  extraction: {
    by_status: {
      extracted: 40000,
      failed: 100,
      skipped: 200,
      pending: 50,
    },
    top_failure_reasons: [
      { reason: 'unsupported format: application/octet-stream', count: 80 },
      { reason: 'timeout', count: 20 },
    ],
    by_content_type: [
      {
        content_type: 'application/pdf',
        extracted: 10000,
        failed: 50,
        skipped: 10,
      },
      {
        content_type: 'image/png',
        extracted: 5000,
        failed: 0,
        skipped: 100,
      },
    ],
  },
  embeddings: {
    emails: { embedded: 1200000, missing: 80000 },
    attachment_chunks: { embedded: 90000, missing: 1000 },
  },
  imports: [
    {
      started_at: '2024-06-01T12:00:00',
      source_account: 'me@example.com',
      status: 'completed',
      messages_inserted: 500,
      messages_skipped: 10,
    },
  ],
  audit_tail: [
    {
      at: '2024-12-01T14:00:00+00:00',
      username: 'owner',
      action: 'events_generate',
      detail: { bursts: 3, created: 1, model: 'llama3.2' },
    },
    {
      at: '2024-12-01T13:00:00+00:00',
      username: 'owner',
      action: 'ask',
      detail: { status: 'complete' },
    },
  ],
  generated_at: '2024-12-01T15:30:00+00:00',
}

function mockArchiveHealth(body: ArchiveHealth = mockHealth) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response
}

describe('DataHealthPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders mocked health payload sections and failure reasons', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockSessionOk()
        if (String(url).includes('/api/health/archive')) {
          return mockArchiveHealth()
        }
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderWithProviders(<DataHealthPage />)

    expect(await screen.findByRole('heading', { name: 'Coverage' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Data Health' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Threading' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Extraction' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Embeddings' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Recent imports' })).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: 'Model & export activity' }),
    ).toBeInTheDocument()

    expect(
      screen.getByText('unsupported format: application/octet-stream'),
    ).toBeInTheDocument()
    expect(screen.getByText('timeout')).toBeInTheDocument()

    // Failed counts > 0 include text prefix "failed:" (not color-only)
    expect(screen.getByText(/failed:\s*100/)).toBeInTheDocument()
    expect(screen.getByText(/failed:\s*80/)).toBeInTheDocument()

    expect(screen.getByText('1,280,000')).toBeInTheDocument()
    expect(screen.getByText('Attachment chunks')).toBeInTheDocument()
    expect(screen.getByText('90,000')).toBeInTheDocument()

    // Audit tail table (sixth section)
    expect(screen.getByTestId('audit-tail-table')).toBeInTheDocument()
    expect(screen.getByText('events_generate')).toBeInTheDocument()
    expect(screen.getByText('ask')).toBeInTheDocument()
  })

  it('shows Retry on error state', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (String(url).includes('/api/auth/session')) return mockSessionOk()
      if (String(url).includes('/api/health/archive')) {
        return {
          ok: false,
          status: 500,
          json: async () => ({ detail: 'boom' }),
        } as Response
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<DataHealthPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /Failed to load data health/,
    )
    const retry = screen.getByRole('button', { name: /retry/i })
    expect(retry).toBeInTheDocument()

    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/auth/session')) return mockSessionOk()
      if (String(url).includes('/api/health/archive')) {
        return mockArchiveHealth()
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    fireEvent.click(retry)

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Coverage' })).toBeInTheDocument()
    })
  })
})
