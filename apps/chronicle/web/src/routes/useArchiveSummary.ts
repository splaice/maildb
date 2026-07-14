import { useQuery } from '@tanstack/react-query'

import { apiGet } from '../api/client'
import type { ArchiveSummary } from '../api/types'

export const archiveSummaryQueryKey = ['archive', 'summary'] as const

export function useArchiveSummary() {
  return useQuery({
    queryKey: archiveSummaryQueryKey,
    queryFn: ({ signal }) => apiGet<ArchiveSummary>('/api/archive/summary', signal),
    retry: false,
  })
}
