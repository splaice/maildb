import { useCallback, useEffect, useRef, useState, type PointerEvent } from 'react'

import type { BucketPoint } from '../api/types'
import { timeForX, type Viewport, xForTime } from './timeScale'

const COLORS = {
  steel: '#344050',
  action: '#5aa7ff',
  muted: '#91a0b5',
  bg: '#151b23',
  bar: '#344050',
} as const

const HEIGHT = 48
const MIN_WINDOW_PX = 8

export interface DensityNavigatorProps {
  extent: Viewport
  viewport: Viewport
  densityBuckets: BucketPoint[]
  onViewportChange: (vp: Viewport) => void
}

type DragMode =
  | { kind: 'move'; originX: number; originVp: Viewport }
  | { kind: 'resize-left'; originX: number; originVp: Viewport }
  | { kind: 'resize-right'; originX: number; originVp: Viewport }
  | null

/**
 * Full-width 48px density strip over the FULL extent with a viewport window.
 *
 * aria-hidden: keyboard equivalence is via toolbar / table (Phase 5 full a11y).
 * Density data comes from the same buckets response; keep previous while loading.
 */
export function DensityNavigator({
  extent,
  viewport,
  densityBuckets,
  onViewportChange,
}: DensityNavigatorProps) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [width, setWidth] = useState(0)
  const dragRef = useRef<DragMode>(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    if (typeof ResizeObserver === 'undefined') {
      setWidth(el.clientWidth || 920)
      return
    }
    const ro = new ResizeObserver((entries) => {
      const w = Math.floor(entries[0]?.contentRect.width ?? 0)
      setWidth(w)
    })
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || width <= 0) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.floor(width * dpr)
    canvas.height = Math.floor(HEIGHT * dpr)
    canvas.style.width = `${width}px`
    canvas.style.height = `${HEIGHT}px`
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = COLORS.bg
    ctx.fillRect(0, 0, width, HEIGHT)

    let max = 1
    for (const b of densityBuckets) if (b.count > max) max = b.count

    const extentSpan = Math.max(1, extent.toMs - extent.fromMs)
    // Approximate bar width from density spacing
    let barW = 2
    if (densityBuckets.length >= 2) {
      const t0 = Date.parse(densityBuckets[0]!.bucket)
      const t1 = Date.parse(densityBuckets[1]!.bucket)
      if (Number.isFinite(t0) && Number.isFinite(t1) && t1 > t0) {
        barW = Math.max(1, ((t1 - t0) / extentSpan) * width - 1)
      }
    } else if (densityBuckets.length === 1) {
      barW = Math.max(2, width / 40)
    }

    ctx.fillStyle = COLORS.bar
    for (const b of densityBuckets) {
      const t = Date.parse(b.bucket)
      if (!Number.isFinite(t)) continue
      const x = xForTime(t, extent, width)
      const h = (b.count / max) * (HEIGHT - 8)
      ctx.globalAlpha = 0.7
      ctx.fillRect(x, HEIGHT - 4 - h, barW, h)
    }
    ctx.globalAlpha = 1

    // Viewport window
    const x0 = xForTime(viewport.fromMs, extent, width)
    const x1 = xForTime(viewport.toMs, extent, width)
    const wx = Math.min(x0, x1)
    const ww = Math.max(MIN_WINDOW_PX, Math.abs(x1 - x0))
    ctx.fillStyle = COLORS.action
    ctx.globalAlpha = 0.12
    ctx.fillRect(wx, 0, ww, HEIGHT)
    ctx.globalAlpha = 1
    ctx.strokeStyle = COLORS.action
    ctx.lineWidth = 1
    ctx.strokeRect(wx + 0.5, 0.5, ww - 1, HEIGHT - 1)

    // Edge handles
    ctx.fillStyle = COLORS.action
    ctx.fillRect(wx, 0, 3, HEIGHT)
    ctx.fillRect(wx + ww - 3, 0, 3, HEIGHT)
  }, [densityBuckets, extent, viewport, width])

  useEffect(() => {
    const id = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(id)
  }, [draw])

  const applyDrag = (clientX: number) => {
    const mode = dragRef.current
    const el = wrapRef.current
    if (!mode || !el || width <= 0) return
    const rect = el.getBoundingClientRect()
    const x = clientX - rect.left
    const dx = x - mode.originX
    const span = mode.originVp.toMs - mode.originVp.fromMs

    if (mode.kind === 'move') {
      const dMs = (dx / width) * (extent.toMs - extent.fromMs)
      onViewportChange({
        fromMs: mode.originVp.fromMs + dMs,
        toMs: mode.originVp.toMs + dMs,
      })
      return
    }

    const minSpan = (MIN_WINDOW_PX / width) * (extent.toMs - extent.fromMs)
    if (mode.kind === 'resize-left') {
      const dMs = (dx / width) * (extent.toMs - extent.fromMs)
      let fromMs = mode.originVp.fromMs + dMs
      const toMs = mode.originVp.toMs
      if (toMs - fromMs < minSpan) fromMs = toMs - minSpan
      onViewportChange({ fromMs, toMs })
    } else if (mode.kind === 'resize-right') {
      const dMs = (dx / width) * (extent.toMs - extent.fromMs)
      const fromMs = mode.originVp.fromMs
      let toMs = mode.originVp.toMs + dMs
      if (toMs - fromMs < minSpan) toMs = fromMs + minSpan
      onViewportChange({ fromMs, toMs })
    }
    void span
  }

  const onPointerDown = (e: PointerEvent) => {
    if (width <= 0) return
    const el = wrapRef.current
    if (!el) return
    el.setPointerCapture(e.pointerId)
    const rect = el.getBoundingClientRect()
    const x = e.clientX - rect.left
    const x0 = xForTime(viewport.fromMs, extent, width)
    const x1 = xForTime(viewport.toMs, extent, width)
    const left = Math.min(x0, x1)
    const right = Math.max(x0, x1)
    const edge = 6

    if (x >= left - edge && x <= left + edge) {
      dragRef.current = {
        kind: 'resize-left',
        originX: x,
        originVp: { ...viewport },
      }
    } else if (x >= right - edge && x <= right + edge) {
      dragRef.current = {
        kind: 'resize-right',
        originX: x,
        originVp: { ...viewport },
      }
    } else if (x >= left && x <= right) {
      dragRef.current = {
        kind: 'move',
        originX: x,
        originVp: { ...viewport },
      }
    } else {
      // Click outside window = center viewport there
      const t = timeForX(x, extent, width)
      const half = (viewport.toMs - viewport.fromMs) / 2
      onViewportChange({ fromMs: t - half, toMs: t + half })
      dragRef.current = null
    }
  }

  const onPointerMove = (e: PointerEvent) => {
    if (!dragRef.current) return
    applyDrag(e.clientX)
  }

  const onPointerUp = () => {
    dragRef.current = null
  }

  return (
    <div
      ref={wrapRef}
      className="w-full touch-none overflow-hidden rounded-md border border-steel"
      style={{ height: HEIGHT }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      data-testid="density-navigator"
      // Density navigator is pointer-first; keyboard users use toolbar / table.
      // Full a11y pass (role=slider) is Phase 5.
      aria-hidden="true"
    >
      <canvas ref={canvasRef} />
    </div>
  )
}
