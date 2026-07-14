import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
} from 'react'

import type { BucketPoint } from '../api/types'
import {
  bucketWidthPx,
  formatPeriodRange,
  parseUnit,
  timeForX,
  ticksFor,
  UNIT_MS,
  type Unit,
  type Viewport,
  wheelZoomFactor,
  xForTime,
  zoomViewport,
} from './timeScale'

const COLORS = {
  steel: '#344050',
  action: '#5aa7ff',
  attachment: '#55c2a3',
  muted: '#91a0b5',
  primary: '#e6edf3',
  bg: '#0d1117',
  graphite900: '#151b23',
} as const

const AXIS_H = 28
const LANE_H = 72
const LANE_LABEL_W = 52
const LANE_GAP = 4

export interface TimelineCanvasProps {
  viewport: Viewport
  extent: Viewport | null
  unit: string
  messages: BucketPoint[]
  attachments: BucketPoint[]
  isFetching: boolean
  brush: Viewport | null
  /** Currently selected bucket (for highlight outline). */
  selectedBucket?: { bucketIso: string; lane: string } | null
  onViewportChange: (vp: Viewport) => void
  onBrushChange: (brush: Viewport | null) => void
  onWidthChange: (width: number) => void
  /** Fired when a bar is clicked (lane + bucket ISO). */
  onSelectBucket?: (bucketIso: string, laneName: string) => void
}

/**
 * Pure hit-test: which bucket bar contains plot-local x?
 * Bars are positioned at xForTime(bucketStart) with width bucketWidthPx−1.
 * Exported for unit tests.
 */
export function bucketAtX(
  plotX: number,
  viewport: Viewport,
  plotW: number,
  unit: Unit,
  points: BucketPoint[],
): string | null {
  if (plotW <= 0 || points.length === 0) return null
  const bw = Math.max(1, bucketWidthPx(unit, viewport, plotW) - 1)
  // Prefer the bucket whose start is nearest and whose bar covers plotX.
  let hit: string | null = null
  let bestDist = Infinity
  for (const pt of points) {
    const t = Date.parse(pt.bucket)
    if (!Number.isFinite(t)) continue
    const x = xForTime(t, viewport, plotW)
    if (plotX >= x && plotX < x + bw) {
      const mid = x + bw / 2
      const dist = Math.abs(plotX - mid)
      if (dist < bestDist) {
        bestDist = dist
        hit = pt.bucket
      }
    }
  }
  // Fallback: snap to nearest bucket start by time if inside its span (unit width).
  if (!hit) {
    const t = timeForX(plotX, viewport, plotW)
    const unitMs = UNIT_MS[unit]
    for (const pt of points) {
      const start = Date.parse(pt.bucket)
      if (!Number.isFinite(start)) continue
      if (t >= start && t < start + unitMs) {
        hit = pt.bucket
        break
      }
    }
  }
  return hit
}

/** Which lane (0 = messages, 1 = attachments) contains localY, or null. */
export function laneAtY(localY: number): 'messages' | 'attachments' | null {
  if (localY <= AXIS_H) return null
  const messagesTop = AXIS_H + LANE_GAP
  const attachmentsTop = messagesTop + LANE_H + LANE_GAP
  if (localY >= messagesTop && localY < messagesTop + LANE_H) return 'messages'
  if (localY >= attachmentsTop && localY < attachmentsTop + LANE_H) {
    return 'attachments'
  }
  return null
}

function sumCounts(points: BucketPoint[]): number {
  return points.reduce((s, p) => s + p.count, 0)
}

function maxCount(points: BucketPoint[]): number {
  let m = 0
  for (const p of points) if (p.count > m) m = p.count
  return m
}

function buildAriaLabel(
  viewport: Viewport,
  messages: BucketPoint[],
  attachments: BucketPoint[],
): string {
  const range = formatPeriodRange(viewport)
  const msg = sumCounts(messages).toLocaleString()
  const att = sumCounts(attachments).toLocaleString()
  return `Timeline, ${range}, ${msg} messages, ${att} attachments`
}

