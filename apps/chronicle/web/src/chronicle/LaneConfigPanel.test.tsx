import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LaneConfigPanel } from './LaneConfigPanel'
import { LANES_STORAGE_KEY } from '../workingset/urlState'

function installMemoryLocalStorage(): void {
  const map = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      get length() {
        return map.size
      },
      clear: () => map.clear(),
      getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
      key: (i: number) => [...map.keys()][i] ?? null,
      removeItem: (k: string) => {
        map.delete(k)
      },
      setItem: (k: string, v: string) => {
        map.set(k, String(v))
      },
    },
    configurable: true,
    writable: true,
  })
}

describe('LaneConfigPanel', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
  })

  it('toggles and reorders via keyboard-accessible controls', () => {
    const onToggle = vi.fn()
    const onMove = vi.fn()
    render(
      <LaneConfigPanel
        lanes={['messages', 'attachments', 'top_people']}
        onToggle={onToggle}
        onMove={onMove}
      />,
    )

    expect(screen.getByTestId('lane-config-panel')).toBeInTheDocument()
    expect(screen.getByLabelText('Show Messages')).toBeChecked()
    expect(screen.getByLabelText('Show People (distinct)')).not.toBeChecked()

    fireEvent.click(screen.getByLabelText('Show People (distinct)'))
    expect(onToggle).toHaveBeenCalledWith('people')

    fireEvent.click(screen.getByLabelText('Move Messages down'))
    expect(onMove).toHaveBeenCalledWith('messages', 'down')

    fireEvent.click(screen.getByLabelText('Move Top people up'))
    expect(onMove).toHaveBeenCalledWith('top_people', 'up')
  })

  it('saves current lanes as default lens', () => {
    render(
      <LaneConfigPanel
        lanes={['people', 'messages']}
        onToggle={() => {}}
        onMove={() => {}}
      />,
    )
    fireEvent.click(screen.getByTestId('save-default-lanes'))
    expect(localStorage.getItem(LANES_STORAGE_KEY)).toBe('people,messages')
  })
})
