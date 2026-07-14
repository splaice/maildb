import { describe, expect, it, vi } from 'vitest'

import { CommandRegistry, filterCommands } from './commandRegistry'
import { clearRecents, loadRecents, pushRecent } from './recents'

describe('CommandRegistry', () => {
  it('register / execute / cleanup', () => {
    const reg = new CommandRegistry(() => {})
    const run = vi.fn()
    const unsub = reg.register({
      id: 'go.home',
      title: 'Go home',
      group: 'Routes',
      run,
    })
    expect(reg.list()).toHaveLength(1)
    reg.execute('go.home', { navigate: vi.fn() })
    expect(run).toHaveBeenCalled()
    unsub()
    expect(reg.list()).toHaveLength(0)
    reg.execute('go.home', { navigate: vi.fn() })
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('filters by when()', () => {
    const reg = new CommandRegistry(() => {})
    reg.register({
      id: 'focus',
      title: 'Focus period…',
      group: 'Actions',
      when: (ctx) => !!(ctx.getState?.().brush),
      run: () => {},
    })
    expect(reg.list({ navigate: vi.fn(), getState: () => ({}) })).toHaveLength(0)
    expect(
      reg.list({
        navigate: vi.fn(),
        getState: () => ({ brush: { fromMs: 1, toMs: 2 } }),
      }),
    ).toHaveLength(1)
  })

  it('filterCommands matches title and keywords', () => {
    const cmds = [
      {
        id: '1',
        title: 'Go to Chronicle',
        group: 'Routes',
        keywords: ['timeline'],
        run: () => {},
      },
      {
        id: '2',
        title: 'Go to Research',
        group: 'Routes',
        run: () => {},
      },
    ]
    expect(filterCommands(cmds, 'chron')).toHaveLength(1)
    expect(filterCommands(cmds, 'timeline')).toHaveLength(1)
    expect(filterCommands(cmds, '')).toHaveLength(2)
  })
})

describe('palette recents', () => {
  it('persists last 5 (returns + reloads)', () => {
    clearRecents()
    let last: ReturnType<typeof pushRecent> = []
    for (let i = 0; i < 7; i++) {
      last = pushRecent({ id: `c${i}`, title: `Command ${i}` })
    }
    expect(last).toHaveLength(5)
    expect(last[0]!.id).toBe('c6')
    expect(last.map((r) => r.id)).not.toContain('c0')
    // reload from storage (same key)
    const loaded = loadRecents()
    expect(loaded.map((r) => r.id)).toEqual(last.map((r) => r.id))
    clearRecents()
  })
})
