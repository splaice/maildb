import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

import { resetUnauthorizedRedirect, setUnauthorizedRedirect } from '../api/client'

beforeEach(() => {
  // Avoid window.location.assign side effects in jsdom; tests that care
  // about redirect inject their own handler.
  setUnauthorizedRedirect(() => {})
})

afterEach(() => {
  cleanup()
  resetUnauthorizedRedirect()
})
