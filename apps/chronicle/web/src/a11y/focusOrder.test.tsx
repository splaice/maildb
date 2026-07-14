import { screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { classifyFocusTarget, getFocusableElements } from './focusable'
import { A11Y_ROUTES, renderA11yRoute, stubA11yFetch } from './renderRoute'

describe('focus order (first 10 focusable)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  for (const route of A11Y_ROUTES) {
    it(`${route}: starts skip-link → command bar → nav → main`, async () => {
      vi.stubGlobal('fetch', vi.fn().mockImplementation(stubA11yFetch()))
      renderA11yRoute(route)

      await waitFor(() => {
        expect(screen.getByTestId('workstation-shell')).toBeInTheDocument()
      })

      // Wait for auth shell chrome
      await screen.findByRole('navigation', { name: 'Primary' })

      const focusable = getFocusableElements(
        screen.getByTestId('workstation-shell'),
      ).slice(0, 10)
      expect(focusable.length).toBeGreaterThanOrEqual(4)

      const classes = focusable.map(classifyFocusTarget)

      // Structural order: skip-link first, then command-bar items, then nav, then main
      expect(classes[0]).toBe('skip-link')

      const firstCmd = classes.findIndex((c) => c === 'command-bar')
      const firstNav = classes.findIndex((c) => c === 'nav')
      const firstMain = classes.findIndex((c) => c === 'main')

      expect(firstCmd).toBeGreaterThan(0)
      expect(firstNav).toBeGreaterThan(firstCmd)
      // Main content may appear after nav (and after scope-bar "other" items)
      if (firstMain >= 0) {
        expect(firstMain).toBeGreaterThan(firstNav)
      }

      // Sequence of region transitions is monotonic: skip → cmd → nav → (main)
      let stage = 0
      const stages = ['skip-link', 'command-bar', 'nav', 'main'] as const
      for (const c of classes) {
        if (c === 'other') continue
        const idx = stages.indexOf(c as (typeof stages)[number])
        if (idx < 0) continue
        expect(idx).toBeGreaterThanOrEqual(stage)
        stage = idx
      }
    })
  }
})
