import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import { ShortcutProvider, useShortcuts } from './ShortcutContext'
import type { ShortcutBinding } from './shortcutRegistry'
import { setStatusHint } from './statusHint'

function PageWithShortcut() {
  const bindings: ShortcutBinding[] = [
    {
      id: 'test.custom',
      chord: { key: 'j' },
      description: 'Jump next test',
      group: 'Test',
      run: () => {},
    },
  ]
  useShortcuts(bindings)
  return <div>page</div>
}

describe('Shortcut reference overlay', () => {
  it('? lists registered shortcuts from the registry', async () => {
    render(
      <MemoryRouter>
        <ShortcutProvider>
          <PageWithShortcut />
        </ShortcutProvider>
      </MemoryRouter>,
    )

    fireEvent.keyDown(window, { key: '?' })
    const overlay = await screen.findByTestId('shortcut-reference')
    expect(overlay).toBeInTheDocument()
    // Global + page bindings
    expect(screen.getByText('Focus universal query')).toBeInTheDocument()
    expect(screen.getByText('Jump next test')).toBeInTheDocument()
    expect(screen.getByText('Test')).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByTestId('shortcut-reference')).not.toBeInTheDocument()
    })
  })

  it('G no-op shows status-strip hint without person selection', async () => {
    render(
      <MemoryRouter>
        <ShortcutProvider>
          <div />
        </ShortcutProvider>
      </MemoryRouter>,
    )
    setStatusHint('Select a person first to open the graph')
    expect(await screen.findByTestId('status-hint')).toHaveTextContent(
      /Select a person first/,
    )
  })
})
