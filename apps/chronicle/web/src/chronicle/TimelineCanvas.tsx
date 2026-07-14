import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
} from 'react'

import type { BucketPoint, LaneData } from '../api/types'
import { isTopPeopleLane } from '../api/types'
import {
  AXIS_H,
  BAR_LANE_COLORS,
  LANE_GAP,
  LANE_H,
  LANE_LABEL_W,
  MULTIROW_HEADER_H,
  MULTIROW_ROW_H,
  PEOPLE_CYAN,
  barsPoints,
  canvasHeightForLanes,
  laneAtY as laneAtYFromLayout,
  layoutLanes,
  type LaneSpec,
} from './laneModel'
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

export interface TimelineCanvasProps {
  viewport: Viewport
  extent: Viewport | null
  unit: string
  /** Ordered lane specs (store order). */
  lanes: LaneSpec[]
  /** Lane key → server payload (bars series or top_people contacts). */
  laneData: Record<string, LaneData>
  isFetching: boolean
  brush: Viewport | null
  /** Currently selected bucket (for highlight outline). */
  selectedBucket?: { bucketIso: string; lane: string } | null
  onViewportChange: (vp: Viewport) => void
  onBrushChange: (brush: Viewport | null) => void
  onWidthChange: (width: number) => void
  /** Fired when a bar/mark is clicked (lane + bucket ISO). */
  onSelectBucket?: (bucketIso: string, laneName: string) => void
  /**
   * Double-click a mark/bucket enters focus on that bucket's span.
   * Alt+double-click keeps zoom ×0.25 (documented in toolbar hint).
   */
  onFocusBucket?: (period: Viewport) => void
}

/**
 * Pure double-click policy for the canvas surface.
 * - altKey → zoom ×0.25 (retain prior zoom-on-double-click)
 * - bucket hit without alt → enter focus on that span
 * - no hit → zoom ×0.25 fallback
 * Exported for unit tests.
 */
