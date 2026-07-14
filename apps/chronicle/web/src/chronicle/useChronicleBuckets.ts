import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { apiPost } from '../api/client'
import type { ChronicleBuckets, ChronicleRequest, QueryScope } from '../api/types'
import { type Viewport, viewportToIso } from './timeScale'

export const CHRONICLE_LANES = ['messages', 'attachments'] as const

/** Quantize pixel width to 32px steps to avoid resize churn. */
export function quantizePixelWidth(pixelWidth: number): number {
  if (!Number.isFinite(pixelWidth) || pixelWidth <= 0) return 32
  return Math.max(32, Math.round(pixelWidth / 32) * 32)
}

export function chronicleBucketsQueryKey(parts: {
  scopeFingerprint: string
  viewportFromIso: string
  viewportToIso: string
  pixelWidth: number
  lanes: readonly string[]
}) {
  return [
    'chronicle',
    'buckets',
    parts.scopeFingerprint,
    parts.viewportFromIso,
    parts.viewportToIso,
    parts.pixelWidth,
    [...parts.lanes].join(','),
  ] as const
}

/**
 * Fingerprint-relevant scope key for the query. Full QueryScope serialization
 * lands in task 1.3; for now empty scope is the only client value.
 */
export function scopeKey(scope: QueryScope | undefined): string {
  if (!scope) return 'qs_empty'
  return JSON.stringify({
    version: scope.version ?? 1,
    date: scope.date ?? null,
    mailboxes: scope.mailboxes ?? [],
    senders: scope.senders ?? [],
  })
}

export interface UseChronicleBucketsArgs {
  viewport: Viewport
  pixelWidth: number
  scope?: QueryScope
  lanes?: readonly string[]
  /** When false, the query is disabled (e.g. zero width). */
  enabled?: boolean
}

/**
 * TanStack Query hook for POST /api/chronicle/buckets.
 *
 * - Keyed by fingerprint-relevant scope, viewport ISO range, quantized pixel
 *   width, and lanes.
 * - Viewport-driven refetch is debounced 150ms (interaction state stays local).
 * - POST uses the query AbortSignal so stale pans cancel (PERF-003).
 * - placeholderData: keepPreviousData so prior lanes stay during refetch (§16.2).
 */
export function useChronicleBuckets({
  viewport,
  pixelWidth,
  scope,
  lanes = CHRONICLE_LANES,
  enabled = true,
}: UseChronicleBucketsArgs) {
  const quantizedWidth = quantizePixelWidth(pixelWidth)
  const iso = viewportToIso(viewport)

  // Debounce only the query-key viewport; callers keep live interaction state.
  const [debouncedIso, setDebouncedIso] = useState(iso)
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedIso(iso)
    }, 150)
    return () => window.clearTimeout(handle)
  }, [iso.from, iso.to])

  const fp = scopeKey(scope)
  const laneList = useMemo(() => [...lanes], [lanes])

  const queryKey = chronicleBucketsQueryKey({
    scopeFingerprint: fp,
    viewportFromIso: debouncedIso.from,
    viewportToIso: debouncedIso.to,
    pixelWidth: quantizedWidth,
    lanes: laneList,
  })

  return useQuery({
    queryKey,
    queryFn: ({ signal }) => {
      const body: ChronicleRequest = {
        scope: scope ?? {},
        viewport: {
          from: debouncedIso.from,
          to: debouncedIso.to,
        },
        pixel_width: quantizedWidth,
        aggregation: 'auto',
        lanes: laneList,
      }
      return apiPost<ChronicleBuckets>('/api/chronicle/buckets', body, signal)
    },
    enabled: enabled && quantizedWidth >= 32,
    placeholderData: keepPreviousData,
    retry: false,
  })
}
