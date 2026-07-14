import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { apiGet } from '../api/client'
import type {
  PeopleListResponse,
  TopicListResponse,
  WorkspaceListResponse,
} from '../api/types'
import {
  OPEN_PALETTE_EVENT,
  parseDatePhrase,
} from '../keyboard'
import { useWorkingSetStore } from '../workingset/store'
import {
  type Command,
  type CommandContext,
  filterCommands,
} from './commandRegistry'
import { useCommandRegistry } from './CommandContext'
import { loadRecents, pushRecent, type RecentEntry } from './recents'

type PaletteItem =
  | { kind: 'command'; command: Command }
  | { kind: 'person'; id: string; label: string; address?: string }
  | { kind: 'topic'; id: string; label: string }
  | { kind: 'workspace'; id: string; label: string }
  | { kind: 'date'; label: string; fromMs: number; toMs: number }
  | { kind: 'recent'; entry: RecentEntry }

function flattenTopics(
  nodes: TopicListResponse['topics'],
  acc: { id: string; label: string }[] = [],
): { id: string; label: string }[] {
  for (const n of nodes) {
    acc.push({ id: n.id, label: n.label })
    if (n.children?.length) flattenTopics(n.children, acc)
  }
  return acc
}

export function CommandPalette() {
  const registry = useCommandRegistry()
  const navigate = useNavigate()
  const setViewport = useWorkingSetStore((s) => s.setViewport)
  const addSender = useWorkingSetStore((s) => s.addSender)
  const setSelection = useWorkingSetStore((s) => s.setSelection)
  const patchScope = useWorkingSetStore((s) => s.patchScope)

  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [people, setPeople] = useState<PaletteItem[]>([])
  const [topics, setTopics] = useState<PaletteItem[]>([])
  const [workspaces, setWorkspaces] = useState<PaletteItem[]>([])
  const [recents, setRecents] = useState<RecentEntry[]>(() => loadRecents())

  const inputRef = useRef<HTMLInputElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const ctx: CommandContext = useMemo(
    () => ({
      navigate,
      getState: () => ({
        brush: useWorkingSetStore.getState().brush,
        selection: useWorkingSetStore.getState().selection,
        compare: useWorkingSetStore.getState().compare,
        view: useWorkingSetStore.getState().view,
      }),
    }),
    [navigate],
  )

  const close = useCallback(() => {
    setOpen(false)
    setQuery('')
    setActiveIndex(0)
    setPeople([])
    setTopics([])
    setWorkspaces([])
    // Restore focus after paint.
    requestAnimationFrame(() => {
      previousFocus.current?.focus?.()
    })
  }, [])

  const openPalette = useCallback(() => {
    previousFocus.current = document.activeElement as HTMLElement | null
    setRecents(loadRecents())
    setOpen(true)
    setQuery('')
    setActiveIndex(0)
  }, [])

  useEffect(() => {
    const onOpen = () => openPalette()
    window.addEventListener(OPEN_PALETTE_EVENT, onOpen)
    return () => window.removeEventListener(OPEN_PALETTE_EVENT, onOpen)
  }, [openPalette])

  // Focus trap + initial focus.
  useEffect(() => {
    if (!open) return
    inputRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        close()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open, close])

  // Fetch remote sections when query has ≥ 2 chars (debounced 200ms).
  useEffect(() => {
    if (!open) return
    const q = query.trim()
    if (q.length < 2) {
      setPeople([])
      setTopics([])
      setWorkspaces([])
      return
    }
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    const t = window.setTimeout(() => {
      void (async () => {
        try {
          const [peopleRes, topicsRes, wsRes] = await Promise.all([
            apiGet<PeopleListResponse>(
              `/api/people?q=${encodeURIComponent(q)}&limit=5`,
              ac.signal,
            ).catch(() => null),
            apiGet<TopicListResponse>('/api/topics?include_hidden=true', ac.signal).catch(
              () => null,
            ),
            apiGet<WorkspaceListResponse>('/api/workspaces', ac.signal).catch(() => null),
          ])
          if (ac.signal.aborted) return
          if (peopleRes) {
            setPeople(
              peopleRes.items.slice(0, 5).map((p) => ({
                kind: 'person' as const,
                id: p.id,
                label: p.display_name || p.addresses[0] || p.id,
                address: p.addresses[0],
              })),
            )
          }
          if (topicsRes) {
            const flat = flattenTopics(topicsRes.topics)
            const ql = q.toLowerCase()
            setTopics(
              flat
                .filter((t) => t.label.toLowerCase().includes(ql))
                .slice(0, 5)
                .map((t) => ({
                  kind: 'topic' as const,
                  id: t.id,
                  label: t.label,
                })),
            )
          }
          if (wsRes) {
            const ql = q.toLowerCase()
            setWorkspaces(
              wsRes.items
                .filter((w) => w.name.toLowerCase().includes(ql))
                .slice(0, 5)
                .map((w) => ({
                  kind: 'workspace' as const,
                  id: w.id,
                  label: w.name,
                })),
            )
          }
        } catch {
          /* aborted or network */
        }
      })()
    }, 200)
    return () => {
      window.clearTimeout(t)
      ac.abort()
    }
  }, [query, open])

  // Re-list when palette opens so late-registered page commands appear.
  const registered = useMemo(
    () => (registry ? registry.list(ctx) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- open forces refresh
    [registry, ctx, open],
  )
  const commandHits = useMemo(
    () => filterCommands(registered, query),
    [registered, query],
  )

  const dateHit = useMemo(() => {
    const parsed = parseDatePhrase(query)
    if (!parsed) return null
    return {
      kind: 'date' as const,
      label: `Jump to ${parsed.label}`,
      fromMs: parsed.fromMs,
      toMs: parsed.toMs,
    }
  }, [query])

  const items: PaletteItem[] = useMemo(() => {
    if (!query.trim()) {
      return recents.map((entry) => ({ kind: 'recent' as const, entry }))
    }
    const out: PaletteItem[] = []
    for (const c of commandHits) {
      out.push({ kind: 'command', command: c })
    }
    if (dateHit) out.push(dateHit)
    out.push(...people, ...topics, ...workspaces)
    return out
  }, [query, recents, commandHits, dateHit, people, topics, workspaces])

  useEffect(() => {
    setActiveIndex(0)
  }, [query, items.length])

  const executeItem = useCallback(
    (item: PaletteItem) => {
      if (!registry) return
      if (item.kind === 'command') {
        registry.execute(item.command.id, ctx)
        setRecents(pushRecent({ id: item.command.id, title: item.command.title }))
      } else if (item.kind === 'recent') {
        const cmd = registry.get(item.entry.id)
        if (cmd) {
          registry.execute(cmd.id, ctx)
          setRecents(pushRecent({ id: cmd.id, title: cmd.title }))
        }
      } else if (item.kind === 'person') {
        // Scope-first: add analytical person filter; stay on current lens.
        if (item.address) addSender(item.address)
        else addSender(item.label)
        setRecents(
          pushRecent({ id: `person:${item.id}`, title: `Person: ${item.label}` }),
        )
      } else if (item.kind === 'topic') {
        // Topic filter via free_text structured operator (no topic field on QueryScope).
        const cur = useWorkingSetStore.getState().scope.free_text ?? ''
        const token = `topic:${item.label.replace(/\s+/g, '-').toLowerCase()}`
        const next = cur.includes(token) ? cur : [cur, token].filter(Boolean).join(' ')
        patchScope({ free_text: next })
        setSelection({ kind: 'topic', topicId: item.id })
        setRecents(
          pushRecent({ id: `topic:${item.id}`, title: `Topic: ${item.label}` }),
        )
      } else if (item.kind === 'workspace') {
        navigate(`/workspaces/${encodeURIComponent(item.id)}`)
        setRecents(
          pushRecent({
            id: `workspace:${item.id}`,
            title: `Workspace: ${item.label}`,
          }),
        )
      } else if (item.kind === 'date') {
        setViewport({ fromMs: item.fromMs, toMs: item.toMs })
        navigate('/')
        setRecents(pushRecent({ id: `date:${item.label}`, title: item.label }))
      }
      close()
    },
    [
      registry,
      ctx,
      addSender,
      setSelection,
      patchScope,
      navigate,
      setViewport,
      close,
    ],
  )

  if (!registry || !open) return null

  const activeId =
    items[activeIndex] != null ? `palette-opt-${activeIndex}` : undefined

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-black/50 pt-[12vh]"
      role="presentation"
      data-testid="command-palette-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close()
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        data-testid="command-palette"
        className="w-full max-w-xl overflow-hidden rounded-lg border border-steel bg-graphite-900 shadow-xl"
      >
        <div className="border-b border-steel p-2">
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Go to, search people, topics, dates…"
            aria-label="Command palette query"
            aria-controls="palette-listbox"
            aria-activedescendant={activeId}
            className="w-full rounded-md border border-steel bg-graphite-800 px-3 py-2 text-text-primary placeholder:text-text-muted focus:outline focus:outline-2 focus:outline-action"
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setActiveIndex((i) => Math.min(items.length - 1, i + 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setActiveIndex((i) => Math.max(0, i - 1))
              } else if (e.key === 'Enter') {
                e.preventDefault()
                const item = items[activeIndex]
                if (item) executeItem(item)
              }
            }}
          />
        </div>
        <ul
          id="palette-listbox"
          role="listbox"
          aria-label="Commands"
          className="max-h-80 overflow-auto py-1"
          data-testid="palette-listbox"
        >
          {items.length === 0 ? (
            <li className="px-3 py-2 text-[12px] text-text-muted">
              {query.trim() ? 'No matches' : 'Type to search commands…'}
            </li>
          ) : (
            items.map((item, i) => {
              const label = itemLabel(item)
              const group = itemGroup(item)
              const prevGroup = i > 0 ? itemGroup(items[i - 1]!) : null
              return (
                <li key={itemKey(item, i)} role="presentation">
                  {group !== prevGroup ? (
                    <div
                      className="px-3 pt-2 pb-0.5 text-[10px] font-medium uppercase tracking-wide text-text-muted"
                      data-testid={`palette-group-${group}`}
                    >
                      {group}
                    </div>
                  ) : null}
                  <div
                    id={`palette-opt-${i}`}
                    role="option"
                    aria-selected={i === activeIndex}
                    data-testid={`palette-option-${i}`}
                    className={[
                      'cursor-pointer px-3 py-1.5 text-[13px]',
                      i === activeIndex
                        ? 'bg-graphite-800 text-action'
                        : 'text-text-primary hover:bg-graphite-800',
                    ].join(' ')}
                    onMouseEnter={() => setActiveIndex(i)}
                    onMouseDown={(e) => {
                      e.preventDefault()
                      executeItem(item)
                    }}
                  >
                    {label}
                  </div>
                </li>
              )
            })
          )}
        </ul>
      </div>
    </div>
  )
}

function itemLabel(item: PaletteItem): string {
  switch (item.kind) {
    case 'command':
      return item.command.title
    case 'recent':
      return item.entry.title
    case 'person':
      return item.label
    case 'topic':
      return item.label
    case 'workspace':
      return item.label
    case 'date':
      return item.label
  }
}

function itemGroup(item: PaletteItem): string {
  switch (item.kind) {
    case 'command':
      return item.command.group
    case 'recent':
      return 'Recent'
    case 'person':
      return 'People'
    case 'topic':
      return 'Topics'
    case 'workspace':
      return 'Workspaces'
    case 'date':
      return 'Dates'
  }
}

function itemKey(item: PaletteItem, i: number): string {
  switch (item.kind) {
    case 'command':
      return `cmd:${item.command.id}`
    case 'recent':
      return `recent:${item.entry.id}`
    case 'person':
      return `person:${item.id}`
    case 'topic':
      return `topic:${item.id}`
    case 'workspace':
      return `ws:${item.id}`
    case 'date':
      return `date:${item.label}:${i}`
  }
}
