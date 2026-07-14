/**
 * Grounded answer block for Research Desk Ask mode (spec §5.5).
 * Streams retrieval → tokens → citations; model-unavailable panel (LC-010).
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import type { QueryScope, SearchResult } from '../api/types'
import { ResultCard } from '../research/ResultCard'
import { createBlock, createWorkspace, listWorkspaces } from '../workspaces/api'
import { renderAnswerWithCitations } from './citationText'
import {
  streamAsk,
  type AskCitationEvent,
  type AskDoneEvent,
  type AskRetrievalEvent,
} from './sseClient'

const btnClass =
  'rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary enabled:hover:bg-graphite-900 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action'

export interface AnswerBlockProps {
  question: string
  scope: QueryScope
  /** Bump to re-run ask for the same question */
  runId: number
  onSelectSource: (sourceId: string, sourceType: string) => void
  /** Called when stream finishes (success or fail) so parent can clear busy */
  onFinished?: () => void
}

function retrievalStatusLine(r: AskRetrievalEvent): string {
  const msg = r.types.message ?? 0
  const att = r.types.attachment ?? 0
  const parts: string[] = []
  if (msg > 0) parts.push(`${msg} message${msg === 1 ? '' : 's'}`)
  if (att > 0) parts.push(`${att} attachment passage${att === 1 ? '' : 's'}`)
  const detail = parts.length > 0 ? parts.join(', ') : '0 sources'
  return `${r.count} source${r.count === 1 ? '' : 's'} retrieved · ${detail}`
}

function toResultCards(rows: RetrievalRow[]): SearchResult[] {
  return rows.map((row) => {
    if (row.source_type === 'attachment') {
      return {
        result_type: 'attachment' as const,
        id: row.source_id,
        filename: row.title || row.source_id,
        content_type: null,
        source_message_id: null,
        sender: row.sender,
        date: row.date,
        snippet: row.snippet || '',
        extraction_status: null,
        match: { kind: 'hybrid' as const },
      }
    }
    return {
      result_type: 'message' as const,
      id: row.source_id,
      subject: row.title,
      sender: row.sender,
      sender_name: row.sender,
      date: row.date,
      mailbox: null,
      thread_id: null,
      snippet: row.snippet || '',
      has_attachment: false,
      match: { kind: 'hybrid' as const },
    }
  })
}

interface RetrievalRow {
  source_id: string
  source_type: string
  title: string | null
  date: string | null
  sender: string | null
  snippet: string
}