/**
 * Canvas-2D multi-lane timeline renderer. All time↔pixel math lives in
 * timeScale.ts; this component is a thin interactive surface.
 */
export function TimelineCanvas({
  viewport,
  extent,
  unit,
  messages,
  attachments,
  isFetching,
  brush,
  selectedBucket = null,
  onViewportChange,
  onBrushChange,
  onWidthChange,
  onSelectBucket,
}: TimelineCanvasProps) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const wheelHandlerRef = useRef<(e: globalThis.WheelEvent) => void>(() => {})

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const handler = (e: globalThis.WheelEvent) => wheelHandlerRef.current(e)
    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [])
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [cssSize, setCssSize] = useState({ w: 0, h: AXIS_H + 2 * (LANE_H + LANE_GAP) })
  const brushDrag = useRef<{ startX: number; curX: number } | null>(null)
  const rafRef = useRef<number>(0)
  const shimmerRef = useRef(0)

  // ResizeObserver × devicePixelRatio (fallback for jsdom / older environments)
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const h = AXIS_H + 2 * (LANE_H + LANE_GAP)
    const apply = (w: number) => {
      const width = Math.max(0, Math.floor(w))
      setCssSize({ w: width, h })
      onWidthChange(width)
    }
    if (typeof ResizeObserver === 'undefined') {
      apply(el.clientWidth || 920)
      return
    }
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      apply(entry.contentRect.width)
    })
    ro.observe(el)
    apply(el.clientWidth)
    return () => ro.disconnect()
  }, [onWidthChange])

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || cssSize.w <= 0) return
    const dpr = window.devicePixelRatio || 1
    const w = cssSize.w
    const h = cssSize.h
    if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
      canvas.width = Math.floor(w * dpr)
      canvas.height = Math.floor(h * dpr)
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
    }
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    ctx.fillStyle = COLORS.bg
    ctx.fillRect(0, 0, w, h)

    const plotW = Math.max(1, w - LANE_LABEL_W)
    const unitTyped: Unit = parseUnit(unit)
    const bw = Math.max(1, bucketWidthPx(unitTyped, viewport, plotW) - 1)

    // Axis
    ctx.fillStyle = COLORS.graphite900
    ctx.fillRect(0, 0, w, AXIS_H)
    const ticks = ticksFor(viewport, plotW)
    ctx.strokeStyle = COLORS.steel
    ctx.fillStyle = COLORS.muted
    ctx.font = '11px Inter, system-ui, sans-serif'
    ctx.textBaseline = 'middle'
    for (const tick of ticks) {
      const x = LANE_LABEL_W + xForTime(tick.timeMs, viewport, plotW)
      ctx.beginPath()
      ctx.moveTo(x, AXIS_H - (tick.major ? 10 : 6))
      ctx.lineTo(x, AXIS_H)
      ctx.stroke()
      if (tick.major) {
        ctx.fillText(tick.label, x + 2, AXIS_H / 2)
      }
    }

    const lanes: { name: string; points: BucketPoint[]; color: string }[] = [
      { name: 'messages', points: messages, color: COLORS.action },
      { name: 'attachments', points: attachments, color: COLORS.attachment },
    ]

    lanes.forEach((lane, i) => {
      const top = AXIS_H + LANE_GAP + i * (LANE_H + LANE_GAP)
      const max = maxCount(lane.points) || 1
      ctx.fillStyle = COLORS.graphite900
      ctx.fillRect(0, top, w, LANE_H)

      // Count scale label
      ctx.fillStyle = COLORS.muted
      ctx.font = '10px Inter, system-ui, sans-serif'
      ctx.textBaseline = 'top'
      ctx.fillText(`0–${maxCount(lane.points).toLocaleString()}`, 4, top + 4)
      ctx.fillText(lane.name, 4, top + 18)

      const barMaxH = LANE_H - 12
      for (const pt of lane.points) {
        const t = Date.parse(pt.bucket)
        if (!Number.isFinite(t)) continue
        const x = LANE_LABEL_W + xForTime(t, viewport, plotW)
        const barH = (pt.count / max) * barMaxH
        const barY = top + LANE_H - 4 - barH
        ctx.fillStyle = lane.color
        ctx.globalAlpha = 0.85
        ctx.fillRect(x, barY, bw, barH)
        ctx.globalAlpha = 1
        // Selected bucket: 1px action-blue outline
        if (
          selectedBucket &&
          selectedBucket.lane === lane.name &&
          selectedBucket.bucketIso === pt.bucket
        ) {
          ctx.strokeStyle = COLORS.action
          ctx.lineWidth = 1
          ctx.strokeRect(x + 0.5, barY + 0.5, Math.max(0, bw - 1), Math.max(0, barH - 1))
        }
      }
    })

    // Brush overlay
    const activeBrush = brushDrag.current
      ? {
          fromMs: timeForX(
            Math.min(brushDrag.current.startX, brushDrag.current.curX) - LANE_LABEL_W,
            viewport,
            plotW,
          ),
          toMs: timeForX(
            Math.max(brushDrag.current.startX, brushDrag.current.curX) - LANE_LABEL_W,
            viewport,
            plotW,
          ),
        }
      : brush

    if (activeBrush && activeBrush.toMs > activeBrush.fromMs) {
      const x0 = LANE_LABEL_W + xForTime(activeBrush.fromMs, viewport, plotW)
      const x1 = LANE_LABEL_W + xForTime(activeBrush.toMs, viewport, plotW)
      ctx.fillStyle = COLORS.action
      ctx.globalAlpha = 0.15
      ctx.fillRect(x0, 0, x1 - x0, h)
      ctx.globalAlpha = 1
      ctx.strokeStyle = COLORS.action
      ctx.lineWidth = 1
      ctx.strokeRect(x0 + 0.5, 0.5, x1 - x0 - 1, h - 1)
    }

    // Empty viewport message
    const total = sumCounts(messages) + sumCounts(attachments)
    if (total === 0 && !isFetching) {
      ctx.fillStyle = COLORS.muted
      ctx.font = '13px Inter, system-ui, sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText('No activity in range', w / 2, h / 2)
      if (extent) {
        if (viewport.toMs < extent.fromMs) {
          ctx.fillText(
            `activity after ${formatPeriodRange({ fromMs: extent.fromMs, toMs: extent.fromMs })} →`,
            w / 2,
            h / 2 + 18,
          )
        } else if (viewport.fromMs > extent.toMs) {
          ctx.fillText(
            `← activity before ${new Date(extent.toMs).getUTCFullYear()}`,
            w / 2,
            h / 2 + 18,
          )
        } else if (viewport.toMs <= extent.fromMs || viewport.fromMs >= extent.toMs) {
          ctx.fillText(
            `← activity before ${new Date(extent.toMs).getUTCFullYear()}`,
            w / 2,
            h / 2 + 18,
          )
        }
      }
      ctx.textAlign = 'start'
    }

    // Loading shimmer (2px indeterminate line at top)
    if (isFetching) {
      const phase = (shimmerRef.current % 1000) / 1000
      const sw = w * 0.3
      const sx = (w + sw) * phase - sw
      const grad = ctx.createLinearGradient(sx, 0, sx + sw, 0)
      grad.addColorStop(0, 'transparent')
      grad.addColorStop(0.5, COLORS.action)
      grad.addColorStop(1, 'transparent')
      ctx.fillStyle = grad
      ctx.fillRect(0, 0, w, 2)
    }
  }, [
    attachments,
    brush,
    cssSize.h,
    cssSize.w,
    extent,
    isFetching,
    messages,
    selectedBucket,
    unit,
    viewport,
  ])

  // rAF redraw on data/viewport change (no continuous loop unless shimmering)
  useEffect(() => {
    let alive = true
    const frame = () => {
      if (!alive) return
      draw()
      if (isFetching) {
        shimmerRef.current = performance.now()
        rafRef.current = requestAnimationFrame(frame)
      }
    }
    rafRef.current = requestAnimationFrame(frame)
    return () => {
      alive = false
      cancelAnimationFrame(rafRef.current)
    }
  }, [draw, isFetching])

  const plotWidth = () => Math.max(1, cssSize.w - LANE_LABEL_W)

  // React attaches JSX onWheel passively (preventDefault is ignored), so the
  // wheel listener is bound natively with { passive: false } in an effect
  // below, reading the latest handler through a ref.
  const onWheel = (e: globalThis.WheelEvent) => {
    e.preventDefault()
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    const localX = e.clientX - rect.left
    const plotX = Math.max(0, localX - LANE_LABEL_W)

    if (e.ctrlKey || e.metaKey) {
      const factor = wheelZoomFactor(e.deltaY)
      onViewportChange(zoomViewport(viewport, factor, plotX, plotWidth()))
    } else {
      const delta = e.deltaY + e.deltaX
      const span = viewport.toMs - viewport.fromMs
      const deltaMs = (delta / plotWidth()) * span
      onViewportChange({
        fromMs: viewport.fromMs + deltaMs,
        toMs: viewport.toMs + deltaMs,
      })
    }
  }

  wheelHandlerRef.current = onWheel

  const clickRef = useRef<{ x: number; y: number; t: number } | null>(null)

  const onPointerDown = (e: PointerEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    const localY = e.clientY - rect.top
    const localX = e.clientX - rect.left
    // Drag on the axis region = brush
    if (localY <= AXIS_H) {
      ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
      brushDrag.current = { startX: localX, curX: localX }
      clickRef.current = null
      return
    }
    // Record potential bar click (lane region)
    clickRef.current = { x: localX, y: localY, t: performance.now() }
  }

  const onPointerMove = (e: PointerEvent) => {
    if (!brushDrag.current) return
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    brushDrag.current = {
      ...brushDrag.current,
      curX: e.clientX - rect.left,
    }
    // Trigger redraw
    cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(draw)
  }

  const onPointerUp = (e: PointerEvent) => {
    if (brushDrag.current) {
      const rect = wrapRef.current?.getBoundingClientRect()
      if (!rect) {
        brushDrag.current = null
        return
      }
      const { startX, curX } = brushDrag.current
      brushDrag.current = null
      const pw = plotWidth()
      const x0 = Math.min(startX, curX) - LANE_LABEL_W
      const x1 = Math.max(startX, curX) - LANE_LABEL_W
      if (Math.abs(x1 - x0) < 4) {
        onBrushChange(null)
        return
      }
      onBrushChange({
        fromMs: timeForX(Math.max(0, x0), viewport, pw),
        toMs: timeForX(Math.min(pw, x1), viewport, pw),
      })
      void e
      return
    }

    // Bar click hit-test
    const pending = clickRef.current
    clickRef.current = null
    if (!pending || !onSelectBucket) return
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    const localX = e.clientX - rect.left
    const localY = e.clientY - rect.top
    // Ignore if pointer moved more than a few px (drag-like)
    if (Math.hypot(localX - pending.x, localY - pending.y) > 6) return
    const lane = laneAtY(localY)
    if (!lane) return
    const pw = plotWidth()
    const plotX = localX - LANE_LABEL_W
    if (plotX < 0) return
    const unitTyped = parseUnit(unit)
    const points = lane === 'messages' ? messages : attachments
    const bucketIso = bucketAtX(plotX, viewport, pw, unitTyped, points)
    if (bucketIso) onSelectBucket(bucketIso, lane)
  }

  const onDoubleClick = (e: MouseEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    const plotX = Math.max(0, e.clientX - rect.left - LANE_LABEL_W)
    // Zoom ×0.25 span anchored at pointer
    onViewportChange(zoomViewport(viewport, 0.25, plotX, plotWidth()))
  }

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      onBrushChange(null)
      brushDrag.current = null
    }
  }

  return (
    <div
      ref={wrapRef}
      className="relative w-full touch-none overflow-hidden rounded-lg border border-steel"
      style={{ height: cssSize.h }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onDoubleClick={onDoubleClick}
      onKeyDown={onKeyDown}
      tabIndex={0}
      data-testid="timeline-canvas-wrap"
    >
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={buildAriaLabel(viewport, messages, attachments)}
        data-testid="timeline-canvas"
      />
    </div>
  )
}
