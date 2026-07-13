import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  mockSessionOk,
  mockUnauthorized,
  renderApp,
} from '../test/test-utils'

describe('LoginPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders labeled username and password fields', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockUnauthorized()
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/login'])

    expect(await screen.findByLabelText(/username/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows Invalid credentials alert on 401', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
        if (String(url).includes('/api/auth/session')) return mockUnauthorized()
        if (String(url).includes('/api/auth/login') && init?.method === 'POST') {
          return mockUnauthorized()
        }
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/login'])
    await screen.findByLabelText(/username/i)

    fireEvent.change(screen.getByLabelText(/username/i), {
      target: { value: 'analyst' },
    })
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'wrong' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Invalid credentials',
    )
  })

  it('navigates to / on successful login', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
        if (String(url).includes('/api/auth/session')) return mockUnauthorized()
        if (String(url).includes('/api/auth/login') && init?.method === 'POST') {
          return mockSessionOk('analyst')
        }
        if (String(url).includes('/api/archive/summary')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              accounts: [],
              date_range: { from: null, to: null },
              counts: {
                messages: 0,
                threads: 0,
                attachments: 0,
                contacts: 0,
              },
              extraction: {
                extracted: 0,
                failed: 0,
                skipped: 0,
                pending: 0,
              },
              embedding: { embedded: 0, missing: 0 },
              versions: { schema: 'maildb', api: '0.1.0' },
            }),
          } as Response
        }
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )

    renderApp(['/login'])
    await screen.findByLabelText(/username/i)

    fireEvent.change(screen.getByLabelText(/username/i), {
      target: { value: 'analyst' },
    })
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByTestId('workstation-shell')).toBeInTheDocument()
    })
    expect(screen.getByText('Life Chronicle')).toBeInTheDocument()
  })
})
