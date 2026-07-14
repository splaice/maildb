import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { isEditableTarget } from './isEditableTarget'
import {
  type ShortcutBinding,
  type ShortcutChord,
  ShortcutRegistry,
  eventToChordKey,
} from './shortcutRegistry'
import {
  getStatusHint,
  subscribeStatusHint,
} from './statusHint'

const ShortcutRegistryContext = createContext<ShortcutRegistry | null>(null)

/** Custom event: `/` and command bar focus. */
export const FOCUS_COMMAND_BAR_EVENT = 'chronicle:focus-command-bar'
export const OPEN_PALETTE_EVENT = 'chronicle:open-palette'
export const OPEN_SHORTCUT_REF_EVENT = 'chronicle:open-shortcut-ref'
export const CLOSE_SHORTCUT_REF_EVENT = 'chronicle:close-shortcut-ref'

export function ShortcutProvider({ children }: { children: ReactNode }) {
  const registry = useMemo(() => new ShortcutRegistry(), [])
  const [hint, setHint] = useState<string | null>(() => getStatusHint())
  const [refOpen, setRefOpen] = useState(false)
  const registryRef = useRef(registry)
  registryRef.current = registry

  useEffect(() => subscribeStatusHint(setHint), [])

  // Global dispatcher for registered shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      // `?` and reference overlay escape handled even... no, still guard inputs
      // except we allow Esc always for overlays via separate handlers.
      if (isEditableTarget(e.target)) return

      // Built-in: open shortcut reference
      if (e.key === '?' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault()
        setRefOpen(true)
        window.dispatchEvent(new CustomEvent(OPEN_SHORTCUT_REF_EVENT))
        return
      }

      // Built-in: focus command bar
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent(FOCUS_COMMAND_BAR_EVENT))
        return
      }

      // Built-in: open palette
      if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey) && !e.altKey) {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT))
        return
      }

      const binding = registryRef.current.match(e)
      if (!binding) return
      const handled = binding.run(e)
      if (handled === false) return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Register global navigation shortcuts (always present).
  useEffect(() => {
    const unsubs = [
      registry.register({
        id: 'global.focus-query',
        chord: { key: '/' },
        description: 'Focus universal query',
        group: 'Global',
        run: () => {
          window.dispatchEvent(new CustomEvent(FOCUS_COMMAND_BAR_EVENT))
        },
      }),
      registry.register({
        id: 'global.palette',
        chord: { key: 'k', mod: true },
        description: 'Open command palette',
        group: 'Global',
        run: () => {
          window.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT))
        },
      }),
      registry.register({
        id: 'global.shortcut-ref',
        chord: { key: '?' },
        description: 'Open contextual shortcut reference',
        group: 'Global',
        run: () => setRefOpen(true),
      }),
      registry.register({
        id: 'global.nav-chronicle',
        chord: { key: 't' },
        description: 'Open or return to Chronicle',
        group: 'Navigation',
        run: () => {
          // Dispatched via navigate helper registered by App shell binder.
          window.dispatchEvent(
            new CustomEvent('chronicle:navigate', { detail: { to: '/' } }),
          )
        },
      }),
      registry.register({
        id: 'global.nav-research',
        chord: { key: 'r' },
        description: 'Open Research Desk with current scope',
        group: 'Navigation',
        run: () => {
          window.dispatchEvent(
            new CustomEvent('chronicle:navigate', {
              detail: { to: '/research' },
            }),
          )
        },
      }),
      registry.register({
        id: 'global.nav-topics-map',
        chord: { key: 'm' },
        description: 'Open Topic Atlas projection view',
        group: 'Navigation',
        run: () => {
          window.dispatchEvent(
            new CustomEvent('chronicle:navigate', {
              detail: { to: '/topics?tv=projection' },
            }),
          )
        },
      }),
    ]
    return () => {
      for (const u of unsubs) u()
    }
  }, [registry])

  return (
    <ShortcutRegistryContext.Provider value={registry}>
      {children}
      {hint ? (
        <div
          role="status"
          aria-live="polite"
          data-testid="status-hint"
          className="pointer-events-none fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-md border border-steel bg-graphite-800 px-3 py-1 text-[11px] text-text-primary shadow-lg"
        >
          {hint}
        </div>
      ) : null}
      {refOpen ? (
        <ShortcutReferenceOverlay
          registry={registry}
          onClose={() => setRefOpen(false)}
        />
      ) : null}
    </ShortcutRegistryContext.Provider>
  )
}

export function useShortcutRegistry(): ShortcutRegistry | null {
  return useContext(ShortcutRegistryContext)
}

function chordMatchesEvent(e: KeyboardEvent, chord: ShortcutChord): boolean {
  const key =
    e.key.length === 1 ? e.key.toLowerCase() : e.key === ' ' ? ' ' : e.key
  const wantKey = chord.key.length === 1 ? chord.key.toLowerCase() : chord.key
  if (key !== wantKey) return false
  const wantMod = !!chord.mod
  const wantShift = !!chord.shift
  const wantAlt = !!chord.alt
  const hasMod = e.metaKey || e.ctrlKey
  if (wantMod !== hasMod) return false
  if (wantShift !== e.shiftKey) return false
  if (wantAlt !== e.altKey) return false
  return true
}

