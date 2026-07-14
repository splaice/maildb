import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'

import type { SearchMode } from '../api/types'
import { LANE_CATALOG } from '../chronicle/laneModel'
import {
  DEFAULT_LANES,
  loadSavedLanes,
  saveLanesAsDefault,
} from '../workingset/urlState'
import {
  type Density,
  type ReducedMotionPref,
  getTimelineMotionProps,
  readDensity,
  readReducedMotion,
  writeDensity,
  writeReducedMotion,
} from './appearance'
import {
  type AppSettingsDocument,
  fetchSettings,
  putSettings,
} from './api'
import { ShortcutList } from './ShortcutList'

const SEARCH_MODES: { value: SearchMode; label: string }[] = [
  { value: 'hybrid', label: 'Hybrid' },
  { value: 'exact', label: 'Exact' },
  { value: 'semantic', label: 'Semantic' },
]

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-lg border border-steel bg-graphite-900 p-4">
      <h2 className="mb-3 text-sm font-medium text-text-primary">{title}</h2>
      {children}
    </section>
  )
}

function SaveHint({ text }: { text: string | null }) {
  if (!text) return null
  return (
    <p
      className="mt-2 text-[11px] text-attachment"
      role="status"
      aria-live="polite"
      data-testid="settings-save-hint"
    >
      {text}
    </p>
  )
}