export function AnswerBlock({
  question,
  scope,
  runId,
  onSelectSource,
  onFinished,
}: AnswerBlockProps) {
  const [text, setText] = useState('')
  const [retrieval, setRetrieval] = useState<AskRetrievalEvent | null>(null)
  const [citations, setCitations] = useState<AskCitationEvent[]>([])
  const [done, setDone] = useState<AskDoneEvent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [showRetrieval, setShowRetrieval] = useState(false)
  const [activeExcerpt, setActiveExcerpt] = useState<AskCitationEvent | null>(null)
  const [retrievalRows, setRetrievalRows] = useState<RetrievalRow[]>([])
  const [pinOpen, setPinOpen] = useState(false)
  const [pinBusy, setPinBusy] = useState(false)
  const [pinStatus, setPinStatus] = useState<string | null>(null)
  const [pinError, setPinError] = useState<string | null>(null)
  const [pinWorkspaces, setPinWorkspaces] = useState<
    { id: string; name: string }[]
  >([])
  const [pinNewName, setPinNewName] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
  }, [])

  useEffect(() => {
    if (!question.trim() || runId === 0) return

    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    setText('')
    setRetrieval(null)
    setCitations([])
    setDone(null)
    setError(null)
    setUnavailable(null)
    setActiveExcerpt(null)
    setRetrievalRows([])
    setShowRetrieval(false)
    setStreaming(true)

    void (async () => {
      try {
        const result = await streamAsk(
          { question, scope, mode: 'scope' },
          {
            onRetrieval: (e) => {
              setRetrieval(e)
            },
            onToken: (e) => {
              setText((prev) => prev + e.text)
            },
            onCitation: (e) => {
              setCitations((prev) => [...prev, e])
              setRetrievalRows((prev) => {
                if (prev.some((r) => r.source_id === e.source_id)) return prev
                return [
                  ...prev,
                  {
                    source_id: e.source_id,
                    source_type: e.source_type,
                    title: null,
                    date: null,
                    sender: null,
                    snippet: e.excerpt || '',
                  },
                ]
              })
            },
            onDone: (e) => {
              setDone(e)
            },
            onError: (e) => {
              setError(e.message)
            },
          },
          ac.signal,
        )
        if (ac.signal.aborted) return
        if (result && result.available === false) {
          setUnavailable(result.reason || 'Model service unavailable')
        }
      } catch (err) {
        if (ac.signal.aborted) return
        if (err instanceof DOMException && err.name === 'AbortError') return
        setError(err instanceof Error ? err.message : 'Ask failed')
      } finally {
        if (!ac.signal.aborted) {
          setStreaming(false)
          onFinished?.()
        }
      }
    })()

    return () => {
      ac.abort()
    }
  }, [question, scope, runId, onFinished])

  // When we get citations, ensure retrieval rows cover them for "Show retrieval set"
  useEffect(() => {
    if (citations.length === 0) return
    setRetrievalRows((prev) => {
      const ids = new Set(prev.map((r) => r.source_id))
      const extra: RetrievalRow[] = []
      for (const c of citations) {
        if (!ids.has(c.source_id)) {
          extra.push({
            source_id: c.source_id,
            source_type: c.source_type,
            title: null,
            date: null,
            sender: null,
            snippet: c.excerpt || '',
          })
        }
      }
      return extra.length ? [...prev, ...extra] : prev
    })
  }, [citations])

  const onCitationClick = (cit: AskCitationEvent) => {
    setActiveExcerpt(cit)
    onSelectSource(cit.source_id, cit.source_type)
  }

  const copyWithCitations = async () => {
    const legend = citations
      .map((c) => `${c.marker} ${c.source_id}`)
      .join('\n')
    const payload = legend ? `${text}\n\n—\n${legend}` : text
    try {
      await navigator.clipboard.writeText(payload)
    } catch {
      // ignore clipboard failures in tests / insecure contexts
    }
  }

  const openPinMenu = async () => {
    setPinOpen((v) => !v)
    setPinStatus(null)
    setPinError(null)
    if (!pinOpen) {
      try {
        const res = await listWorkspaces()
        setPinWorkspaces(res.items.map((w) => ({ id: w.id, name: w.name })))
      } catch (err) {
        setPinError(err instanceof Error ? err.message : 'Failed to load workspaces')
      }
    }
  }

  const pinAnswerTo = async (workspaceId: string) => {
    if (!done?.answer_id) return
    setPinBusy(true)
    setPinError(null)
    setPinStatus(null)
    try {
      await createBlock(workspaceId, {
        block_type: 'answer',
        content: { answer_id: done.answer_id },
      })
      setPinStatus('Pinned')
    } catch (err) {
      setPinError(err instanceof Error ? err.message : 'Pin failed')
    } finally {
      setPinBusy(false)
    }
  }

  const createWorkspaceAndPinAnswer = async () => {
    const name = pinNewName.trim()
    if (!name || !done?.answer_id) return
    setPinBusy(true)
    setPinError(null)
    try {
      const ws = await createWorkspace({ name, scope })
      await pinAnswerTo(ws.id)
      setPinNewName('')
      const res = await listWorkspaces()
      setPinWorkspaces(res.items.map((w) => ({ id: w.id, name: w.name })))
    } catch (err) {
      setPinError(err instanceof Error ? err.message : 'Create failed')
      setPinBusy(false)
    }
  }

  if (unavailable) {
    return (
      <div
        role="status"
        className="rounded-md border border-steel bg-graphite-900 px-3 py-3 text-sm text-text-muted"
        data-testid="ask-unavailable"
      >
        Model service unavailable — search remains available
        {unavailable !== 'Model service unavailable' ? (
          <span className="mt-1 block text-[11px]">({unavailable})</span>
        ) : null}
      </div>
    )
  }

  if (!question.trim() || runId === 0) {
    return null
  }

  const cards = toResultCards(retrievalRows)

  return (
    <section
      className="rounded-md border border-steel bg-graphite-900 p-3"
      data-testid="answer-block"
      aria-label="Grounded answer"
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-text-primary">Answer</h2>
        <div className="flex items-center gap-2">
          {streaming ? (
            <button
              type="button"
              className={btnClass}
              onClick={cancel}
              data-testid="ask-cancel"
            >
              Cancel
            </button>
          ) : null}
        </div>
      </div>

      {retrieval ? (
        <p
          className="mb-2 text-[11px] text-text-muted"
          data-testid="ask-retrieval-status"
        >
          {retrievalStatusLine(retrieval)}
          {retrieval.degraded ? (
            <span className="ml-2 text-conflict">
              degraded: {Object.keys(retrieval.degraded).join(', ')}
            </span>
          ) : null}
        </p>
      ) : streaming ? (
        <p className="mb-2 text-[11px] text-text-muted" data-testid="ask-retrieving">
          Retrieving sources…
        </p>
      ) : null}

      {error ? (
        <div
          role="alert"
          className="mb-2 rounded border border-conflict bg-graphite-950 px-2 py-1 text-sm text-conflict"
          data-testid="ask-error"
        >
          {error}
        </div>
      ) : null}

      <div
        className="min-h-[2rem] whitespace-pre-wrap text-sm text-text-primary"
        data-testid="ask-answer-text"
        aria-live="polite"
      >
        {text
          ? renderAnswerWithCitations(text, citations, onCitationClick)
          : streaming
            ? '…'
            : null}
      </div>

      {activeExcerpt ? (
        <div
          className="mt-2 rounded border border-steel bg-graphite-950 px-2 py-1 text-[12px] text-text-muted"
          data-testid="ask-citation-excerpt"
        >
          <span className="font-mono text-action">{activeExcerpt.marker}</span>{' '}
          {activeExcerpt.excerpt}
        </div>
      ) : null}

      {done || (!streaming && text) ? (
        <footer
          className="mt-3 flex flex-wrap items-center gap-2 border-t border-steel pt-2 text-[11px] text-text-muted"
          data-testid="ask-footer"
        >
          {done ? (
            <>
              <span data-testid="ask-model-route">{done.model_route}</span>
              <span aria-hidden>·</span>
              <span data-testid="ask-policy-version">{done.policy_version}</span>
              <span aria-hidden>·</span>
              <time data-testid="ask-generated-at" dateTime={done.generated_at}>
                {done.generated_at}
              </time>
              {done.unmatched_markers?.length ? (
                <span data-testid="ask-unmatched" className="text-conflict">
                  unmatched: {done.unmatched_markers.join(', ')}
                </span>
              ) : null}
            </>
          ) : null}
          <button
            type="button"
            className={btnClass}
            onClick={() => setShowRetrieval((v) => !v)}
            data-testid="ask-show-retrieval"
          >
            {showRetrieval ? 'Hide retrieval set' : 'Show retrieval set'}
          </button>
          <button
            type="button"
            className={btnClass}
            onClick={() => void copyWithCitations()}
            data-testid="ask-copy"
          >
            Copy with citations
          </button>
          {done?.answer_id ? (
            <div className="relative" data-testid="pin-answer">
              <button
                type="button"
                className={btnClass}
                onClick={() => void openPinMenu()}
                data-testid="pin-answer-btn"
              >
                Pin answer
              </button>
              {pinOpen ? (
                <div
                  className="absolute bottom-full left-0 z-20 mb-1 min-w-[14rem] rounded-md border border-steel bg-graphite-900 p-2 shadow-lg"
                  data-testid="pin-answer-menu"
                  role="menu"
                >
                  {pinWorkspaces.length === 0 ? (
                    <p className="mb-1 text-[11px] text-text-muted">No workspaces yet</p>
                  ) : (
                    <ul className="mb-2 max-h-40 space-y-0.5 overflow-auto">
                      {pinWorkspaces.map((w) => (
                        <li key={w.id}>
                          <button
                            type="button"
                            className="w-full rounded px-1.5 py-1 text-left text-[12px] text-text-primary hover:bg-graphite-800"
                            disabled={pinBusy}
                            onClick={() => void pinAnswerTo(w.id)}
                            data-testid={`pin-answer-workspace-${w.id}`}
                            role="menuitem"
                          >
                            {w.name}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="flex gap-1 border-t border-steel pt-2">
                    <input
                      type="text"
                      value={pinNewName}
                      onChange={(e) => setPinNewName(e.target.value)}
                      placeholder="New workspace"
                      className="min-w-0 flex-1 rounded border border-steel bg-graphite-950 px-1.5 py-0.5 text-[12px] text-text-primary"
                      data-testid="pin-answer-new-name"
                      disabled={pinBusy}
                    />
                    <button
                      type="button"
                      className={btnClass}
                      disabled={pinBusy || !pinNewName.trim()}
                      onClick={() => void createWorkspaceAndPinAnswer()}
                      data-testid="pin-answer-create"
                    >
                      Create
                    </button>
                  </div>
                  {pinStatus ? (
                    <p className="mt-1 text-[11px] text-action" data-testid="pin-answer-status">
                      {pinStatus}
                    </p>
                  ) : null}
                  {pinError ? (
                    <p className="mt-1 text-[11px] text-conflict" role="alert">
                      {pinError}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
        </footer>
      ) : null}

      {showRetrieval ? (
        <div className="mt-2 space-y-2" data-testid="ask-retrieval-set">
          {cards.length === 0 ? (
            <p className="text-[11px] text-text-muted">
              {retrieval
                ? `${retrieval.count} source(s) in retrieval set`
                : 'No retrieval rows'}
            </p>
          ) : (
            cards.map((r) => (
              <ResultCard
                key={r.id}
                result={r}
                freeText=""
                selected={false}
                onSelect={() =>
                  onSelectSource(
                    r.id,
                    r.result_type === 'attachment' ? 'attachment' : 'message',
                  )
                }
              />
            ))
          )}
        </div>
      ) : null}
    </section>
  )
}
