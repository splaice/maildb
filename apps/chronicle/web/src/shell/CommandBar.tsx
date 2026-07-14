import { useQueryClient } from '@tanstack/react-query'
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useNavigate } from 'react-router'

import { apiGet, apiPost } from '../api/client'
import type { PeopleListResponse, TopicListResponse } from '../api/types'
import { sessionQueryKey, useSession } from '../auth/useSession'
import {
  FOCUS_COMMAND_BAR_EVENT,
  parseDatePhrase,
} from '../keyboard'
import { archiveSummaryQueryKey } from '../routes/useArchiveSummary'
import { useWorkingSetStore } from '../workingset/store'

/** §5.3 structured operators for autocomplete. */
export const SEARCH_OPERATORS = [
  'from:',
  'to:',
  'cc:',
  'participant:',
  'subject:',
  'after:',
  'before:',
  'on:',
  'mailbox:',
  'domain:',
  'topic:',
  'person:',
  'organization:',
  'filetype:',
  'filename:',
  'has:attachment',
  'has:failed-extraction',
  'is:thread',
  'is:attachment',
  'is:message',
] as const

export type CommandBarMode = 'search' | 'ask' | 'explore'

type SuggestItem =
  | { kind: 'person'; id: string; label: string; address?: string }
  | { kind: 'topic'; id: string; label: string }
  | { kind: 'operator'; label: string }

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

