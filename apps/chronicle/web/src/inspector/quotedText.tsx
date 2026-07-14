/**
 * Quoted-text collapse for plain text and sanitized HTML.
 * Never inserts unsanitized HTML — server already sanitized; DOM transforms
 * run on a detached container after parsing trusted markup.
 */

import type { ReactNode } from 'react'

/** Collapse consecutive lines starting with `>` into a <details> block. */
export function collapsePureQuotedBlocks(
  text: string,
): { type: 'text' | 'quote'; content: string; lines: number }[] {
  const lines = text.split('\n')
  const blocks: { type: 'text' | 'quote'; content: string; lines: number }[] = []
  let i = 0
  while (i < lines.length) {
    if (lines[i]!.startsWith('>')) {
      const start = i
      while (i < lines.length && lines[i]!.startsWith('>')) i += 1
      blocks.push({
        type: 'quote',
        content: lines.slice(start, i).join('\n'),
        lines: i - start,
      })
    } else {
      const start = i
      while (i < lines.length && !lines[i]!.startsWith('>')) i += 1
      const content = lines.slice(start, i).join('\n')
      // Skip a single empty line from a pure empty string input.
      if (content.length > 0) {
        blocks.push({ type: 'text', content, lines: i - start })
      } else if (i - start > 1 || (i - start === 1 && lines[start] !== '')) {
        blocks.push({ type: 'text', content, lines: i - start })
      }
    }
  }
  return blocks
}

/** Collapse consecutive lines starting with `>` into a <details> block. */
export function collapsePlainQuotedText(text: string): ReactNode[] {
  const lines = text.split('\n')
  const nodes: React.ReactNode[] = []
  let i = 0
  let key = 0
  while (i < lines.length) {
    if (lines[i]!.startsWith('>')) {
      const start = i
      while (i < lines.length && lines[i]!.startsWith('>')) i += 1
      const block = lines.slice(start, i).join('\n')
      const n = i - start
      nodes.push(
        <details key={`q-${key++}`} className="my-1 rounded border border-steel bg-graphite-800 px-2 py-1">
          <summary className="cursor-pointer text-text-muted">
            Show quoted text · {n} line{n === 1 ? '' : 's'}
          </summary>
          <pre className="mt-1 whitespace-pre-wrap font-mono text-text-muted">{block}</pre>
        </details>,
      )
    } else {
      const start = i
      while (i < lines.length && !lines[i]!.startsWith('>')) i += 1
      const block = lines.slice(start, i).join('\n')
      if (block.length > 0) {
        nodes.push(
          <span key={`t-${key++}`} className="whitespace-pre-wrap">
            {block}
            {i < lines.length ? '\n' : ''}
          </span>,
        )
      }
    }
  }
  return nodes
}

/**
 * Wrap top-level <blockquote> elements in <details> on a detached container.
 * Input must already be sanitized HTML from the server.
 */
export function wrapBlockquotesInDetails(sanitizedHtml: string): string {
  if (typeof DOMParser === 'undefined') {
    // SSR / non-DOM: return as-is
    return sanitizedHtml
  }
  const parser = new DOMParser()
  const doc = parser.parseFromString(
    `<div id="root">${sanitizedHtml}</div>`,
    'text/html',
  )
  const root = doc.getElementById('root')
  if (!root) return sanitizedHtml

  // Only direct-child blockquotes (top-level within the body fragment)
  const children = Array.from(root.childNodes)
  for (const child of children) {
    if (child.nodeType !== 1) continue
    const el = child as Element
    if (el.tagName.toLowerCase() !== 'blockquote') continue
    const details = doc.createElement('details')
    details.className = 'my-1 rounded border border-steel bg-graphite-800 px-2 py-1'
    const summary = doc.createElement('summary')
    summary.className = 'cursor-pointer text-text-muted'
    summary.textContent = 'Show quoted text'
    details.appendChild(summary)
    // Move the blockquote into details
    root.insertBefore(details, el)
    details.appendChild(el)
  }
  return root.innerHTML
}
