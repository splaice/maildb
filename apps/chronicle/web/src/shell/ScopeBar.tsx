import { useMemo, useState } from 'react'

import { useArchiveSummary } from '../routes/useArchiveSummary'
import { ScopeChipList, useScopeChips } from '../workingset/ScopeChips'
import { useWorkingSetStore } from '../workingset/store'
import { isScopePristine } from '../workingset/urlState'
import { useUrlSync } from '../workingset/useUrlSync'

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

export function ScopeBar() {
  // URL contract lives on the shell-mounted scope bar so working-set state
  // hydrates once and persists across lens route changes.
  useUrlSync()

  const chips = useScopeChips()
  const scope = useWorkingSetStore((s) => s.scope)
  const resultCount = useWorkingSetStore((s) => s.resultCount)
  const setScopeDate = useWorkingSetStore((s) => s.setScopeDate)
  const addMailbox = useWorkingSetStore((s) => s.addMailbox)
  const clearScope = useWorkingSetStore((s) => s.clearScope)

  const archive = useArchiveSummary()
  const accounts = archive.data?.accounts ?? []

  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [showDateEditor, setShowDateEditor] = useState(false)
  const [showMailboxPicker, setShowMailboxPicker] = useState(false)

  const pristine = useMemo(() => isScopePristine(scope), [scope])

  const applyDateRange = () => {
    if (!dateFrom && !dateTo) {
      setScopeDate(null)
    } else {
      setScopeDate({
        ...(dateFrom ? { from: dateFrom } : {}),
        ...(dateTo ? { to: dateTo } : {}),
      })
    }
    setShowDateEditor(false)
  }

  const framing =
    resultCount != null
      ? `${resultCount.toLocaleString()} messages in scope`
      : null

  return (
    <div
      className="col-span-3 flex items-center gap-2 border-b border-steel bg-graphite-900 px-3"
      style={{ height: 44 }}
      role="region"
      aria-label="Working set scope"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
        <ScopeChipList chips={chips} />

        <div className="flex items-center gap-1">
          <button
            type="button"
            className={btnClass}
            aria-expanded={showDateEditor}
            aria-label="Add date filter"
            onClick={() => {
              setShowDateEditor((v) => !v)
              setShowMailboxPicker(false)
              setDateFrom(scope.date?.from ?? '')
              setDateTo(scope.date?.to ?? '')
            }}
          >
            Date
          </button>
          <button
            type="button"
            className={btnClass}
            aria-expanded={showMailboxPicker}
            aria-label="Add mailbox filter"
            onClick={() => {
              setShowMailboxPicker((v) => !v)
              setShowDateEditor(false)
            }}
          >
            Mailbox
          </button>
        </div>

        {showDateEditor ? (
          <div
            className="flex items-center gap-1 rounded-md border border-steel bg-graphite-800 px-2 py-1"
            data-testid="date-range-editor"
          >
            <label className="sr-only" htmlFor="scope-date-from">
              Date from
            </label>
            <input
              id="scope-date-from"
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="rounded border border-steel bg-graphite-900 px-1 text-text-primary"
            />
            <span className="text-text-muted">–</span>
            <label className="sr-only" htmlFor="scope-date-to">
              Date to
            </label>
            <input
              id="scope-date-to"
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="rounded border border-steel bg-graphite-900 px-1 text-text-primary"
            />
            <button type="button" className={btnClass} onClick={applyDateRange}>
              Apply
            </button>
          </div>
        ) : null}

        {showMailboxPicker ? (
          <div
            className="flex items-center gap-1 rounded-md border border-steel bg-graphite-800 px-2 py-1"
            data-testid="mailbox-picker"
          >
            <label className="sr-only" htmlFor="scope-mailbox">
              Mailbox
            </label>
            <select
              id="scope-mailbox"
              className="max-w-[12rem] rounded border border-steel bg-graphite-900 px-1 text-text-primary"
              defaultValue=""
              onChange={(e) => {
                const v = e.target.value
                if (v) {
                  addMailbox(v)
                  e.target.value = ''
                  setShowMailboxPicker(false)
                }
              }}
            >
              <option value="" disabled>
                Select mailbox…
              </option>
              {accounts.map((a) => (
                <option key={a.account} value={a.account}>
                  {a.account}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {framing ? (
          <span
            className="tabular-nums text-text-muted"
            data-testid="scope-result-count"
          >
            {framing}
          </span>
        ) : null}
        <button
          type="button"
          className={btnClass}
          disabled={pristine}
          onClick={() => clearScope()}
        >
          Reset scope
        </button>
      </div>
    </div>
  )
}
