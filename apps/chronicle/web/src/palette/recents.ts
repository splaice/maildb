/** Recent palette commands (last 5) in localStorage (+ memory fallback). */

const STORAGE_KEY = 'chronicle.palette.recents.v1'
const MAX = 5

export interface RecentEntry {
  id: string
  title: string
}

/** In-memory mirror so tests and private-mode browsers still work. */
let memory: RecentEntry[] = []

function readStorage(): RecentEntry[] | null {
  try {
    if (typeof localStorage === 'undefined') return null
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return null
    return parsed
      .filter(
        (x): x is RecentEntry =>
          !!x &&
          typeof x === 'object' &&
          typeof (x as RecentEntry).id === 'string' &&
          typeof (x as RecentEntry).title === 'string',
      )
      .slice(0, MAX)
  } catch {
    return null
  }
}

function writeStorage(entries: RecentEntry[]): void {
  memory = entries
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
    }
  } catch {
    /* ignore quota / security */
  }
}

export function loadRecents(): RecentEntry[] {
  const fromStore = readStorage()
  if (fromStore) {
    memory = fromStore
    return fromStore
  }
  return memory.slice(0, MAX)
}

export function pushRecent(entry: RecentEntry): RecentEntry[] {
  const prev = loadRecents().filter((r) => r.id !== entry.id)
  const next = [entry, ...prev].slice(0, MAX)
  writeStorage(next)
  return next
}

export function clearRecents(): void {
  memory = []
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(STORAGE_KEY)
    }
  } catch {
    /* ignore */
  }
}
