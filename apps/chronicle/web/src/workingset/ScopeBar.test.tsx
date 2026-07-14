import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ScopeBar } from '../shell/ScopeBar'
import { resetWorkingSetStore, useWorkingSetStore } from './store'

function renderScopeBar() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ScopeBar />
    </QueryClientProvider>,
  )
}

describe('ScopeBar', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          accounts: [
            { account: 'me@example.com', messages: 100 },
            { account: 'work@example.com', messages: 50 },
          ],
          date_range: { from: '2010-01-01', to: '2024-01-01' },
          counts: { messages: 1, threads: 1, attachments: 0, contacts: 0 },
          extraction: { extracted: 0, failed: 0, skipped: 0, pending: 0 },
          embedding: { embedded: 0, missing: 0 },
          versions: { schema: 'maildb', api: '0.1.0' },
        }),
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    resetWorkingSetStore()
  })

  it('renders chips from store', () => {
    renderScopeBar()
    // After mount hydrate; then mutate store (analytical) so chips appear.
    act(() => {
      useWorkingSetStore.getState().setScopeDate({ from: '2014-01-01', to: '2018-12-31' })
      useWorkingSetStore.getState().addMailbox('me@example.com')
      useWorkingSetStore.getState().addSender('alice@x.com')
    })

    const region = screen.getByRole('region', { name: 'Working set scope' })
    expect(within(region).getByLabelText('Date: 2014 – 2018')).toBeInTheDocument()
    expect(within(region).getByLabelText('Mailbox: me@example.com')).toBeInTheDocument()
    expect(within(region).getByLabelText('Sender: alice@x.com')).toBeInTheDocument()
  })

  it('remove button updates store', () => {
    renderScopeBar()
    act(() => {
      useWorkingSetStore.getState().addMailbox('me@example.com')
    })

    fireEvent.click(
      screen.getByRole('button', { name: 'Remove filter Mailbox me@example.com' }),
    )
    expect(useWorkingSetStore.getState().scope.mailboxes).toBeUndefined()
  })

  it('reset clears scope and is disabled when pristine', () => {
    renderScopeBar()
    const reset = screen.getByRole('button', { name: 'Reset scope' })
    expect(reset).toBeDisabled()

    act(() => {
      useWorkingSetStore.getState().addMailbox('me@example.com')
    })
    expect(screen.getByRole('button', { name: 'Reset scope' })).not.toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Reset scope' }))
    expect(useWorkingSetStore.getState().scope).toEqual({})
    expect(screen.getByRole('button', { name: 'Reset scope' })).toBeDisabled()
  })

  it('arrow-key focus movement within chip list', () => {
    renderScopeBar()
    act(() => {
      useWorkingSetStore.getState().setScopeDate({ from: '2014-01-01', to: '2015-01-01' })
      useWorkingSetStore.getState().addMailbox('me@example.com')
    })

    const dateBtn = screen.getByRole('button', { name: 'Date: 2014 – 2015' })
    const removeDate = screen.getByRole('button', {
      name: 'Remove filter Date 2014 – 2015',
    })
    dateBtn.focus()
    expect(document.activeElement).toBe(dateBtn)

    fireEvent.keyDown(dateBtn, { key: 'ArrowRight' })
    expect(document.activeElement).toBe(removeDate)

    fireEvent.keyDown(removeDate, { key: 'ArrowRight' })
    const mailboxBtn = screen.getByRole('button', { name: 'Mailbox: me@example.com' })
    expect(document.activeElement).toBe(mailboxBtn)

    fireEvent.keyDown(mailboxBtn, { key: 'ArrowLeft' })
    expect(document.activeElement).toBe(removeDate)
  })
})