/**
 * Register page-scoped shortcuts; cleaned up on unmount.
 * When ShortcutProvider is present, bindings join the central registry
 * (for `?` reference + unified dispatch). Without a provider (unit tests),
 * falls back to a local keydown listener with the same input guard.
 */
export function useShortcuts(bindings: ShortcutBinding[]): void {
  const registry = useShortcutRegistry()
  const key = bindings.map((b) => b.id).join('|')
  const bindingsRef = useRef(bindings)
  bindingsRef.current = bindings

  useEffect(() => {
    if (registry) {
      const unsubs = bindingsRef.current.map((b) => registry.register(b))
      return () => {
        for (const u of unsubs) u()
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      if (isEditableTarget(e.target)) return
      for (const b of bindingsRef.current) {
        if (chordMatchesEvent(e, b.chord)) {
          const handled = b.run(e)
          if (handled === false) return
          e.preventDefault()
          return
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key captures identity
  }, [registry, key])
}

function ShortcutReferenceOverlay({
  registry,
  onClose,
}: {
  registry: ShortcutRegistry
  onClose: () => void
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  const [tick, setTick] = useState(0)

  // Refresh list when opened (registry may change under us).
  useEffect(() => {
    setTick((t) => t + 1)
  }, [registry])

  useEffect(() => {
    previousFocus.current = document.activeElement as HTMLElement | null
    dialogRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      previousFocus.current?.focus?.()
    }
  }, [onClose])

  const bindings = registry.list()
  void tick
  const groups = new Map<string, ShortcutBinding[]>()
  for (const b of bindings) {
    const list = groups.get(b.group) ?? []
    list.push(b)
    groups.set(b.group, list)
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 pt-[10vh]"
      role="presentation"
      data-testid="shortcut-reference-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcut-ref-title"
        tabIndex={-1}
        data-testid="shortcut-reference"
        className="max-h-[70vh] w-full max-w-lg overflow-auto rounded-lg border border-steel bg-graphite-900 p-4 shadow-xl focus:outline-none"
      >
        <h2
          id="shortcut-ref-title"
          className="mb-3 text-sm font-medium text-text-primary"
        >
          Keyboard shortcuts
        </h2>
        <table className="w-full border-collapse text-left text-[12px]">
          <caption className="sr-only">
            Currently registered keyboard shortcuts
          </caption>
          <thead>
            <tr className="border-b border-steel text-text-muted">
              <th scope="col" className="py-1 pr-3 font-normal">
                Shortcut
              </th>
              <th scope="col" className="py-1 font-normal">
                Action
              </th>
            </tr>
          </thead>
          <tbody>
            {Array.from(groups.entries()).map(([group, items]) => (
              <GroupRows key={group} group={group} items={items} />
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-[11px] text-text-muted">Press Esc to close</p>
      </div>
    </div>
  )
}

function GroupRows({
  group,
  items,
}: {
  group: string
  items: ShortcutBinding[]
}) {
  return (
    <>
      <tr>
        <th
          scope="colgroup"
          colSpan={2}
          className="pt-3 pb-1 text-[11px] font-medium uppercase tracking-wide text-text-muted"
        >
          {group}
        </th>
      </tr>
      {items.map((b) => (
        <tr key={b.id} className="border-b border-steel/50" data-shortcut-id={b.id}>
          <th
            scope="row"
            className="py-1 pr-3 font-mono font-normal text-action"
          >
            {formatChordDisplay(b)}
          </th>
          <td className="py-1 text-text-primary">{b.description}</td>
        </tr>
      ))}
    </>
  )
}

function formatChordDisplay(b: ShortcutBinding): string {
  const { chord } = b
  const parts: string[] = []
  if (chord.mod) parts.push('Ctrl/Cmd')
  if (chord.shift) parts.push('Shift')
  if (chord.alt) parts.push('Alt')
  const k =
    chord.key === ' '
      ? 'Space'
      : chord.key === 'Escape'
        ? 'Esc'
        : chord.key.length === 1
          ? chord.key.toUpperCase()
          : chord.key
  parts.push(k)
  return parts.join('+')
}

/** App-level navigate binder (uses react-router). */
export function NavigationShortcutBinder() {
  // Imported lazily-style via require of useNavigate in parent — see AppShellBindings.
  return null
}

export function useRegisterNavigationShortcuts(
  navigate: (to: string) => void,
): void {
  useEffect(() => {
    const onNav = (e: Event) => {
      const detail = (e as CustomEvent<{ to: string }>).detail
      if (detail?.to) navigate(detail.to)
    }
    window.addEventListener('chronicle:navigate', onNav)
    return () => window.removeEventListener('chronicle:navigate', onNav)
  }, [navigate])
}

/** Optional: open reference from outside. */
export function useShortcutReferenceOpen(): {
  open: boolean
  setOpen: (v: boolean) => void
} {
  // Exposed via context would be better; keep simple — ref open is internal.
  const [open, setOpen] = useState(false)
  return { open, setOpen }
}

// re-export for tests that assert chord matching via event
export { eventToChordKey }
