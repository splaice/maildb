import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { highlightSnippet } from './highlightSnippet'

describe('highlightSnippet', () => {
  it('wraps free-text hits in mark without HTML injection', () => {
    const { container } = render(
      <div>{highlightSnippet('hello <script>alert(1)</script> world', 'script')}</div>,
    )
    // Free text is text content — angle brackets are not raw HTML nodes.
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('mark')).toHaveTextContent('script')
    expect(container.textContent).toContain('<script>alert(1)</script>')
  })

  it('returns plain text when no needle', () => {
    const { container } = render(<div>{highlightSnippet('plain text', '')}</div>)
    expect(container.querySelector('mark')).toBeNull()
    expect(container).toHaveTextContent('plain text')
  })
})
