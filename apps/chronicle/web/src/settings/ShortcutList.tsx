/**
 * Inline keyboard shortcut reference — same data source as the `?` overlay
 * (live ShortcutRegistry), rendered without the modal chrome.
 */
import { useShortcutRegistry, type ShortcutBinding } from '../keyboard'

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

export function ShortcutList() {
  const registry = useShortcutRegistry()
  const bindings = registry?.list() ?? []
  const groups = new Map<string, ShortcutBinding[]>()
  for (const b of bindings) {
    const list = groups.get(b.group) ?? []
    list.push(b)
    groups.set(b.group, list)
  }

  if (bindings.length === 0) {
    return (
      <p className="text-text-muted" data-testid="shortcut-list-empty">
        No shortcuts registered in this context.
      </p>
    )
  }

  return (
    <table
      className="w-full border-collapse text-left text-[12px]"
      data-testid="settings-shortcut-list"
    >
      <caption className="sr-only">Currently registered keyboard shortcuts</caption>
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
          <th scope="row" className="py-1 pr-3 font-mono font-normal text-action">
            {formatChordDisplay(b)}
          </th>
          <td className="py-1 text-text-primary">{b.description}</td>
        </tr>
      ))}
    </>
  )
}
