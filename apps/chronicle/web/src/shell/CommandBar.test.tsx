import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetWorkingSetStore, useWorkingSetStore } from '../workingset/store'
import { CommandBar } from './CommandBar'

function renderBar() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CommandBar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('CommandBar', () => {
  beforeEach(() => {
    resetWorkingSetStore()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        const u = String(url)
        if (u.includes('/api/auth/session')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ username: 'analyst' }),
          } as Response
        }
        if (u.includes('/api/people')) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              items: [
                {
                  id: 'c1',
                  display_name: 'Alice Chen',
                  kind: 'human',
                  kind_source: null,
                  tags: [],
                  human_probability: 1,
                  addresses: ['alice@example.com'],
                  name_variants: [],
                  messages_from: 10,
                  messages_to: 5,
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
                  label: 'House renovation',
                  origin: 'manual',
                  member_count: 3,
                  hidden: false,
                  top_terms: [],
                  children: [],
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
    resetWorkingSetStore()
  })

  it('shows mode segmented control before execution', () => {
    renderBar()
    expect(screen.getByTestId('command-bar-mode')).toBeInTheDocument()
    expect(screen.getByTestId('command-bar-mode-search')).toHaveAttribute(
      'aria-checked',
      'true',
    )
    fireEvent.click(screen.getByTestId('command-bar-mode-ask'))
    expect(screen.getByTestId('command-bar-mode-ask')).toHaveAttribute(
      'aria-checked',
      'true',
    )
    fireEvent.click(screen.getByTestId('command-bar-mode-explore'))
    expect(screen.getByTestId('command-bar-mode-explore')).toHaveAttribute(
      'aria-checked',
      'true',
    )
  })

  it('Search mode navigates to /research with query', async () => {
    renderBar()
    const input = screen.getByTestId('command-bar-input')
    fireEvent.change(input, { target: { value: 'roof decision' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => {
      expect(useWorkingSetStore.getState().query).toBe('roof decision')
    })
  })

  it('Explore mode jumps viewport for date phrases', async () => {
    renderBar()
    fireEvent.click(screen.getByTestId('command-bar-mode-explore'))
    const input = screen.getByTestId('command-bar-input')
    fireEvent.change(input, { target: { value: '2015' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => {
      const vp = useWorkingSetStore.getState().viewport
      expect(vp?.fromMs).toBe(Date.UTC(2015, 0, 1))
      expect(vp?.toMs).toBe(Date.UTC(2016, 0, 1))
    })
  })

  it('autocomplete keyboard flow with listbox roles; person pick adds scope', async () => {
    renderBar()
    const input = screen.getByTestId('command-bar-input')
    fireEvent.change(input, { target: { value: 'ali' } })

    await waitFor(() => {
      expect(screen.getByTestId('command-bar-suggest')).toBeInTheDocument()
    })
    const listbox = screen.getByRole('listbox', { name: 'Suggestions' })
    expect(listbox).toBeInTheDocument()
    const options = screen.getAllByRole('option')
    expect(options.length).toBeGreaterThan(0)

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(useWorkingSetStore.getState().scope.senders).toContain(
        'alice@example.com',
      )
    })
  })

  it('Escape closes autocomplete', async () => {
    renderBar()
    const input = screen.getByTestId('command-bar-input')
    fireEvent.change(input, { target: { value: 'ali' } })
    await waitFor(() => {
      expect(screen.getByTestId('command-bar-suggest')).toBeInTheDocument()
    })
    fireEvent.keyDown(input, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByTestId('command-bar-suggest')).not.toBeInTheDocument()
    })
  })
})
