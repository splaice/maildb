/**
 * Pure SSE frame parser for POST /api/ask streams.
 * Handles chunk-split frames and multi-event buffers (no deps).
 */

export interface SseFrame {
  event: string
  data: string
}

/**
 * Incremental SSE parser. Feed arbitrary chunk strings; returns complete frames.
 * Remaining partial data is kept until the next call or {@link SseParser.flush}.
 */
export class SseParser {
  private buffer = ''

  push(chunk: string): SseFrame[] {
    this.buffer += chunk
    return this._drain(false)
  }

  /** Emit any final event if the stream ended without a trailing blank line. */
  flush(): SseFrame[] {
    return this._drain(true)
  }

  private _drain(flush: boolean): SseFrame[] {
    const frames: SseFrame[] = []
    // Normalize CRLF → LF
    this.buffer = this.buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')

    let sep: number
    while ((sep = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, sep)
      this.buffer = this.buffer.slice(sep + 2)
      const frame = parseBlock(block)
      if (frame) frames.push(frame)
    }

    if (flush && this.buffer.trim()) {
      const frame = parseBlock(this.buffer)
      this.buffer = ''
      if (frame) frames.push(frame)
    }

    return frames
  }
}

function parseBlock(block: string): SseFrame | null {
  if (!block.trim()) return null
  let event = 'message'
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      // Preserve leading space after "data:" per SSE (one optional space stripped)
      let v = line.slice('data:'.length)
      if (v.startsWith(' ')) v = v.slice(1)
      dataLines.push(v)
    }
    // ignore id:, retry:, comments
  }
  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

/**
 * Parse a complete SSE body string into frames (convenience for tests).
 */
export function parseSseBody(body: string): SseFrame[] {
  const p = new SseParser()
  const frames = p.push(body)
  frames.push(...p.flush())
  return frames
}

export interface AskRetrievalEvent {
  count: number
  types: { message?: number; attachment?: number; [k: string]: number | undefined }
  degraded: Record<string, string> | null
}

export interface AskTokenEvent {
  text: string
}

export interface AskCitationEvent {
  marker: string
  source_id: string
  source_type: string
  excerpt: string
  location: { char_start?: number; char_end?: number; [k: string]: unknown } | null
}

export interface AskDoneEvent {
  answer_id: string
  model_route: string
  policy_version: string
  generated_at: string
  unmatched_markers: string[]
}

export interface AskErrorEvent {
  message: string
}

export interface AskUnavailable {
  available: false
  reason: string
}

export type AskStreamHandlers = {
  onRetrieval?: (e: AskRetrievalEvent) => void
  onToken?: (e: AskTokenEvent) => void
  onCitation?: (e: AskCitationEvent) => void
  onDone?: (e: AskDoneEvent) => void
  onError?: (e: AskErrorEvent) => void
}

/**
 * Stream POST /api/ask via fetch + ReadableStream. Calls handlers for each event.
 * Resolves when the stream ends. Throws on HTTP errors (except 200 JSON unavailable).
 * Returns AskUnavailable when the server responds with the non-SSE unavailable payload.
 */
export async function streamAsk(
  body: { question: string; scope?: unknown; mode?: 'scope' },
  handlers: AskStreamHandlers,
  signal?: AbortSignal,
): Promise<AskUnavailable | void> {
  const response = await fetch('/api/ask', {
    method: 'POST',
    credentials: 'include',
    headers: {
      Accept: 'text/event-stream, application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      question: body.question,
      scope: body.scope ?? {},
      mode: body.mode ?? 'scope',
    }),
    signal,
  })

  if (response.status === 401) {
    throw new Error('Unauthorized')
  }

  const ct = response.headers.get('content-type') || ''
  if (ct.includes('application/json')) {
    const json = (await response.json()) as AskUnavailable
    if (json && json.available === false) {
      return json
    }
    throw new Error(`Unexpected JSON response from /api/ask`)
  }

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }

  if (!response.body) {
    throw new Error('No response body')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()

  const dispatch = (frames: SseFrame[]) => {
    for (const frame of frames) {
      let data: unknown
      try {
        data = JSON.parse(frame.data)
      } catch {
        continue
      }
      switch (frame.event) {
        case 'retrieval':
          handlers.onRetrieval?.(data as AskRetrievalEvent)
          break
        case 'token':
          handlers.onToken?.(data as AskTokenEvent)
          break
        case 'citation':
          handlers.onCitation?.(data as AskCitationEvent)
          break
        case 'done':
          handlers.onDone?.(data as AskDoneEvent)
          break
        case 'error':
          handlers.onError?.(data as AskErrorEvent)
          break
      }
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value, { stream: true })
      dispatch(parser.push(chunk))
    }
    dispatch(parser.push(decoder.decode()))
    dispatch(parser.flush())
  } finally {
    reader.releaseLock()
  }
}
