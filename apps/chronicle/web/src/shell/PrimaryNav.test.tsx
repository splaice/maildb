import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import { PrimaryNav } from './PrimaryNav'

describe('PrimaryNav', () => {
  it('includes Workspaces nav item linking to /workspaces', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <PrimaryNav />
      </MemoryRouter>,
    )
    const link = screen.getByRole('link', { name: 'Workspaces' })
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', '/workspaces')
  })

  it('marks Workspaces as current on /workspaces', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces']}>
        <PrimaryNav />
      </MemoryRouter>,
    )
    const link = screen.getByRole('link', { name: 'Workspaces' })
    expect(link).toHaveAttribute('aria-current', 'page')
  })
})
