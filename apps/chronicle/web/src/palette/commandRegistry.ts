/**
 * Pure command registry for the Ctrl/Cmd+K palette.
 * Components register context-specific commands via provider effects.
 */

export interface CommandContext {
  navigate: (to: string) => void
  /** Optional bag for page-supplied state (brush, selection, …). */
  getState?: () => Record<string, unknown>
}

export interface Command {
  id: string
  title: string
  group: string
  keywords?: string[]
  /** When false, command is hidden from the palette. Default true. */
  when?: (ctx: CommandContext) => boolean
  run: (ctx: CommandContext) => void
}

export type CommandConflictWarnFn = (
  message: string,
  existingId: string,
  newId: string,
) => void

export class CommandRegistry {
  private commands = new Map<string, Command>()
  private onConflict: CommandConflictWarnFn

  constructor(onConflict?: CommandConflictWarnFn) {
    this.onConflict =
      onConflict ??
      ((msg, a, b) => {
        if (import.meta.env?.DEV) {
          console.warn(`[commands] ${msg} (${a} vs ${b})`)
        }
      })
  }

  register(command: Command): () => void {
    if (this.commands.has(command.id)) {
      // Re-register replaces; warn only if titles differ (accidental reuse).
      const prev = this.commands.get(command.id)!
      if (prev.title !== command.title) {
        this.onConflict('id re-registered with different title', command.id, command.id)
      }
    }
    this.commands.set(command.id, command)
    return () => this.unregister(command.id)
  }

  unregister(id: string): void {
    this.commands.delete(id)
  }

  execute(id: string, ctx: CommandContext): void {
    const cmd = this.commands.get(id)
    if (!cmd) return
    if (cmd.when && !cmd.when(ctx)) return
    cmd.run(ctx)
  }

  /** Currently registered commands (optionally filtered by when). */
  list(ctx?: CommandContext): Command[] {
    const all = Array.from(this.commands.values())
    const filtered =
      ctx == null
        ? all
        : all.filter((c) => (c.when ? c.when(ctx) : true))
    return filtered.sort((a, b) => {
      const g = a.group.localeCompare(b.group)
      if (g !== 0) return g
      return a.title.localeCompare(b.title)
    })
  }

  get(id: string): Command | undefined {
    return this.commands.get(id)
  }

  clear(): void {
    this.commands.clear()
  }
}

/** Filter commands by free-text query (title + keywords). */
export function filterCommands(commands: Command[], query: string): Command[] {
  const q = query.trim().toLowerCase()
  if (!q) return commands
  return commands.filter((c) => {
    if (c.title.toLowerCase().includes(q)) return true
    if (c.group.toLowerCase().includes(q)) return true
    return (c.keywords ?? []).some((k) => k.toLowerCase().includes(q))
  })
}
