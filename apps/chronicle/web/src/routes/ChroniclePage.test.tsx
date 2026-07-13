import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  mockArchiveSummary,
  mockSessionOk,
  mockUnauthorized,
  renderApp,
} from '../test/test-utils'

describe('Archive summary panel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders mocked ArchiveSummary counts', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockSessionOk()
        if (String(url).includes('/api/archive/summary')) {
          return mockArchiveSummary()
        }
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/'])

    expect(await screen.findByText('Archive coverage')).toBeInTheDocument()
    expect(screen.getByText('1,280,000')).toBeInTheDocument()
    expect(screen.getByText('400,000')).toBeInTheDocument()
    expect(screen.getByText('50,000')).toBeInTheDocument()
    expect(screen.getByText('12,000')).toBeInTheDocument()
    expect(
      screen.getByText(/40000 extracted \/ 100 failed \/ 200 skipped \/ 50 pending/),
    ).toBeInTheDocument()
    expect(screen.getByText(/1200000 embedded \/ 80000 missing/)).toBeInTheDocument()
  })

  it('shows Retry on error state', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (String(url).includes('/api/auth/session')) return mockSessionOk()
      if (String(url).includes('/api/archive/summary')) {
        return {
          ok: false,
          status: 500,
          json: async () => ({ detail: 'boom' }),
        } as Response
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderApp(['/'])

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /Failed to load archive coverage/,
    )
    const retry = screen.getByRole('button', { name: /retry/i })
    expect(retry).toBeInTheDocument()

    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/auth/session')) return mockSessionOk()
      if (String(url).includes('/api/archive/summary')) {
        return mockArchiveSummary()
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    fireEvent.click(retry)

    await waitFor(() => {
      expect(screen.getByText('Archive coverage')).toBeInTheDocument()
    })
  })

  it('redirects unauthenticated visits to /login', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockUnauthorized()
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/'])

    expect(await screen.findByLabelText(/username/i)).toBeInTheDocument()
    expect(screen.queryByTestId('workstation-shell')).not.toBeInTheDocument()
  })
})
