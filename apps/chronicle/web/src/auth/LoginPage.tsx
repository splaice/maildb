import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router'

import { ApiError, apiPost } from '../api/client'
import type { SessionInfo } from '../api/types'
import { sessionQueryKey, useSession } from './useSession'

export function LoginPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const session = useSession()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (session.isSuccess && session.data) {
    return <Navigate to="/" replace />
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const info = await apiPost<SessionInfo>('/api/auth/login', {
        username,
        password,
      })
      queryClient.setQueryData(sessionQueryKey, info)
      navigate('/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Invalid credentials')
      } else {
        setError('Login failed')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-graphite-950 p-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-lg border border-steel bg-graphite-900 p-6"
        noValidate
      >
        <h1 className="mb-4 text-base font-medium text-text-primary">
          Life Chronicle
        </h1>
        <p className="mb-6 text-text-muted">Sign in to the archive workstation</p>

        <div className="mb-4">
          <label htmlFor="login-username" className="mb-1 block text-text-muted">
            Username
          </label>
          <input
            id="login-username"
            name="username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            className="w-full rounded-md border border-steel bg-graphite-800 px-3 py-2 text-text-primary"
          />
        </div>

        <div className="mb-4">
          <label htmlFor="login-password" className="mb-1 block text-text-muted">
            Password
          </label>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="w-full rounded-md border border-steel bg-graphite-800 px-3 py-2 text-text-primary"
          />
        </div>

        {error ? (
          <p role="alert" className="mb-4 text-conflict">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-action px-3 py-2 font-medium text-graphite-950 disabled:opacity-60"
        >
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