export function SettingsPage() {
  const [density, setDensity] = useState<Density>(() => readDensity())
  const [motion, setMotion] = useState<ReducedMotionPref>(() => readReducedMotion())
  const [doc, setDoc] = useState<AppSettingsDocument | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [hint, setHint] = useState<string | null>(null)
  const [lanes, setLanes] = useState<string[]>(() => {
    return loadSavedLanes() ?? [...DEFAULT_LANES]
  })

  const flash = useCallback((msg: string) => {
    setHint(msg)
    window.setTimeout(() => setHint(null), 2000)
  }, [])

  useEffect(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        const d = await fetchSettings(ac.signal)
        if (ac.signal.aborted) return
        setDoc(d)
        if (d.chronicle?.default_lanes?.length) {
          setLanes(d.chronicle.default_lanes)
        }
      } catch {
        if (!ac.signal.aborted) {
          setLoadError('Could not load server settings')
        }
      }
    })()
    return () => ac.abort()
  }, [])

  const patchServer = useCallback(
    async (patch: Parameters<typeof putSettings>[0], label: string) => {
      // Optimistic: merge into local doc immediately
      setDoc((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          ...Object.fromEntries(
            Object.entries(patch).map(([k, v]) => [
              k,
              { ...(prev[k as keyof AppSettingsDocument] as object), ...(v as object) },
            ]),
          ),
        } as AppSettingsDocument
      })
      try {
        const next = await putSettings(patch)
        setDoc(next)
        flash(`${label} saved`)
      } catch {
        flash(`Failed to save ${label}`)
        // reload
        try {
          setDoc(await fetchSettings())
        } catch {
          /* keep optimistic */
        }
      }
    },
    [flash],
  )

  const onDensity = (d: Density) => {
    setDensity(d)
    writeDensity(d)
    flash('Density saved')
  }

  const onMotion = (m: ReducedMotionPref) => {
    setMotion(m)
    writeReducedMotion(m)
    flash('Motion preference saved')
  }

  const onLaneToggle = (key: string) => {
    setLanes((prev) => {
      const next = prev.includes(key)
        ? prev.filter((k) => k !== key)
        : [...prev, key]
      saveLanesAsDefault(next)
      void patchServer({ chronicle: { default_lanes: next } }, 'Default lanes')
      return next
    })
  }

  const motionProps = getTimelineMotionProps(motion)

  return (
    <div className="mx-auto max-w-2xl space-y-4" data-testid="settings-page">
      <h1 className="text-base font-medium text-text-primary">Settings</h1>

      {loadError ? (
        <p className="text-conflict" role="alert">
          {loadError}
        </p>
      ) : null}

      <SaveHint text={hint} />

      <Section title="Appearance">
        <fieldset className="mb-3">
          <legend className="mb-1 text-[12px] text-text-muted">Density</legend>
          <div className="flex gap-3" role="radiogroup" aria-label="Density">
            {(
              [
                ['compact', 'Compact'],
                ['comfortable', 'Comfortable'],
              ] as const
            ).map(([id, label]) => (
              <label key={id} className="flex items-center gap-1.5 text-[13px]">
                <input
                  type="radio"
                  name="density"
                  value={id}
                  checked={density === id}
                  onChange={() => onDensity(id)}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend className="mb-1 text-[12px] text-text-muted">Reduced motion</legend>
          <div
            className="flex gap-3"
            role="radiogroup"
            aria-label="Reduced motion"
          >
            {(
              [
                ['auto', 'Auto'],
                ['always', 'Always'],
              ] as const
            ).map(([id, label]) => (
              <label key={id} className="flex items-center gap-1.5 text-[13px]">
                <input
                  type="radio"
                  name="reduced-motion"
                  value={id}
                  checked={motion === id}
                  onChange={() => onMotion(id)}
                />
                {label}
              </label>
            ))}
          </div>
          <p className="mt-1 text-[11px] text-text-muted">
            Auto respects the system prefers-reduced-motion setting. Always
            disables smooth pan easing
            {motionProps.reducedMotion ? ' (active)' : ''}.
          </p>
          {/* Exposed for tests: flag contract for TimelineCanvas props */}
          <span
            data-testid="timeline-reduced-motion"
            data-reduced-motion={motionProps.reducedMotion ? 'true' : 'false'}
            hidden
          />
        </fieldset>
      </Section>

      <Section title="AI and models">
        {doc ? (
          <>
            <p
              className="mb-3 text-[12px] text-text-muted"
              data-testid="model-route-display"
            >
              Local route: ollama · {doc.ai.answer_model} — {doc.ai.retention_note}
            </p>
            <p className="mb-3 text-[11px] text-text-muted">
              External providers are not configured in this deployment. Only the
              local Ollama route is available.
            </p>
            <div className="flex flex-col gap-2">
              {(
                [
                  ['ask_enabled', 'Ask'],
                  ['interpret_enabled', 'Interpretation'],
                  ['generate_enabled', 'Event generation'],
                ] as const
              ).map(([key, label]) => (
                <label
                  key={key}
                  className="flex items-center gap-2 text-[13px] text-text-primary"
                >
                  <input
                    type="checkbox"
                    checked={doc.ai[key]}
                    onChange={(e) =>
                      void patchServer(
                        { ai: { [key]: e.target.checked } },
                        label,
                      )
                    }
                    data-testid={`ai-toggle-${key}`}
                  />
                  {label}
                </label>
              ))}
            </div>
          </>
        ) : (
          <p className="text-text-muted">Loading…</p>
        )}
      </Section>

      <Section title="Search">
        <label className="flex flex-col gap-1 text-[13px]">
          <span className="text-text-muted">Default retrieval mode</span>
          <select
            className="rounded-md border border-steel bg-graphite-800 px-2 py-1.5 text-text-primary"
            value={doc?.search.default_mode ?? 'hybrid'}
            disabled={!doc}
            aria-label="Default retrieval mode"
            onChange={(e) =>
              void patchServer(
                { search: { default_mode: e.target.value as SearchMode } },
                'Search mode',
              )
            }
          >
            {SEARCH_MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      </Section>

      <Section title="Chronicle">
        <fieldset>
          <legend className="mb-2 text-[12px] text-text-muted">Default lanes</legend>
          <div className="flex flex-col gap-1.5">
            {LANE_CATALOG.map((spec) => (
              <label
                key={spec.key}
                className="flex items-center gap-2 text-[13px] text-text-primary"
              >
                <input
                  type="checkbox"
                  checked={lanes.includes(spec.key)}
                  onChange={() => onLaneToggle(spec.key)}
                  data-testid={`lane-default-${spec.key}`}
                />
                {spec.label}
              </label>
            ))}
          </div>
        </fieldset>
      </Section>

      <Section title="Privacy">
        <label className="flex flex-col gap-1 text-[13px]">
          <span className="text-text-muted">Session timeout (seconds)</span>
          <input
            type="number"
            min={900}
            max={86400}
            step={60}
            className="w-40 rounded-md border border-steel bg-graphite-800 px-2 py-1.5 text-text-primary"
            value={doc?.privacy.session_max_age_s ?? 43200}
            disabled={!doc}
            aria-label="Session timeout in seconds"
            onChange={(e) => {
              const n = Number(e.target.value)
              if (!Number.isFinite(n)) return
              setDoc((prev) =>
                prev
                  ? {
                      ...prev,
                      privacy: { ...prev.privacy, session_max_age_s: n },
                    }
                  : prev,
              )
            }}
            onBlur={(e) => {
              const n = Number(e.target.value)
              if (!Number.isFinite(n)) return
              const clamped = Math.min(86400, Math.max(900, Math.round(n)))
              void patchServer(
                { privacy: { session_max_age_s: clamped } },
                'Session timeout',
              )
            }}
          />
        </label>
        <p className="mt-3 text-[13px]">
          <Link
            to="/data-health"
            className="text-action underline-offset-2 hover:underline"
          >
            Audit trail
          </Link>
          <span className="text-text-muted"> — view recent audit events in Data Health</span>
        </p>
      </Section>

      <Section title="Keyboard">
        <p className="mb-2 text-[11px] text-text-muted">
          Live shortcut map (same registry as the ? overlay). Read-only.
        </p>
        <ShortcutList />
      </Section>
    </div>
  )
}
