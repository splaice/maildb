import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'

import { apiPost } from '../api/client'
import { sessionQueryKey, useSession } from '../auth/useSession'
import { archiveSummaryQueryKey } from '../routes/useArchiveSummary'

export function CommandBar() {
  const { data: session } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  async function logout() {
    try {
      await apiPost<{ status: string }>('/api/auth/logout')
    } catch {
      // Still clear client session even if the network call fails.
    }
    queryClient.removeQueries({ queryKey: sessionQueryKey })
    queryClient.removeQueries({ queryKey: archiveSummaryQueryKey })
    navigate('/login', { replace: true })
  }

  return (
    <header
      className="col-span-3 flex items-center gap-3 border-b border-steel bg-graphite-900 px-3"
      style={{ height: 56 }}
    >
      <div className="shrink-0 font-medium text-text-primary">Life Chronicle</div>
      <input
        type="search"
        disabled
        placeholder="Search, Ask, or Explore — /"
        aria-label="Universal search (coming soon)"
        className="min-w-0 flex-1 rounded-md border border-steel bg-graphite-800 px-3 py-1.5 text-text-muted placeholder:text-text-muted disabled:cursor-not-allowed disabled:opacity-70"
      />
      <div className="flex shrink-0 items-center gap-2">
        <span className="text-text-muted">{session?.username ?? ''}</span>
        <button
          type="button"
          onClick={() => void logout()}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
        >
          Logout
        </button>
      </div>
    </header>
  )
}
