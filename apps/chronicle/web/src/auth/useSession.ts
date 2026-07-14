import { useQuery } from '@tanstack/react-query'

import { apiGet } from '../api/client'
import type { SessionInfo } from '../api/types'

export const sessionQueryKey = ['session'] as const

export function useSession() {
  return useQuery({
    queryKey: sessionQueryKey,
    queryFn: ({ signal }) => apiGet<SessionInfo>('/api/auth/session', signal),
    retry: false,
    staleTime: 60_000,
  })
}
