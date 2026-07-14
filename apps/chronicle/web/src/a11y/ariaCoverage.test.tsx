import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { A11Y_ROUTES, renderA11yRoute, stubA11yFetch } from './renderRoute'

function accessibleName(el: Element): string {
  if (!(el instanceof HTMLElement)) return ''
  const labelled = el.getAttribute('aria-label')
  if (labelled?.trim()) return labelled.trim()
  const labelledBy = el.getAttribute('aria-labelledby')
  if (labelledBy) {
    const parts = labelledBy
      .split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent?.trim() ?? '')
      .filter(Boolean)
    if (parts.length) return parts.join(' ')
  }
  const text = (el.textContent ?? '').replace(/\s+/g, ' ').trim()
  if (text) return text
  if (el instanceof HTMLInputElement) {
    return (el.getAttribute('placeholder') ?? el.getAttribute('name') ?? '').trim()
  }
  if (el instanceof HTMLImageElement) {
    return (el.getAttribute('alt') ?? '').trim()
  }
  return ''
}

describe('aria / landmark coverage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  for (const route of A11Y_ROUTES) {
    it(`${route}: banner/nav/main/complementary once each; named buttons and links`, async () => {
      vi.stubGlobal('fetch', vi.fn().mockImplementation(stubA11yFetch()))
      renderA11yRoute(route)

      const shell = await screen.findByTestId('workstation-shell')

      await waitFor(() => {
        expect(within(shell).getByRole('navigation', { name: 'Primary' })).toBeInTheDocument()
      })

      const banners = within(shell).getAllByRole('banner')
      expect(banners).toHaveLength(1)

      const navs = within(shell).getAllByRole('navigation', { name: 'Primary' })
      expect(navs).toHaveLength(1)

      const mains = within(shell).getAllByRole('main')
      expect(mains).toHaveLength(1)

      const complements = within(shell).getAllByRole('complementary', {
        name: 'Evidence inspector',
      })
      expect(complements).toHaveLength(1)

      // Every button and link has an accessible name
      const buttons = within(shell).getAllByRole('button')
      const links = within(shell).getAllByRole('link')
      const offenders: string[] = []
      for (const el of [...buttons, ...links]) {
        const name = accessibleName(el)
        if (!name) {
          offenders.push(
            `${el.tagName.toLowerCase()}#${el.getAttribute('data-testid') ?? el.className}`,
          )
        }
      }
      expect(offenders, `unnamed controls: ${offenders.join(', ')}`).toEqual([])
    })
  }
})
