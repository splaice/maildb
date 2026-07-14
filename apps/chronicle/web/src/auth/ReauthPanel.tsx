/**
 * Inline (modal-less) password re-prompt for fresh-auth flows (§15.1).
 * Used when bulk export returns reauth-required.
 */

import { useState, type FormEvent } from 'react'

import { ApiError, apiPost } from '../api/client'
import type { SessionInfo } from '../api/types'
import { useSession } from './useSession'

export interface ReauthPanelProps {
  onSuccess: () => void
  onCancel?: () => void
  reason?: string
}

export function ReauthPanel({
  onSuccess,
  onCancel,
  reason = 'Re-authenticate to continue this export',
}: ReauthPanelProps) {
  const session = useSession()
  const username = session.data?.username ?? ''
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await apiPost<SessionInfo>('/api/auth/login', {
        username,
        password,
      })
      setPassword('')
      onSuccess()
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Invalid credentials')
      } else if (err instanceof ApiError && err.status === 429) {
        setError('Too many attempts — try again later')
      } else {
        setError('Re-authentication failed')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      className="rounded-md border border-steel bg-graphite-900 p-3"
      data-testid="reauth-panel"
      noValidate
    >
      <p className="mb-2 text-sm text-text-primary">{reason}</p>
      <p className="mb-2 text-[11px] text-text-muted">
        Signed in as <span className="font-mono">{username || '…'}</span>
      </p>
      <label htmlFor="reauth-password" className="mb-1 block text-[11px] text-text-muted">
        Password
      </label>
      <input
        id="reauth-password"
        name="password"
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        required
        className="mb-2 w-full rounded-md border border-steel bg-graphite-800 px-2 py-1.5 text-sm text-text-primary"
        data-testid="reauth-password"
      />
      {error ? (
        <p role="alert" className="mb-2 text-[12px] text-conflict" data-testid="reauth-error">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          disabled={submitting || !username}
          className="rounded-md bg-action px-3 py-1.5 text-sm font-medium text-graphite-950 disabled:opacity-60"
          data-testid="reauth-submit"
        >
          {submitting ? 'Verifying…' : 'Confirm identity'}
        </button>
        {onCancel ? (
          <button
            type="button"
            className="rounded-md border border-steel px-3 py-1.5 text-sm text-text-primary"
            onClick={onCancel}
            data-testid="reauth-cancel"
          >
            Cancel
          </button>
        ) : null}
      </div>
    </form>
  )
}