export function resolveDoubleClickAction(args: {
  altKey: boolean
  bucketHit: Viewport | null
  viewport: Viewport
  plotX: number
  plotW: number
}): { kind: 'zoom'; viewport: Viewport } | { kind: 'focus'; period: Viewport } {
  if (args.altKey) {
    return {
      kind: 'zoom',
      viewport: zoomViewport(args.viewport, 0.25, args.plotX, args.plotW),
    }
  }
  if (args.bucketHit) {
    return { kind: 'focus', period: args.bucketHit }
  }
  return {
    kind: 'zoom',
    viewport: zoomViewport(args.viewport, 0.25, args.plotX, args.plotW),
  }
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

/** @deprecated Prefer layout-aware hit-test; kept for tests with default two bars. */
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

function truncateLabel(text: string, maxCh: number): string {
  if (text.length <= maxCh) return text
  return text.slice(0, Math.max(0, maxCh - 1)) + '…'
}

function buildAriaLabel(
  viewport: Viewport,
  lanes: LaneSpec[],
  laneData: Record<string, LaneData>,
): string {
  const range = formatPeriodRange(viewport)
  const parts = lanes.map((spec) => {
    if (spec.kind === 'bars') {
      const n = sumCounts(barsPoints(laneData, spec.key)).toLocaleString()
      return `${n} ${spec.label.toLowerCase()}`
    }
    const tp = laneData[spec.key]
    const n = isTopPeopleLane(tp) ? tp.contacts.length : 0
    return `${n} contacts`
  })
  return `Timeline, ${range}, ${parts.join(', ')}`
}

/**
 * Canvas-2D multi-lane timeline renderer. All time↔pixel math lives in
 * timeScale.ts; this component is a thin interactive surface.
 */
export function TimelineCanvas({
  viewport,
  extent,
  unit,
  lanes,
  laneData,
  isFetching,
  brush,
  selectedBucket = null,
  onViewportChange,
  onBrushChange,
  onWidthChange,
  onSelectBucket,
  onFocusBucket,
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
  const contentH = useMemo(
    () => canvasHeightForLanes(lanes, laneData),
    [lanes, laneData],
  )
  const layout = useMemo(() => layoutLanes(lanes, laneData), [lanes, laneData])
  const [cssSize, setCssSize] = useState({ w: 0, h: contentH })
  const brushDrag = useRef<{ startX: number; curX: number } | null>(null)
  const rafRef = useRef<number>(0)
  const shimmerRef = useRef(0)

  // Keep height in sync with lane config / top_people row count.
  useEffect(() => {
    setCssSize((prev) => (prev.h === contentH ? prev : { ...prev, h: contentH }))
  }, [contentH])

  // ResizeObserver × devicePixelRatio (fallback for jsdom / older environments)
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const apply = (w: number) => {
      const width = Math.max(0, Math.floor(w))
      setCssSize({ w: width, h: contentH })
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
  }, [onWidthChange, contentH])

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

    for (const block of layout) {
      const { spec, top, height } = block
      ctx.fillStyle = COLORS.graphite900
      ctx.fillRect(0, top, w, height)

      if (spec.kind === 'bars') {
        const points = barsPoints(laneData, spec.key)
        const max = maxCount(points) || 1
        const color = BAR_LANE_COLORS[spec.key] ?? COLORS.action

        ctx.fillStyle = COLORS.muted
        ctx.font = '10px Inter, system-ui, sans-serif'
        ctx.textBaseline = 'top'
        ctx.fillText(`0–${maxCount(points).toLocaleString()}`, 4, top + 4)
        ctx.fillText(spec.label, 4, top + 18)

        const barMaxH = LANE_H - 12
        for (const pt of points) {
          const t = Date.parse(pt.bucket)
          if (!Number.isFinite(t)) continue
          const x = LANE_LABEL_W + xForTime(t, viewport, plotW)
          const barH = (pt.count / max) * barMaxH
          const barY = top + LANE_H - 4 - barH
          ctx.fillStyle = color
          ctx.globalAlpha = 0.85
          ctx.fillRect(x, barY, bw, barH)
          ctx.globalAlpha = 1
          if (
            selectedBucket &&
            selectedBucket.lane === spec.key &&
            selectedBucket.bucketIso === pt.bucket
          ) {
            ctx.strokeStyle = COLORS.action
            ctx.lineWidth = 1
            ctx.strokeRect(x + 0.5, barY + 0.5, Math.max(0, bw - 1), Math.max(0, barH - 1))
          }
        }
      } else {
        // multirow: top_people activity spans
        const data = laneData[spec.key]
        const contacts = isTopPeopleLane(data) ? data.contacts : []
        ctx.fillStyle = COLORS.muted
        ctx.font = '10px Inter, system-ui, sans-serif'
        ctx.textBaseline = 'middle'
        ctx.fillText(spec.label, 4, top + MULTIROW_HEADER_H / 2)

        contacts.forEach((contact, i) => {
          const rowTop = top + MULTIROW_HEADER_H + i * MULTIROW_ROW_H
          const rowMax = maxCount(contact.buckets) || 1
          ctx.fillStyle = COLORS.muted
          ctx.font = '10px Inter, system-ui, sans-serif'
          ctx.textBaseline = 'middle'
          ctx.fillText(
            truncateLabel(contact.display_name, 16),
            4,
            rowTop + MULTIROW_ROW_H / 2,
          )

          for (const pt of contact.buckets) {
            if (pt.count <= 0) continue
            const t = Date.parse(pt.bucket)
            if (!Number.isFinite(t)) continue
            const x = LANE_LABEL_W + xForTime(t, viewport, plotW)
            const opacity = Math.min(1, Math.max(0.15, pt.count / rowMax))
            ctx.fillStyle = PEOPLE_CYAN
            ctx.globalAlpha = opacity
            const markH = MULTIROW_ROW_H - 4
            ctx.fillRect(x, rowTop + 2, bw, markH)
            ctx.globalAlpha = 1
            const hitLane = `top_people:${contact.contact_id}`
            if (
              selectedBucket &&
              selectedBucket.lane === hitLane &&
              selectedBucket.bucketIso === pt.bucket
            ) {
              ctx.strokeStyle = COLORS.action
              ctx.lineWidth = 1
              ctx.strokeRect(x + 0.5, rowTop + 2.5, Math.max(0, bw - 1), markH - 1)
            }
          }
        })
      }
    }

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
    let total = 0
    for (const spec of lanes) {
      if (spec.kind === 'bars') total += sumCounts(barsPoints(laneData, spec.key))
      else {
        const tp = laneData[spec.key]
        if (isTopPeopleLane(tp)) {
          for (const c of tp.contacts) total += sumCounts(c.buckets)
        }
      }
    }
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
    brush,
    cssSize.h,
    cssSize.w,
    extent,
    isFetching,
    laneData,
    lanes,
    layout,
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

    // Bar / multirow mark click hit-test
    const pending = clickRef.current
    clickRef.current = null
    if (!pending || !onSelectBucket) return
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    const localX = e.clientX - rect.left
    const localY = e.clientY - rect.top
    // Ignore if pointer moved more than a few px (drag-like)
    if (Math.hypot(localX - pending.x, localY - pending.y) > 6) return
    const hitKey = laneAtYFromLayout(localY, layout)
    if (!hitKey) return
    const pw = plotWidth()
    const plotX = localX - LANE_LABEL_W
    if (plotX < 0) return
    const unitTyped = parseUnit(unit)

    let points: BucketPoint[] = []
    if (hitKey.startsWith('top_people:')) {
      const contactId = hitKey.slice('top_people:'.length)
      const tp = laneData.top_people
      if (isTopPeopleLane(tp)) {
        const contact = tp.contacts.find((c) => c.contact_id === contactId)
        points = contact?.buckets ?? []
      }
    } else {
      points = barsPoints(laneData, hitKey)
    }
    const bucketIso = bucketAtX(plotX, viewport, pw, unitTyped, points)
    if (bucketIso) onSelectBucket(bucketIso, hitKey)
  }

  const onDoubleClick = (e: MouseEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect) return
    const localX = e.clientX - rect.left
    const localY = e.clientY - rect.top
    const plotX = Math.max(0, localX - LANE_LABEL_W)
    const pw = plotWidth()

    // Resolve bucket hit for non-alt double-click → focus.
    let bucketHit: Viewport | null = null
    if (!e.altKey && onFocusBucket) {
      const hitKey = laneAtYFromLayout(localY, layout)
      if (hitKey) {
        const unitTyped = parseUnit(unit)
        let points: BucketPoint[] = []
        if (hitKey.startsWith('top_people:')) {
          const contactId = hitKey.slice('top_people:'.length)
          const tp = laneData.top_people
          if (isTopPeopleLane(tp)) {
            const contact = tp.contacts.find((c) => c.contact_id === contactId)
            points = contact?.buckets ?? []
          }
        } else {
          points = barsPoints(laneData, hitKey)
        }
        const bucketIso = bucketAtX(plotX, viewport, pw, unitTyped, points)
        if (bucketIso) {
          const fromMs = Date.parse(bucketIso)
          if (Number.isFinite(fromMs)) {
            bucketHit = { fromMs, toMs: fromMs + UNIT_MS[unitTyped] }
          }
        }
      }
    }

    const action = resolveDoubleClickAction({
      altKey: e.altKey,
      bucketHit,
      viewport,
      plotX,
      plotW: pw,
    })
    if (action.kind === 'focus' && onFocusBucket) {
      onFocusBucket(action.period)
    } else if (action.kind === 'zoom') {
      onViewportChange(action.viewport)
    }
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
      data-lane-count={lanes.length}
      data-canvas-h={cssSize.h}
    >
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={buildAriaLabel(viewport, lanes, laneData)}
        data-testid="timeline-canvas"
      />
    </div>
  )
}
