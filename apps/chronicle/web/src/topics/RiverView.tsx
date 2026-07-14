import { useMemo, useState } from 'react'

import type { TopicRiverResponse, TopicRiverSeries } from '../api/types'
import { TOPIC_PURPLE } from '../chronicle/laneModel'

export interface RiverViewProps {
  data: TopicRiverResponse | undefined
  loading?: boolean
  selectedId: string | null
  onSelect: (id: string) => void
}

/**
 * Topic river: area-less multirow bands (thin wrapper over multirow series shape).
 * Absolute | normalized toggle with explicit legend (Table 22 river safeguards).
 */
export function RiverView({ data, loading, selectedId, onSelect }: RiverViewProps) {
  const [normalized, setNormalized] = useState(false)
  const [asTable, setAsTable] = useState(false)

  const topics = data?.topics ?? []

  const buckets = useMemo(() => {
    const set = new Set<string>()
    for (const t of topics) {
      for (const b of t.buckets) set.add(b.bucket)
    }
    return Array.from(set).sort()
  }, [topics])

  const series = useMemo(() => {
    return topics.map((t) => {
      const byBucket = new Map(t.buckets.map((b) => [b.bucket, b.count]))
      const counts = buckets.map((b) => byBucket.get(b) ?? 0)
      const total = counts.reduce((a, c) => a + c, 0)
      const values = normalized
        ? counts.map((c) => (total > 0 ? c / total : 0))
        : counts
      const peak = values.reduce((m, v) => Math.max(m, v), 0)
      return { ...t, values, peak, total }
    })
  }, [topics, buckets, normalized])

  if (loading) {
    return (
      <div data-testid="river-view">
        <p className="text-[12px] text-text-muted" data-testid="river-loading">
          Loading river…
        </p>
      </div>
    )
  }

  if (topics.length === 0) {
    return (
      <div data-testid="river-view">
        <p className="text-[12px] text-text-muted" data-testid="river-empty">
          No topic activity in this range
        </p>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="river-view">
      <div className="flex flex-wrap items-center gap-2">
        <div
          className="inline-flex rounded-md border border-steel"
          role="group"
          aria-label="River scale"
          data-testid="river-normalize-toggle"
        >
          <button
            type="button"
            className={`px-2 py-1 text-[11px] ${
              !normalized ? 'bg-graphite-800 text-text-primary' : 'text-text-muted'
            }`}
            aria-pressed={!normalized}
            onClick={() => setNormalized(false)}
          >
            Absolute
          </button>
          <button
            type="button"
            className={`px-2 py-1 text-[11px] ${
              normalized ? 'bg-graphite-800 text-text-primary' : 'text-text-muted'
            }`}
            aria-pressed={normalized}
            onClick={() => setNormalized(true)}
          >
            Normalized
          </button>
        </div>
        <p
          className="text-[11px] text-text-muted"
          data-testid="river-legend"
        >
          {normalized
            ? 'Legend: each band is share of that topic’s activity (0–1 per topic)'
            : 'Legend: absolute message counts per bucket (mode_hint=absolute)'}
          {data?.unit ? ` · unit=${data.unit}` : ''}
        </p>
        <button
          type="button"
          className="ml-auto rounded-md border border-steel bg-graphite-800 px-2 py-1 text-[11px] text-text-primary"
          onClick={() => setAsTable((v) => !v)}
          data-testid="river-view-as-table"
        >
          {asTable ? 'View as bands' : 'View as table'}
        </button>
      </div>

      {asTable ? (
        <RiverTable series={series} buckets={buckets} normalized={normalized} onSelect={onSelect} selectedId={selectedId} />
      ) : (
        <div className="min-h-0 flex-1 space-y-1 overflow-auto" data-testid="river-bands">
          {series.map((t) => (
            <RiverBand
              key={t.topic_id}
              series={t}
              values={t.values}
              peak={t.peak}
              selected={t.topic_id === selectedId}
              onSelect={() => onSelect(t.topic_id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function RiverBand({
  series,
  values,
  peak,
  selected,
  onSelect,
}: {
  series: TopicRiverSeries & { values: number[]; peak: number }
  values: number[]
  peak: number
  selected: boolean
  onSelect: () => void
}) {
  const max = peak > 0 ? peak : 1
  return (
    <button
      type="button"
      className={`flex w-full items-center gap-2 rounded border px-2 py-1 text-left ${
        selected ? 'border-action bg-action/10' : 'border-steel bg-graphite-900'
      }`}
      onClick={onSelect}
      data-testid={`river-band-${series.topic_id}`}
      aria-pressed={selected}
    >
      <span className="w-28 shrink-0 truncate text-[11px] font-medium text-text-primary">
        {series.label}
      </span>
      <div className="flex h-6 min-w-0 flex-1 items-end gap-px" aria-hidden>
        {values.map((v, i) => (
          <div
            key={i}
            className="min-w-[2px] flex-1 rounded-sm"
            style={{
              height: `${Math.max(2, (v / max) * 100)}%`,
              backgroundColor: TOPIC_PURPLE,
              opacity: 0.35 + 0.65 * (v / max),
            }}
          />
        ))}
      </div>
    </button>
  )
}

function RiverTable({
  series,
  buckets,
  normalized,
  onSelect,
  selectedId,
}: {
  series: Array<TopicRiverSeries & { values: number[] }>
  buckets: string[]
  normalized: boolean
  onSelect: (id: string) => void
  selectedId: string | null
}) {
  return (
    <div className="overflow-auto" data-testid="river-table">
      <table className="w-full border-collapse text-left text-[11px]">
        <caption className="sr-only">Topic river as table</caption>
        <thead className="sticky top-0 bg-graphite-900 text-text-muted">
          <tr className="border-b border-steel">
            <th scope="col" className="px-2 py-1 font-medium">
              Topic
            </th>
            {buckets.map((b) => (
              <th key={b} scope="col" className="px-1 py-1 font-medium tabular-nums">
                {b.slice(0, 10)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular-nums font-mono text-text-primary">
          {series.map((t) => (
            <tr
              key={t.topic_id}
              className={`cursor-pointer border-b border-steel/50 hover:bg-graphite-800 ${
                selectedId === t.topic_id ? 'bg-action/10' : ''
              }`}
              onClick={() => onSelect(t.topic_id)}
            >
              <th scope="row" className="px-2 py-1 text-left font-sans font-medium">
                {t.label}
              </th>
              {t.values.map((v, i) => (
                <td key={i} className="px-1 py-1">
                  {normalized ? v.toFixed(2) : v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
