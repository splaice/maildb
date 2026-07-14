import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ReauthPanel } from './ReauthPanel'

function renderPanel(onSuccess = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  client.setQueryData(['session'], { username: 'owner' })
  return {
    onSuccess,
    ...render(
      <QueryClientProvider client={client}>
        <ReauthPanel onSuccess={onSuccess} onCancel={vi.fn()} />
      </QueryClientProvider>,
    ),
  }
}

describe('ReauthPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts login and calls onSuccess', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/api/auth/login') && init?.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ username: 'owner' }),
          headers: new Headers({ 'Content-Type': 'application/json' }),
        } as Response
      }
      throw new Error(`unexpected: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const { onSuccess } = renderPanel()
    fireEvent.change(screen.getByTestId('reauth-password'), {
      target: { value: 'secret' },
    })
    fireEvent.click(screen.getByTestId('reauth-submit'))

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalled()
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/login',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('shows invalid credentials on 401', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Invalid credentials' }),
        headers: new Headers(),
      } as Response),
    )

    renderPanel()
    fireEvent.change(screen.getByTestId('reauth-password'), {
      target: { value: 'wrong' },
    })
    fireEvent.click(screen.getByTestId('reauth-submit'))

    expect(await screen.findByTestId('reauth-error')).toHaveTextContent(
      'Invalid credentials',
    )
  })
})
