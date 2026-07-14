/** Compact monthly activity bar row (simple SVG). */

export interface ActivityBarsProps {
  buckets: { bucket: string; count: number }[]
  height?: number
  className?: string
}

export function ActivityBars({
  buckets,
  height = 32,
  className = '',
}: ActivityBarsProps) {
  if (!buckets.length) {
    return (
      <p className="text-[11px] text-text-muted" data-testid="activity-empty">
        No sent-message activity
      </p>
    )
  }
  const max = Math.max(...buckets.map((b) => b.count), 1)
  const barW = Math.max(2, Math.min(8, Math.floor(280 / buckets.length)))
  const gap = 1
  const width = buckets.length * (barW + gap)

  return (
    <svg
      width={width}
      height={height}
      className={className}
      role="img"
      aria-label="Monthly sent-message volume"
      data-testid="activity-bars"
    >
      {buckets.map((b, i) => {
        const h = Math.max(1, (b.count / max) * (height - 2))
        return (
          <rect
            key={b.bucket}
            x={i * (barW + gap)}
            y={height - h}
            width={barW}
            height={h}
            className="fill-action/70"
          >
            <title>
              {b.bucket.slice(0, 7)}: {b.count}
            </title>
          </rect>
        )
      })}
    </svg>
  )
}
