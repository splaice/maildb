/**
 * /workspaces — list case files + create / delete.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'

import type { QueryScope } from '../api/types'
import { useWorkingSetStore } from '../workingset/store'
import { createWorkspace, deleteWorkspace, listWorkspaces } from './api'

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

function formatUpdated(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = Date.parse(iso)
  if (!Number.isFinite(d)) return iso
  return new Date(d).toLocaleString()
}

export function WorkspacesListPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const scope = useWorkingSetStore((s) => s.scope)
  const [name, setName] = useState('')
  const [useCurrentScope, setUseCurrentScope] = useState(true)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['workspaces'],
    queryFn: ({ signal }) => listWorkspaces(signal),
    retry: false,
  })

  const createMut = useMutation({
    mutationFn: (body: { name: string; scope: QueryScope }) => createWorkspace(body),
    onSuccess: (ws) => {
      void qc.invalidateQueries({ queryKey: ['workspaces'] })
      setName('')
      void navigate(`/workspaces/${ws.id}`)
    },
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteWorkspace(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['workspaces'] })
      setConfirmId(null)
    },
  })

  const items = listQuery.data?.items ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3" data-testid="workspaces-list-page">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-base font-medium text-text-primary">Workspaces</h1>
      </header>

      <section
        className="rounded-md border border-steel bg-graphite-900 p-3"
        data-testid="new-workspace-form"
      >
        <h2 className="mb-2 text-sm font-medium text-text-primary">New workspace</h2>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex min-w-[12rem] flex-1 flex-col gap-0.5 text-[11px] text-text-muted">
            Name
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="rounded border border-steel bg-graphite-950 px-2 py-1 text-sm text-text-primary"
              data-testid="new-workspace-name"
              placeholder="Case file name"
            />
          </label>
          <label className="flex items-center gap-1.5 text-[12px] text-text-primary">
            <input
              type="checkbox"
              checked={useCurrentScope}
              onChange={(e) => setUseCurrentScope(e.target.checked)}
              data-testid="use-current-scope"
            />
            Use current scope
          </label>
          <button
            type="button"
            className={btnClass}
            disabled={!name.trim() || createMut.isPending}
            data-testid="create-workspace"
            onClick={() => {
              const n = name.trim()
              if (!n) return
              createMut.mutate({
                name: n,
                scope: useCurrentScope ? scope : {},
              })
            }}
          >
            Create
          </button>
        </div>
        {createMut.isError ? (
          <p className="mt-2 text-[11px] text-conflict" role="alert">
            {createMut.error instanceof Error
              ? createMut.error.message
              : 'Create failed'}
          </p>
        ) : null}
      </section>

      {listQuery.isLoading ? (
        <p className="text-text-muted" data-testid="workspaces-loading">
          Loading…
        </p>
      ) : listQuery.isError ? (
        <p className="text-conflict" role="alert">
          Failed to load workspaces
        </p>
      ) : items.length === 0 ? (
        <p className="text-text-muted" data-testid="workspaces-empty">
          No workspaces yet
        </p>
      ) : (
        <ul className="space-y-1" data-testid="workspaces-list">
          {items.map((w) => (
            <li
              key={w.id}
              className="flex flex-wrap items-center gap-2 rounded border border-steel bg-graphite-900 px-3 py-2"
              data-testid={`workspace-row-${w.id}`}
            >
              <Link
                to={`/workspaces/${w.id}`}
                className="min-w-0 flex-1 text-sm font-medium text-action hover:underline"
              >
                {w.name}
              </Link>
              <span className="tabular-nums text-[11px] text-text-muted">
                {w.counts.blocks} blocks · {w.counts.pins} pins · {w.counts.notes}{' '}
                notes · {w.counts.answers} answers
              </span>
              <time
                className="tabular-nums text-[11px] text-text-muted"
                dateTime={w.updated_at ?? undefined}
              >
                {formatUpdated(w.updated_at)}
              </time>
              {confirmId === w.id ? (
                <span className="flex items-center gap-1" data-testid="delete-confirm">
                  <span className="text-[11px] text-conflict">Delete?</span>
                  <button
                    type="button"
                    className={btnClass}
                    data-testid="delete-confirm-yes"
                    disabled={deleteMut.isPending}
                    onClick={() => deleteMut.mutate(w.id)}
                  >
                    Yes
                  </button>
                  <button
                    type="button"
                    className={btnClass}
                    data-testid="delete-confirm-no"
                    onClick={() => setConfirmId(null)}
                  >
                    No
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  className={btnClass}
                  data-testid={`delete-workspace-${w.id}`}
                  onClick={() => setConfirmId(w.id)}
                >
                  Delete
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
