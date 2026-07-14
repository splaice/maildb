import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ShortcutProvider } from '../keyboard'
import {
  createTestQueryClient,
  mockSessionOk,
  renderWithProviders,
} from '../test/test-utils'
import { LANES_STORAGE_KEY } from '../workingset/urlState'
import {
  DENSITY_CLASS,
  DENSITY_STORAGE_KEY,
  REDUCE_MOTION_CLASS,
  REDUCED_MOTION_STORAGE_KEY,
} from './appearance'
import { SettingsPage } from './SettingsPage'

function installMemoryLocalStorage(): void {
  const map = new Map<string, string>()
  const storage: Storage = {
    get length() {
      return map.size
    },
    clear() {
      map.clear()
    },
    getItem(key: string) {
      return map.has(key) ? map.get(key)! : null
    },
    key(index: number) {
      return [...map.keys()][index] ?? null
    },
    removeItem(key: string) {
      map.delete(key)
    },
    setItem(key: string, value: string) {
      map.set(key, String(value))
    },
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
    writable: true,
  })
}

const defaultSettings = {
  ai: {
    ask_enabled: true,
    interpret_enabled: true,
    generate_enabled: true,
    answer_model: 'llama3.2',
    retention_note: 'Local Ollama route; prompts are not retained.',
  },
  privacy: { session_max_age_s: 43200 },
  search: { default_mode: 'hybrid' },
  chronicle: { default_lanes: ['messages', 'attachments', 'top_people'] },
}

function mockFetch(handlers: Record<string, (init?: RequestInit) => unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const path = String(url)
      if (path.includes('/api/auth/session')) return mockSessionOk()
      for (const [key, fn] of Object.entries(handlers)) {
        if (path.includes(key)) {
          const body = fn(init)
          return {
            ok: true,
            status: 200,
            json: async () => body,
          } as Response
        }
      }
      throw new Error(`unexpected fetch: ${path}`)
    }),
  )
}

function renderSettings() {
  const client = createTestQueryClient()
  return renderWithProviders(
    <ShortcutProvider>
      <SettingsPage />
    </ShortcutProvider>,
    { client, initialEntries: ['/settings'] },
  )
}

describe('SettingsPage', () => {
  beforeEach(() => {
    installMemoryLocalStorage()
    document.body.classList.remove(
      DENSITY_CLASS.compact,
      DENSITY_CLASS.comfortable,
      REDUCE_MOTION_CLASS,
    )
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    installMemoryLocalStorage()
    document.body.classList.remove(
      DENSITY_CLASS.compact,
      DENSITY_CLASS.comfortable,
      REDUCE_MOTION_CLASS,
    )
  })

  it('renders settings groups', async () => {
    mockFetch({
      '/api/settings': () => defaultSettings,
    })
    renderSettings()
    expect(await screen.findByTestId('settings-page')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Appearance' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'AI and models' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Search' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chronicle' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Privacy' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Keyboard' })).toBeInTheDocument()
  })

  it('shows model route + retention note read-only', async () => {
    mockFetch({ '/api/settings': () => defaultSettings })
    renderSettings()
    const route = await screen.findByTestId('model-route-display')
    expect(route).toHaveTextContent(/Local route: ollama/)
    expect(route).toHaveTextContent('llama3.2')
    expect(route).toHaveTextContent(/Local Ollama/)
    expect(
      screen.getByText(/External providers are not configured/),
    ).toBeInTheDocument()
  })

  it('density change persists and applies body class', async () => {
    mockFetch({ '/api/settings': () => defaultSettings })
    renderSettings()
    await screen.findByTestId('settings-page')
    fireEvent.click(screen.getByRole('radio', { name: 'Comfortable' }))
    expect(localStorage.getItem(DENSITY_STORAGE_KEY)).toBe('comfortable')
    expect(document.body.classList.contains(DENSITY_CLASS.comfortable)).toBe(
      true,
    )
  })

  it('reduced motion Always sets flag for TimelineCanvas props', async () => {
    mockFetch({ '/api/settings': () => defaultSettings })
    renderSettings()
    await screen.findByTestId('settings-page')
    fireEvent.click(screen.getByRole('radio', { name: 'Always' }))
    expect(localStorage.getItem(REDUCED_MOTION_STORAGE_KEY)).toBe('always')
    expect(document.body.classList.contains(REDUCE_MOTION_CLASS)).toBe(true)
    const flag = screen.getByTestId('timeline-reduced-motion')
    expect(flag).toHaveAttribute('data-reduced-motion', 'true')
  })

  it('AI toggle PUTs optimistically', async () => {
    let stored = { ...defaultSettings, ai: { ...defaultSettings.ai } }
    mockFetch({
      '/api/settings': (init) => {
        if (init?.method === 'PUT') {
          const patch = JSON.parse(String(init.body)) as {
            ai?: { ask_enabled?: boolean }
          }
          stored = {
            ...stored,
            ai: { ...stored.ai, ...patch.ai },
          }
          return stored
        }
        return stored
      },
    })
    renderSettings()
    const toggle = await screen.findByTestId('ai-toggle-ask_enabled')
    expect(toggle).toBeChecked()
    fireEvent.click(toggle)
    await waitFor(() => {
      expect(screen.getByTestId('settings-save-hint')).toHaveTextContent(/saved/i)
    })
    expect(stored.ai.ask_enabled).toBe(false)
  })

  it('default lanes write localStorage lens store', async () => {
    mockFetch({
      '/api/settings': (init) => {
        if (init?.method === 'PUT') {
          const patch = JSON.parse(String(init.body)) as {
            chronicle?: { default_lanes?: string[] }
          }
          return {
            ...defaultSettings,
            chronicle: {
              default_lanes:
                patch.chronicle?.default_lanes ??
                defaultSettings.chronicle.default_lanes,
            },
          }
        }
        return defaultSettings
      },
    })
    renderSettings()
    await screen.findByTestId('settings-page')
    fireEvent.click(screen.getByTestId('lane-default-events'))
    await waitFor(() => {
      const raw = localStorage.getItem(LANES_STORAGE_KEY)
      expect(raw).toBeTruthy()
      expect(raw).toContain('events')
    })
  })

  it('Privacy links Audit trail to Data Health', async () => {
    mockFetch({ '/api/settings': () => defaultSettings })
    renderSettings()
    await screen.findByTestId('settings-page')
    const link = screen.getByRole('link', { name: 'Audit trail' })
    expect(link).toHaveAttribute('href', '/data-health')
  })
})
