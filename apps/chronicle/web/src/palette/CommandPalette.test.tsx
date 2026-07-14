import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  FOCUS_COMMAND_BAR_EVENT,
  OPEN_PALETTE_EVENT,
  ShortcutProvider,
} from '../keyboard'
import { CommandPalette } from './CommandPalette'
import { CommandRegistryProvider } from './CommandContext'
import { clearRecents, loadRecents, pushRecent } from './recents'

function renderPalette() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ShortcutProvider>
          <CommandRegistryProvider>
            <CommandPalette />
            <input data-testid="prior-focus" />
          </CommandRegistryProvider>
        </ShortcutProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('CommandPalette', () => {
  beforeEach(() => {
    clearRecents()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        const u = String(url)
        if (u.includes('/api/people')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              items: [
                {
                  id: 'c1',
                  display_name: 'Alice',
                  kind: 'human',
                  kind_source: null,
                  tags: [],
                  human_probability: 1,
                  addresses: ['alice@x.com'],
                  name_variants: [],
                  messages_from: 1,
                  messages_to: 0,
                  first_seen: null,
                  last_seen: null,
                },
              ],
              total: 1,
              next_cursor: null,
              limit: 5,
              offset: 0,
            }),
          } as Response
        }
        if (u.includes('/api/topics')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              topics: [
                {
                  id: 't1',
                  label: 'Renovation',
                  origin: 'manual',
                  member_count: 1,
                  hidden: false,
                  top_terms: [],
                  children: [],
                },
              ],
            }),
          } as Response
        }
        if (u.includes('/api/workspaces')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              items: [
                {
                  id: 'w1',
                  name: 'Roof notes',
                  updated_at: null,
                  counts: {
                    blocks: 0,
                    pins: 0,
                    notes: 0,
                    answers: 0,
                    headings: 0,
                  },
                },
              ],
            }),
          } as Response
        }
        return { ok: true, status: 200, json: async () => ({}) } as Response
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    clearRecents()
  })

  it('opens via event, traps focus, Escape restores prior focus', async () => {
    renderPalette()
    const prior = screen.getByTestId('prior-focus')
    prior.focus()
    expect(document.activeElement).toBe(prior)

    window.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT))
    const dialog = await screen.findByTestId('command-palette')
    expect(dialog).toBeInTheDocument()
    await waitFor(() => {
      expect(document.activeElement).toHaveAttribute(
        'aria-label',
        'Command palette query',
      )
    })

    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByTestId('command-palette')).not.toBeInTheDocument()
    })
    await waitFor(() => {
      expect(document.activeElement).toBe(prior)
    })
  })

  it('shows grouped results from mocks', async () => {
    renderPalette()
    window.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT))
    await screen.findByTestId('command-palette')
    const input = screen.getByLabelText('Command palette query')
    fireEvent.change(input, { target: { value: 'ali' } })

    await waitFor(() => {
      expect(screen.getByTestId('palette-group-People')).toBeInTheDocument()
    })
    expect(screen.getByText('Alice')).toBeInTheDocument()
  })

  it('shows routes when typing Go to', async () => {
    renderPalette()
    window.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT))
    await screen.findByTestId('command-palette')
    const input = screen.getByLabelText('Command palette query')
    fireEvent.change(input, { target: { value: 'Chronicle' } })
    await waitFor(() => {
      expect(screen.getByText('Go to Chronicle')).toBeInTheDocument()
    })
  })

  it('shows date jump for parseable phrases', async () => {
    renderPalette()
    window.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT))
    await screen.findByTestId('command-palette')
    const input = screen.getByLabelText('Command palette query')
    fireEvent.change(input, { target: { value: '2015' } })
    await waitFor(() => {
      expect(screen.getByText(/Jump to 2015/)).toBeInTheDocument()
    })
  })

  it('shows recents when input is empty', async () => {
    clearRecents()
    const entries = pushRecent({ id: 'route.chronicle', title: 'Go to Chronicle' })
    expect(entries).toHaveLength(1)
    expect(loadRecents()[0]!.id).toBe('route.chronicle')
    renderPalette()
    window.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT))
    await screen.findByTestId('command-palette')
    // Empty query → recent commands section
    await waitFor(() => {
      expect(screen.getByTestId('palette-group-Recent')).toBeInTheDocument()
    })
    expect(screen.getByText('Go to Chronicle')).toBeInTheDocument()
  })
})

describe('shortcut input guard', () => {
  it('typing / in an input does NOT focus the command bar', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const focusSpy = vi.fn()
    window.addEventListener(FOCUS_COMMAND_BAR_EVENT, focusSpy)

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ShortcutProvider>
            <input data-testid="other-input" />
          </ShortcutProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    const other = screen.getByTestId('other-input')
    other.focus()
    fireEvent.keyDown(other, { key: '/' })
    expect(focusSpy).not.toHaveBeenCalled()

    // Outside input, / should fire
    fireEvent.keyDown(window, { key: '/' })
    expect(focusSpy).toHaveBeenCalled()
    window.removeEventListener(FOCUS_COMMAND_BAR_EVENT, focusSpy)
  })
})
