import { describe, expect, it } from 'vitest'

import type { SearchResult } from '../api/types'
import { groupResults } from './grouping'

const mockWindow: SearchResult[] = [
  {
    result_type: 'message',
    id: 'msg_1',
    subject: 'Thread A first',
    sender: 'a@x.com',
    date: '2014-03-01T00:00:00Z',
    mailbox: 'personal@x.com',
    thread_id: 'thr_A',
    snippet: 'one',
    has_attachment: false,
    match: { kind: 'exact', field: 'body' },
  },
  {
    result_type: 'message',
    id: 'msg_2',
    subject: 'Thread A second',
    sender: 'b@x.com',
    date: '2015-06-01T00:00:00Z',
    mailbox: 'work@x.com',
    thread_id: 'thr_A',
    snippet: 'two',
    has_attachment: true,
    match: { kind: 'exact', field: 'body' },
  },
  {
    result_type: 'message',
    id: 'msg_3',
    subject: 'Other thread',
    sender: 'c@x.com',
    date: '2014-12-01T00:00:00Z',
    mailbox: 'personal@x.com',
    thread_id: 'thr_B',
    snippet: 'three',
    has_attachment: false,
    match: { kind: 'exact', field: 'body' },
  },
  {
    result_type: 'attachment',
    id: 'att_1',
    filename: 'doc.pdf',
    content_type: 'application/pdf',
    source_message_id: 'msg_1',
    sender: 'a@x.com',
    date: '2016-01-01T00:00:00Z',
    snippet: 'pdf text',
    extraction_status: 'extracted',
    match: { kind: 'semantic', similarity: 0.9 },
  },
]

describe('groupResults', () => {
  it('groups by thread using first subject and counts', () => {
    const groups = groupResults(mockWindow, 'thread')
    const thrA = groups.find((g) => g.key === 'thr_A')
    expect(thrA).toBeDefined()
    expect(thrA!.label).toBe('Thread A first')
    expect(thrA!.items).toHaveLength(2)
    const thrB = groups.find((g) => g.key === 'thr_B')
    expect(thrB!.items).toHaveLength(1)
    const noThread = groups.find((g) => g.key === '__no_thread__')
    expect(noThread!.items).toHaveLength(1)
    expect(noThread!.items[0]!.id).toBe('att_1')
  })

  it('groups by year from date', () => {
    const groups = groupResults(mockWindow, 'year')
    const y2014 = groups.find((g) => g.key === '2014')
    expect(y2014!.items).toHaveLength(2)
    const y2015 = groups.find((g) => g.key === '2015')
    expect(y2015!.items).toHaveLength(1)
    const y2016 = groups.find((g) => g.key === '2016')
    expect(y2016!.items).toHaveLength(1)
  })

  it('groups by mailbox', () => {
    const groups = groupResults(mockWindow, 'mailbox')
    const personal = groups.find((g) => g.key === 'personal@x.com')
    expect(personal!.items).toHaveLength(2)
    const work = groups.find((g) => g.key === 'work@x.com')
    expect(work!.items).toHaveLength(1)
    const unknown = groups.find((g) => g.key === 'Unknown mailbox')
    expect(unknown!.items).toHaveLength(1)
  })
})
