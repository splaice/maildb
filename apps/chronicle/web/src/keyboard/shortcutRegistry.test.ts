import { describe, expect, it, vi } from 'vitest'

import { isEditableTarget } from './isEditableTarget'
import { eventToChordKey, ShortcutRegistry } from './shortcutRegistry'

describe('ShortcutRegistry', () => {
  it('register / match / execute / cleanup', () => {
    const reg = new ShortcutRegistry(() => {})
    const run = vi.fn()
    const unsub = reg.register({
      id: 'test.j',
      chord: { key: 'j' },
      description: 'Next',
      group: 'Test',
      run,
    })
    expect(reg.list()).toHaveLength(1)
    const e = new KeyboardEvent('keydown', { key: 'j' })
    const binding = reg.match(e)
    expect(binding?.id).toBe('test.j')
    binding?.run(e)
    expect(run).toHaveBeenCalled()
    unsub()
    expect(reg.list()).toHaveLength(0)
    expect(reg.match(e)).toBeNull()
  })

  it('warns on chord conflict', () => {
    const warn = vi.fn()
    const reg = new ShortcutRegistry(warn)
    reg.register({
      id: 'a',
      chord: { key: 'x' },
      description: 'A',
      group: 'T',
      run: () => {},
    })
    reg.register({
      id: 'b',
      chord: { key: 'x' },
      description: 'B',
      group: 'T',
      run: () => {},
    })
    expect(warn).toHaveBeenCalled()
  })

  it('matches mod+k for palette', () => {
    const reg = new ShortcutRegistry(() => {})
    reg.register({
      id: 'palette',
      chord: { key: 'k', mod: true },
      description: 'Palette',
      group: 'G',
      run: () => {},
    })
    const e = new KeyboardEvent('keydown', { key: 'k', metaKey: true })
    expect(eventToChordKey(e)).toBe('mod+k')
    expect(reg.match(e)?.id).toBe('palette')
  })
})

describe('isEditableTarget', () => {
  it('detects input / textarea / contenteditable', () => {
    const input = document.createElement('input')
    expect(isEditableTarget(input)).toBe(true)
    const ta = document.createElement('textarea')
    expect(isEditableTarget(ta)).toBe(true)
    const div = document.createElement('div')
    expect(isEditableTarget(div)).toBe(false)
    div.contentEditable = 'true'
    // jsdom may not reflect isContentEditable from the property alone
    Object.defineProperty(div, 'isContentEditable', { get: () => true })
    expect(isEditableTarget(div)).toBe(true)
  })
})
