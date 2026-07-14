import { describe, expect, it } from 'vitest'

import {
  collapsePlainQuotedText,
  collapsePureQuotedBlocks,
  wrapBlockquotesInDetails,
} from './quotedText'

describe('collapsePureQuotedBlocks / collapsePlainQuotedText', () => {
  it('collapses consecutive > lines into a quote block', () => {
    const text = ['Hello', '> quoted one', '> quoted two', 'After'].join('\n')
    const blocks = collapsePureQuotedBlocks(text)
    expect(blocks).toEqual([
      { type: 'text', content: 'Hello', lines: 1 },
      { type: 'quote', content: '> quoted one\n> quoted two', lines: 2 },
      { type: 'text', content: 'After', lines: 1 },
    ])

    const nodes = collapsePlainQuotedText(text)
    expect(nodes.length).toBe(3)
  })

  it('handles all-quoted and empty', () => {
    expect(collapsePureQuotedBlocks('> only')).toEqual([
      { type: 'quote', content: '> only', lines: 1 },
    ])
    expect(collapsePureQuotedBlocks('')).toEqual([])
  })
})

describe('wrapBlockquotesInDetails', () => {
  it('wraps top-level blockquote in details', () => {
    const html = '<p>Hi</p><blockquote><p>quoted</p></blockquote><p>Bye</p>'
    const out = wrapBlockquotesInDetails(html)
    expect(out).toContain('<details')
    expect(out).toContain('Show quoted text')
    expect(out).toContain('<blockquote>')
    expect(out).toContain('Hi')
    expect(out).toContain('Bye')
  })

  it('leaves nested-only content without inventing blockquotes', () => {
    const html = '<p>No quotes here</p>'
    expect(wrapBlockquotesInDetails(html)).toContain('No quotes here')
    expect(wrapBlockquotesInDetails(html)).not.toContain('<details')
  })
})
