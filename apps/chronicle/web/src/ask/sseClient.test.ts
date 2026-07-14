import { describe, expect, it } from 'vitest'

import { SseParser, parseSseBody } from './sseClient'

describe('SseParser', () => {
  it('parses complete single-frame body', () => {
    const body =
      'event: retrieval\ndata: {"count":2,"types":{"message":2},"degraded":null}\n\n'
    const frames = parseSseBody(body)
    expect(frames).toHaveLength(1)
    expect(frames[0]!.event).toBe('retrieval')
    expect(JSON.parse(frames[0]!.data)).toEqual({
      count: 2,
      types: { message: 2 },
      degraded: null,
    })
  })

  it('handles multi-event buffer in one push', () => {
    const body = [
      'event: token\ndata: {"text":"Hello"}\n\n',
      'event: token\ndata: {"text":" world"}\n\n',
      'event: done\ndata: {"answer_id":"a1"}\n\n',
    ].join('')
    const frames = parseSseBody(body)
    expect(frames.map((f) => f.event)).toEqual(['token', 'token', 'done'])
    expect(JSON.parse(frames[0]!.data).text).toBe('Hello')
    expect(JSON.parse(frames[1]!.data).text).toBe(' world')
  })

  it('handles chunk-split frames across pushes', () => {
    const p = new SseParser()
    const a = p.push('event: tok')
    expect(a).toEqual([])
    const b = p.push('en\ndata: {"te')
    expect(b).toEqual([])
    const c = p.push('xt":"ab"}\n\nevent: token\ndata: {"text":"c"}\n\n')
    expect(c).toHaveLength(2)
    expect(c[0]!.event).toBe('token')
    expect(JSON.parse(c[0]!.data).text).toBe('ab')
    expect(JSON.parse(c[1]!.data).text).toBe('c')
  })

  it('handles split between events and multi-line data', () => {
    const p = new SseParser()
    expect(p.push('event: citation\n')).toEqual([])
    expect(p.push('data: {"marker":"[S1]",')).toEqual([])
    const frames = p.push('"source_id":"msg_1"}\n\n')
    expect(frames).toHaveLength(1)
    expect(frames[0]!.event).toBe('citation')
    expect(JSON.parse(frames[0]!.data).source_id).toBe('msg_1')
  })

  it('flush emits trailing frame without blank line terminator', () => {
    const p = new SseParser()
    p.push('event: error\ndata: {"message":"fail"}')
    const frames = p.flush()
    expect(frames).toHaveLength(1)
    expect(frames[0]!.event).toBe('error')
  })

  it('normalizes CRLF line endings', () => {
    const body = 'event: token\r\ndata: {"text":"x"}\r\n\r\n'
    const frames = parseSseBody(body)
    expect(frames).toHaveLength(1)
    expect(JSON.parse(frames[0]!.data).text).toBe('x')
  })
})
