import { useMemo, useState } from 'react'

import type { TopicMatrixResponse } from '../api/types'
import { OriginBadge } from './originBadge'

export interface MatrixViewProps {
  data: TopicMatrixResponse | undefined
  loading?: boolean
  selectedId: string | null
  onSelect: (id: string) => void
}

type SortKey = 'label' | 'row_total' | string

/**
 * Topic × year matrix — accessible table with sort + normalize-per-row.
 * This IS the accessible alternative for itself (Table 22).
 */
export function MatrixView({ data, loading, selectedId, onSelect }: MatrixViewProps) {
  const [normalize, setNormalize] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('row_total')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const columns = data?.columns ?? []
  const rows = useMemo(() => {
    const list = [...(data?.rows ?? [])]
    list.sort((a, b) => {
      let av: string | number
      let bv: string | number
      if (sortKey === 'label') {
        av = a.label
        bv = b.label
      } else if (sortKey === 'row_total') {
        av = a.row_total
        bv = b.row_total
      } else {
        av = a.cells[sortKey] ?? 0
        bv = b.cells[sortKey] ?? 0
      }
      if (typeof av === 'string' && typeof bv === 'string') {
        return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av)
      }
      const an = Number(av)
      const bn = Number(bv)
      return sortDir === 'asc' ? an - bn : bn - an
    })
    return list
  }, [data?.rows, sortKey, sortDir])

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDir(key === 'label' ? 'asc' : 'desc')
    }
  }

  if (loading) {
    return (
      <p className="text-[12px] text-text-muted" data-testid="matrix-loading">
        Loading matrix…
      </p>
    )
  }

  if (!data || rows.length === 0) {
    return (
      <p className="text-[12px] text-text-muted" data-testid="matrix-empty">
        No topic×year data
      </p>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="matrix-view">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-[11px] text-text-muted">
          <input
            type="checkbox"
            checked={normalize}
            onChange={(e) => setNormalize(e.target.checked)}
            data-testid="matrix-normalize-toggle"
          />
          Normalize per row
        </label>
        <span className="text-[11px] text-text-muted" data-testid="matrix-grand-total">
          Total: {data.grand_total.toLocaleString()}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-left text-[11px]">
          <caption className="sr-only">
            Topic by year member counts. Sortable columns. Values are
            {normalize ? ' row-normalized shares' : ' absolute counts'}.
          </caption>
          <thead className="sticky top-0 bg-graphite-900 text-text-muted">
            <tr className="border-b border-steel">
              <th scope="col" className="px-2 py-1.5 font-medium">
                <button
                  type="button"
                  className="hover:text-text-primary"
                  onClick={() => toggleSort('label')}
                  data-testid="matrix-sort-label"
                >
                  Topic{sortKey === 'label' ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                </button>
              </th>
              <th scope="col" className="px-2 py-1.5 font-medium">
                Origin
              </th>
              {columns.map((col) => (
                <th key={col} scope="col" className="px-2 py-1.5 font-medium">
                  <button
                    type="button"
                    className="tabular-nums hover:text-text-primary"
                    onClick={() => toggleSort(col)}
                    data-testid={`matrix-sort-${col}`}
                  >
                    {col}
                    {sortKey === col ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                  </button>
                </th>
              ))}
              <th scope="col" className="px-2 py-1.5 font-medium">
                <button
                  type="button"
                  className="tabular-nums hover:text-text-primary"
                  onClick={() => toggleSort('row_total')}
                  data-testid="matrix-sort-total"
                >
                  Total
                  {sortKey === 'row_total' ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                </button>
              </th>
            </tr>
          </thead>
          <tbody className="tabular-nums font-mono text-text-primary">
            {rows.map((row) => {
              const denom = row.row_total > 0 ? row.row_total : 1
              return (
                <tr
                  key={row.topic_id}
                  className={`cursor-pointer border-b border-steel/50 hover:bg-graphite-800 ${
                    selectedId === row.topic_id ? 'bg-action/10' : ''
                  }`}
                  onClick={() => onSelect(row.topic_id)}
                  data-testid={`matrix-row-${row.topic_id}`}
                >
                  <th
                    scope="row"
                    className="px-2 py-1 text-left font-sans font-medium text-text-primary"
                  >
                    {row.label}
                  </th>
                  <td className="px-2 py-1">
                    <OriginBadge origin={row.origin} />
                  </td>
                  {columns.map((col) => {
                    const raw = row.cells[col] ?? 0
                    const display = normalize ? raw / denom : raw
                    return (
                      <td
                        key={col}
                        className="px-2 py-1"
                        data-testid={`matrix-cell-${row.topic_id}-${col}`}
                        onClick={(e) => {
                          e.stopPropagation()
                          onSelect(row.topic_id)
                        }}
                      >
                        {normalize ? display.toFixed(2) : raw.toLocaleString()}
                      </td>
                    )
                  })}
                  <td className="px-2 py-1 font-medium">
                    {row.row_total.toLocaleString()}
                  </td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr className="border-t border-steel text-text-muted">
              <th scope="row" className="px-2 py-1 text-left font-sans">
                Column total
              </th>
              <td />
              {columns.map((col) => (
                <td key={col} className="px-2 py-1 tabular-nums">
                  {(data.column_totals[col] ?? 0).toLocaleString()}
                </td>
              ))}
              <td className="px-2 py-1 tabular-nums font-medium">
                {data.grand_total.toLocaleString()}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  )
}