export function CommandBar() {
  const { data: session } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const setQuery = useWorkingSetStore((s) => s.setQuery)
  const setViewport = useWorkingSetStore((s) => s.setViewport)
  const addSender = useWorkingSetStore((s) => s.addSender)
  const setSelection = useWorkingSetStore((s) => s.setSelection)
  const patchScope = useWorkingSetStore((s) => s.patchScope)
  const clearScope = useWorkingSetStore((s) => s.clearScope)

  // Palette "Reset scope" static action.
  useEffect(() => {
    const onReset = () => clearScope()
    window.addEventListener('chronicle:reset-scope', onReset)
    return () => window.removeEventListener('chronicle:reset-scope', onReset)
  }, [clearScope])

  const [mode, setMode] = useState<CommandBarMode>('search')
  const [value, setValue] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const [people, setPeople] = useState<SuggestItem[]>([])
  const [topics, setTopics] = useState<SuggestItem[]>([])

  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const listboxId = useId()

  async function logout() {
    try {
      await apiPost<{ status: string }>('/api/auth/logout')
    } catch {
      // Still clear client session even if the network call fails.
    }
    queryClient.removeQueries({ queryKey: sessionQueryKey })
    queryClient.removeQueries({ queryKey: archiveSummaryQueryKey })
    navigate('/login', { replace: true })
  }

  // `/` focuses the bar from anywhere (via ShortcutProvider).
  useEffect(() => {
    const onFocus = () => {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
    window.addEventListener(FOCUS_COMMAND_BAR_EVENT, onFocus)
    return () => window.removeEventListener(FOCUS_COMMAND_BAR_EVENT, onFocus)
  }, [])

  // Autocomplete: debounced 200ms, min 2 chars.
  useEffect(() => {
    const q = value.trim()
    if (q.length < 2) {
      setPeople([])
      setTopics([])
      setOpen(false)
      return
    }
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    const t = window.setTimeout(() => {
      void (async () => {
        try {
          const [peopleRes, topicsRes] = await Promise.all([
            apiGet<PeopleListResponse>(
              `/api/people?q=${encodeURIComponent(q)}&limit=5`,
              ac.signal,
            ).catch(() => null),
            apiGet<TopicListResponse>(
              '/api/topics?include_hidden=true',
              ac.signal,
            ).catch(() => null),
          ])
          if (ac.signal.aborted) return
          const nextPeople: SuggestItem[] = peopleRes
            ? peopleRes.items.slice(0, 5).map((p) => ({
                kind: 'person' as const,
                id: p.id,
                label: p.display_name || p.addresses[0] || p.id,
                address: p.addresses[0],
              }))
            : []
          let nextTopics: SuggestItem[] = []
          if (topicsRes) {
            const flat = flattenTopics(topicsRes.topics)
            const ql = q.toLowerCase()
            nextTopics = flat
              .filter((t) => t.label.toLowerCase().includes(ql))
              .slice(0, 5)
              .map((t) => ({
                kind: 'topic' as const,
                id: t.id,
                label: t.label,
              }))
          }
          setPeople(nextPeople)
          setTopics(nextTopics)
          setOpen(true)
        } catch {
          /* abort */
        }
      })()
    }, 200)
    return () => {
      window.clearTimeout(t)
      ac.abort()
    }
  }, [value])

  const operators = useMemo(() => {
    const q = value.trim().toLowerCase()
    if (q.length < 2) return [] as SuggestItem[]
    return SEARCH_OPERATORS.filter((op) => op.toLowerCase().startsWith(q) || op.toLowerCase().includes(q))
      .slice(0, 8)
      .map((label) => ({ kind: 'operator' as const, label }))
  }, [value])

  const suggestions: SuggestItem[] = useMemo(
    () => [...people, ...topics, ...operators],
    [people, topics, operators],
  )

  useEffect(() => {
    setActiveIndex(0)
  }, [suggestions.length, value])

  const closeSuggest = useCallback(() => {
    setOpen(false)
    setActiveIndex(0)
  }, [])

  const applyPersonOrTopic = useCallback(
    (item: SuggestItem) => {
      if (item.kind === 'person') {
        if (item.address) addSender(item.address)
        else addSender(item.label)
        closeSuggest()
        setValue('')
        return
      }
      if (item.kind === 'topic') {
        const cur = useWorkingSetStore.getState().scope.free_text ?? ''
        const token = `topic:${item.label.replace(/\s+/g, '-').toLowerCase()}`
        const next = cur.includes(token) ? cur : [cur, token].filter(Boolean).join(' ')
        patchScope({ free_text: next })
        setSelection({ kind: 'topic', topicId: item.id })
        closeSuggest()
        setValue('')
        return
      }
      if (item.kind === 'operator') {
        // Insert operator into input (replace current token or append).
        setValue(item.label)
        closeSuggest()
        inputRef.current?.focus()
      }
    },
    [addSender, patchScope, setSelection, closeSuggest],
  )

  const execute = useCallback(() => {
    const q = value.trim()
    if (!q) return
    closeSuggest()

    if (mode === 'search') {
      setQuery(q)
      navigate(`/research?q=${encodeURIComponent(q)}`)
      setValue('')
      return
    }

    if (mode === 'ask') {
      setQuery(q)
      navigate(`/research?q=${encodeURIComponent(q)}&desk=ask`)
      setValue('')
      return
    }

    // Explore: date phrases → Chronicle viewport; else fall back to Search.
    const parsed = parseDatePhrase(q)
    if (parsed) {
      setViewport({ fromMs: parsed.fromMs, toMs: parsed.toMs })
      navigate('/')
      setValue('')
      return
    }
    setQuery(q)
    navigate(`/research?q=${encodeURIComponent(q)}`)
    setValue('')
  }, [value, mode, closeSuggest, setQuery, navigate, setViewport])

  const showList = open && suggestions.length > 0
  const activeDesc =
    showList && suggestions[activeIndex]
      ? `${listboxId}-opt-${activeIndex}`
      : undefined

  return (
    <header
      className="col-span-3 flex items-center gap-3 border-b border-steel bg-graphite-900 px-3"
      style={{ height: 56 }}
    >
      <div className="shrink-0 font-medium text-text-primary">Life Chronicle</div>

      <div className="relative flex min-w-0 flex-1 items-center gap-2">
        <div
          role="radiogroup"
          aria-label="Command bar mode"
          className="flex shrink-0 gap-0.5"
          data-testid="command-bar-mode"
        >
          {([
            ['search', 'Search'],
            ['ask', 'Ask'],
            ['explore', 'Explore'],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={mode === id}
              data-testid={`command-bar-mode-${id}`}
              className={[
                'rounded-md border px-2 py-1 text-[12px]',
                mode === id
                  ? 'border-action bg-graphite-800 text-action'
                  : 'border-steel bg-graphite-900 text-text-muted hover:text-text-primary',
              ].join(' ')}
              onClick={() => setMode(id)}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="relative min-w-0 flex-1">
          <input
            ref={inputRef}
            type="search"
            value={value}
            onChange={(e) => {
              setValue(e.target.value)
              if (e.target.value.trim().length >= 2) setOpen(true)
            }}
            onFocus={() => {
              if (suggestions.length > 0) setOpen(true)
            }}
            onBlur={() => {
              // Delay so option mousedown can fire.
              window.setTimeout(() => closeSuggest(), 150)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault()
                closeSuggest()
                return
              }
              if (showList && e.key === 'ArrowDown') {
                e.preventDefault()
                setActiveIndex((i) => Math.min(suggestions.length - 1, i + 1))
                return
              }
              if (showList && e.key === 'ArrowUp') {
                e.preventDefault()
                setActiveIndex((i) => Math.max(0, i - 1))
                return
              }
              if (e.key === 'Enter') {
                e.preventDefault()
                if (showList && suggestions[activeIndex]) {
                  applyPersonOrTopic(suggestions[activeIndex]!)
                } else {
                  execute()
                }
              }
            }}
            placeholder={
              mode === 'ask'
                ? 'Ask a question — /'
                : mode === 'explore'
                  ? 'Explore a date, person, or topic — /'
                  : 'Search, Ask, or Explore — /'
            }
            aria-label="Universal search"
            aria-autocomplete="list"
            aria-controls={showList ? listboxId : undefined}
            aria-expanded={showList}
            aria-activedescendant={activeDesc}
            role="combobox"
            data-testid="command-bar-input"
            className="w-full rounded-md border border-steel bg-graphite-800 px-3 py-1.5 text-text-primary placeholder:text-text-muted focus:outline focus:outline-2 focus:outline-offset-0 focus:outline-action"
          />

          {showList ? (
            <ul
              id={listboxId}
              role="listbox"
              aria-label="Suggestions"
              data-testid="command-bar-suggest"
              className="absolute left-0 right-0 top-full z-40 mt-1 max-h-64 overflow-auto rounded-md border border-steel bg-graphite-900 py-1 shadow-lg"
            >
              {suggestions.map((item, i) => {
                const label =
                  item.kind === 'operator' ? item.label : item.label
                const section =
                  item.kind === 'person'
                    ? 'People'
                    : item.kind === 'topic'
                      ? 'Topics'
                      : 'Operators'
                const prev = i > 0 ? suggestions[i - 1] : null
                const prevSection = prev
                  ? prev.kind === 'person'
                    ? 'People'
                    : prev.kind === 'topic'
                      ? 'Topics'
                      : 'Operators'
                  : null
                return (
                  <li key={`${item.kind}-${label}-${i}`} role="presentation">
                    {section !== prevSection ? (
                      <div className="px-3 pt-1.5 pb-0.5 text-[10px] font-medium uppercase text-text-muted">
                        {section}
                      </div>
                    ) : null}
                    <div
                      id={`${listboxId}-opt-${i}`}
                      role="option"
                      aria-selected={i === activeIndex}
                      data-testid={`command-bar-option-${i}`}
                      className={[
                        'cursor-pointer px-3 py-1 text-[13px]',
                        i === activeIndex
                          ? 'bg-graphite-800 text-action'
                          : 'text-text-primary',
                      ].join(' ')}
                      onMouseEnter={() => setActiveIndex(i)}
                      onMouseDown={(e) => {
                        e.preventDefault()
                        applyPersonOrTopic(item)
                      }}
                    >
                      {label}
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : null}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <span className="text-text-muted">{session?.username ?? ''}</span>
        <button
          type="button"
          onClick={() => void logout()}
          className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary"
        >
          Logout
        </button>
      </div>
    </header>
  )
}
