import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  apiFetch,
  resetUnauthorizedRedirect,
  setUnauthorizedRedirect,
} from './client'

describe('api/client', () => {
  beforeEach(() => {
    resetUnauthorizedRedirect()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetUnauthorizedRedirect()
  })

  it('includes credentials and returns JSON on success', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ username: 'analyst' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const data = await apiFetch<{ username: string }>('/api/auth/session')
    expect(data).toEqual({ username: 'analyst' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/session',
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('passes AbortSignal through to fetch', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    })
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    await apiFetch('/api/archive/summary', { signal: controller.signal })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/archive/summary',
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('redirects to /login on 401 via injected redirect', async () => {
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Not authenticated' }),
      }),
    )

    await expect(apiFetch('/api/archive/summary')).rejects.toMatchObject({
      status: 401,
    })
    expect(redirect).toHaveBeenCalledWith('/login')
  })

  it('does not redirect on 401 for login endpoint', async () => {
    const redirect = vi.fn()
    setUnauthorizedRedirect(redirect)

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Invalid credentials' }),
      }),
    )

    await expect(
      apiFetch('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: 'x', password: 'y' }),
      }),
    ).rejects.toMatchObject({ status: 401 })
    expect(redirect).not.toHaveBeenCalled()
  })
})
