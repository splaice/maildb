/**
 * PE-004: no relationship-quality language in people/** copy.
 * Assert none of the strings close/important/intimate/best friend appear in people/**.
 */
import { describe, expect, it } from 'vitest'

// Vite raw imports — all people/** sources (exclude this audit file).
import activityBars from './ActivityBars.tsx?raw'
import api from './api.ts?raw'
import egoGraph from './EgoGraph.tsx?raw'
import egoGraphTest from './EgoGraph.test.tsx?raw'
import egoLayout from './egoLayout.ts?raw'
import egoLayoutTest from './egoLayout.test.ts?raw'
import peoplePage from './PeoplePage.tsx?raw'
import peoplePageTest from './PeoplePage.test.tsx?raw'
import personProfile from './PersonProfilePage.tsx?raw'
import personProfileTest from './PersonProfilePage.test.tsx?raw'

const SOURCES: { name: string; text: string }[] = [
  { name: 'ActivityBars.tsx', text: activityBars },
  { name: 'api.ts', text: api },
  { name: 'EgoGraph.tsx', text: egoGraph },
  { name: 'EgoGraph.test.tsx', text: egoGraphTest },
  { name: 'egoLayout.ts', text: egoLayout },
  { name: 'egoLayout.test.ts', text: egoLayoutTest },
  { name: 'PeoplePage.tsx', text: peoplePage },
  { name: 'PeoplePage.test.tsx', text: peoplePageTest },
  { name: 'PersonProfilePage.tsx', text: personProfile },
  { name: 'PersonProfilePage.test.tsx', text: personProfileTest },
]

const FORBIDDEN = ['close', 'important', 'intimate', 'best friend'] as const

describe('people copy audit (PE-004)', () => {
  it('assert none of the strings close/important/intimate/best friend appear in people/**', () => {
    const hits: string[] = []
    for (const { name, text } of SOURCES) {
      const lower = text.toLowerCase()
      for (const word of FORBIDDEN) {
        if (lower.includes(word)) hits.push(`${name}: ${word}`)
      }
    }
    expect(hits).toEqual([])
  })
})
