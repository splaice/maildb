/**
 * JSON fetch wrapper for the Chronicle API.
 * Always sends credentials (session cookie). On 401 (except login), redirects to /login.
 */

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

export type RedirectFn = (path: string) => void

let redirectToLogin: RedirectFn = (path: string) => {
  window.location.assign(path)
}

/** Inject redirect behavior for tests (mockable 401 → /login). */
export function setUnauthorizedRedirect(fn: RedirectFn): void {
  redirectToLogin = fn
}

export function resetUnauthorizedRedirect(): void {
  redirectToLogin = (path: string) => {
    window.location.assign(path)
  }
}

function isLoginPath(path: string): boolean {
  return path === '/api/auth/login' || path.endsWith('/api/auth/login')
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }
  if (init.body != null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers,
    signal: init.signal,
  })

  if (response.status === 401) {
    if (!isLoginPath(path)) {
      redirectToLogin('/login')
    }
    let body: unknown
    try {
      body = await response.json()
    } catch {
      body = undefined
    }
    throw new ApiError(401, 'Unauthorized', body)
  }

  if (!response.ok) {
    let body: unknown
    try {
      body = await response.json()
    } catch {
      body = undefined
    }
    throw new ApiError(response.status, `HTTP ${response.status}`, body)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return apiFetch<T>(path, { method: 'GET', signal })
}

export function apiPost<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
}
