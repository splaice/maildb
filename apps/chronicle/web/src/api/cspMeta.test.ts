import { describe, expect, it } from 'vitest'

import html from '../../index.html?raw'

describe('index.html CSP meta (defense-in-depth §15.2)', () => {
  it('declares Content-Security-Policy meta with object-src none', () => {
    expect(html).toMatch(/http-equiv=["']Content-Security-Policy["']/)
    expect(html).toContain("object-src 'none'")
    expect(html).toContain("default-src 'self'")
    // No remote hosts in connect-src / script-src
    expect(html).not.toMatch(/connect-src[^"]*https?:\/\//)
    expect(html).not.toMatch(/script-src[^"]*https?:\/\//)
  })
})
