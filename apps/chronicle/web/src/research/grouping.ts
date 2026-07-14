import type { SearchResult } from '../api/types'
import type { ResearchGrouping } from '../workingset/urlState'

export interface ResultGroup {
  key: string
  label: string
  items: SearchResult[]
}

function yearFromDate(iso: string | null | undefined): string {
  if (!iso || iso.length < 4) return 'Unknown year'
  return iso.slice(0, 4)
}

/**
 * Client-side grouping of the loaded result window (RD-007 subset).
 */
export function groupResults(
  results: SearchResult[],
  grouping: ResearchGrouping,
): ResultGroup[] {
  if (grouping === 'none' || results.length === 0) {
    return [{ key: 'all', label: '', items: results }]
  }

  const map = new Map<string, ResultGroup>()

  for (const item of results) {
    let key: string
    let label: string

    if (grouping === 'thread') {
      if (item.result_type === 'message' && item.thread_id) {
        key = item.thread_id
        label = item.subject || '(no subject)'
      } else {
        key = '__no_thread__'
        label = 'No thread'
      }
    } else if (grouping === 'year') {
      key = yearFromDate(item.date)
      label = key
    } else {
      // mailbox
      const mb =
        item.result_type === 'message' ? item.mailbox || 'Unknown mailbox' : 'Unknown mailbox'
      key = mb
      label = mb
    }

    let g = map.get(key)
    if (!g) {
      g = { key, label, items: [] }
      map.set(key, g)
    }
    // Thread group: first subject wins as label
    if (grouping === 'thread' && g.items.length === 0 && item.result_type === 'message') {
      g.label = item.subject || '(no subject)'
    }
    g.items.push(item)
  }

  return Array.from(map.values())
}
