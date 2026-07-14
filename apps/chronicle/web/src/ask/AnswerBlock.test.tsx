import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnswerBlock } from './AnswerBlock'

function sseBody(frames: { event: string; data: unknown }[]): string {
  return frames
    .map((f) => `event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`)
    .join('')
}

function streamResponse(body: string, contentType = 'text/event-stream'): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(body))
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': contentType },
  })
}

describe('AnswerBlock', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('streams tokens into text and renders citation chips', async () => {
    const body = sseBody([
      {
        event: 'retrieval',
        data: { count: 12, types: { message: 9, attachment: 3 }, degraded: null },
      },
      { event: 'token', data: { text: 'Metal roof ' } },
      { event: 'token', data: { text: 'chosen [S1].' } },
      {
        event: 'citation',
        data: {
          marker: '[S1]',
          source_id: 'msg_99',
          source_type: 'message',
          excerpt: 'We chose metal…',
          location: { char_start: 0, char_end: 15 },
        },
      },
      {
        event: 'done',
        data: {
          answer_id: 'ans_1',
          model_route: 'ollama:llama3.2',
          policy_version: 'ask-v1',
          generated_at: '2026-07-13T12:00:00Z',
          unmatched_markers: [],
        },
      },
    ])
    vi.mocked(fetch).mockResolvedValue(streamResponse(body))

    const onSelect = vi.fn()
    render(
      <AnswerBlock
        question="What roof?"
        scope={{}}
        runId={1}
        onSelectSource={onSelect}
      />,
    )

    expect(await screen.findByTestId('ask-retrieval-status')).toHaveTextContent(
      /12 sources retrieved/,
    )
    expect(screen.getByTestId('ask-retrieval-status')).toHaveTextContent(
      /9 messages, 3 attachment/,
    )

    await waitFor(() => {
      expect(screen.getByTestId('ask-answer-text')).toHaveTextContent(/Metal roof chosen/)
    })

    const chip = await screen.findByTestId('citation-chip-S1')
    fireEvent.click(chip)
    expect(onSelect).toHaveBeenCalledWith('msg_99', 'message')
    expect(screen.getByTestId('ask-citation-excerpt')).toHaveTextContent(/We chose metal/)

    expect(screen.getByTestId('ask-model-route')).toHaveTextContent('ollama:llama3.2')
    expect(screen.getByTestId('ask-policy-version')).toHaveTextContent('ask-v1')
  })

  it('shows model-unavailable panel for JSON unavailable response', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ available: false, reason: 'Model service unavailable' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    render(
      <AnswerBlock question="Anything?" scope={{}} runId={1} onSelectSource={() => {}} />,
    )

    expect(await screen.findByTestId('ask-unavailable')).toHaveTextContent(
      /Model service unavailable — search remains available/,
    )
  })

  it('cancel aborts the in-flight stream', async () => {
    let aborted = false
    vi.mocked(fetch).mockImplementation((_url, init) => {
      const signal = init?.signal
      return new Promise<Response>((_resolve, reject) => {
        if (signal?.aborted) {
          aborted = true
          reject(new DOMException('Aborted', 'AbortError'))
          return
        }
        signal?.addEventListener('abort', () => {
          aborted = true
          reject(new DOMException('Aborted', 'AbortError'))
        })
        // never resolve until abort
      })
    })

    render(
      <AnswerBlock question="slow?" scope={{}} runId={1} onSelectSource={() => {}} />,
    )

    const cancel = await screen.findByTestId('ask-cancel')
    fireEvent.click(cancel)

    await waitFor(() => {
      expect(aborted).toBe(true)
    })
  })
})
