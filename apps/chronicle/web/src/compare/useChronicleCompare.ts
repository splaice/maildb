import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { apiPost } from '../api/client'
import type {
  ChronicleCompare,
  ChronicleCompareRequest,
  QueryScope,
} from '../api/types'
import { quantizePixelWidth, scopeKey } from '../chronicle/useChronicleBuckets'
import { type Viewport, viewportToIso } from '../chronicle/timeScale'

export function chronicleCompareQueryKey(parts: {
  scopeFingerprint: string
  aFrom: string
  aTo: string
  bFrom: string
  bTo: string
  pixelWidth: number
  lanes: readonly string[]
}) {
  return [
    'chronicle',
    'compare',
    parts.scopeFingerprint,
    parts.aFrom,
    parts.aTo,
    parts.bFrom,
    parts.bTo,
    parts.pixelWidth,
    [...parts.lanes].join(','),
  ] as const
}

export interface UseChronicleCompareArgs {
  a: Viewport
  b: Viewport
  pixelWidth: number
  scope?: QueryScope
  lanes?: readonly string[]
  enabled?: boolean
}

/**
 * TanStack Query hook for POST /api/chronicle/compare.
 * Debounces range changes 150ms; cancels in-flight via AbortSignal.
 */
export function useChronicleCompare({
  a,
  b,
  pixelWidth,
  scope,
  lanes = ['messages', 'attachments', 'people'],
  enabled = true,
}: UseChronicleCompareArgs) {
  const quantizedWidth = quantizePixelWidth(pixelWidth)
  const aIso = viewportToIso(a)
  const bIso = viewportToIso(b)

  const [debounced, setDebounced] = useState({ a: aIso, b: bIso })
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebounced({ a: aIso, b: bIso })
    }, 150)
    return () => window.clearTimeout(handle)
  }, [aIso.from, aIso.to, bIso.from, bIso.to])

  const fp = scopeKey(scope)
  const laneList = useMemo(() => [...lanes], [lanes])

  const queryKey = chronicleCompareQueryKey({
    scopeFingerprint: fp,
    aFrom: debounced.a.from,
    aTo: debounced.a.to,
    bFrom: debounced.b.from,
    bTo: debounced.b.to,
    pixelWidth: quantizedWidth,
    lanes: laneList,
  })

  return useQuery({
    queryKey,
    queryFn: ({ signal }) => {
      const body: ChronicleCompareRequest = {
        scope: scope ?? {},
        a: { from: debounced.a.from, to: debounced.a.to },
        b: { from: debounced.b.from, to: debounced.b.to },
        pixel_width: quantizedWidth,
        lanes: laneList,
      }
      return apiPost<ChronicleCompare>('/api/chronicle/compare', body, signal)
    },
    enabled: enabled && quantizedWidth >= 32,
    placeholderData: keepPreviousData,
    retry: false,
  })
}
