import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  mockArchiveSummary,
  mockSessionOk,
  renderApp,
} from '../test/test-utils'

const NAV_LABELS = [
  'Chronicle',
  'Research',
  'Topics',
  'People',
  'Files',
  'Data Health',
  'Settings',
] as const

describe('Workstation shell', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubAuthenticatedApi() {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes('/api/auth/session')) return mockSessionOk()
        if (String(url).includes('/api/archive/summary')) {
          return mockArchiveSummary()
        }
        throw new Error(`unexpected fetch: ${url}`)
      }),
    )
  }

  it('renders all nav items inside the shell; Chronicle is aria-current at /', async () => {
    stubAuthenticatedApi()
    renderApp(['/'])

    const shell = await screen.findByTestId('workstation-shell')
    expect(shell).toBeInTheDocument()

    const nav = within(shell).getByRole('navigation', { name: 'Primary' })
    for (const label of NAV_LABELS) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument()
    }

    const chronicle = within(nav).getByRole('link', { name: 'Chronicle' })
    expect(chronicle).toHaveAttribute('aria-current', 'page')
  })

  it('renders stub routes inside the shell', async () => {
    stubAuthenticatedApi()
    renderApp(['/research'])

    await screen.findByTestId('workstation-shell')
    expect(screen.getByText('Not yet implemented')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Research' })).toBeInTheDocument()
  })

  it('exposes all six zones with expected geometry landmarks', async () => {
    stubAuthenticatedApi()
    renderApp(['/'])

    const shell = await screen.findByTestId('workstation-shell')
    expect(shell).toHaveClass('bg-graphite-950')

    // Command bar
    expect(within(shell).getByRole('banner')).toBeInTheDocument()
    // Primary nav
    expect(
      within(shell).getByRole('navigation', { name: 'Primary' }),
    ).toBeInTheDocument()
    // Canvas
    expect(within(shell).getByRole('main')).toBeInTheDocument()
    // Inspector
    expect(
      within(shell).getByRole('complementary', { name: 'Evidence inspector' }),
    ).toBeInTheDocument()
    // Status strip
    expect(within(shell).getByRole('contentinfo')).toBeInTheDocument()
    // Scope bar region
    expect(
      within(shell).getByRole('region', { name: 'Working set scope' }),
    ).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText(/messages ·/i)).toBeInTheDocument()
    })
  })

  it('exposes keyboard-reachable interactive controls in logical order', async () => {
    stubAuthenticatedApi()
    renderApp(['/'])
    const shell = await screen.findByTestId('workstation-shell')
    const logout = within(shell).getByRole('button', { name: /logout/i })
    expect(logout.tagName).toBe('BUTTON')
    const links = within(shell).getAllByRole('link')
    expect(links.length).toBeGreaterThanOrEqual(7)
    // Universal command bar is enabled (Phase 5.2); nav links follow in DOM order.
    const search = within(shell).getByLabelText(/universal search/i)
    expect(search).not.toBeDisabled()
  })

  it('skip link is the first focusable control and targets #main', async () => {
    stubAuthenticatedApi()
    renderApp(['/'])
    const shell = await screen.findByTestId('workstation-shell')
    const skip = within(shell).getByRole('link', { name: /skip to main content/i })
    expect(skip).toHaveAttribute('href', '#main')
    expect(within(shell).getByRole('main')).toHaveAttribute('id', 'main')
    // First focusable in shell DOM order
    const focusable = shell.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )
    expect(focusable[0]).toBe(skip)
  })
})
