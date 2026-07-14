import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { specsForKeys } from './laneModel'
import { TimelineTable } from './TimelineTable'

describe('TimelineTable', () => {
  it('renders bucket rows from mock data with caption/th scope', () => {
    const viewport = {
      fromMs: Date.UTC(2014, 0, 1),
      toMs: Date.UTC(2016, 0, 1),
    }
    render(
      <TimelineTable
        viewport={viewport}
        unit="month"
        lanes={specsForKeys(['messages', 'attachments'])}
        laneData={{
          messages: [
            { bucket: '2014-01-01T00:00:00.000Z', count: 12 },
            { bucket: '2014-02-01T00:00:00.000Z', count: 8 },
          ],
          attachments: [
            { bucket: '2014-01-01T00:00:00.000Z', count: 3 },
            { bucket: '2014-03-01T00:00:00.000Z', count: 1 },
          ],
        }}
      />,
    )

    const table = screen.getByTestId('timeline-table')
    expect(table.querySelector('caption')).toBeTruthy()
    expect(table.querySelector('caption')?.textContent).toMatch(/month/)

    const colHeaders = table.querySelectorAll('thead th[scope="col"]')
    expect(colHeaders.length).toBe(3)

    const rowHeaders = table.querySelectorAll('tbody th[scope="row"]')
    expect(rowHeaders.length).toBe(3) // jan, feb, mar merged

    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('includes per-contact columns for top_people', () => {
    const viewport = {
      fromMs: Date.UTC(2014, 0, 1),
      toMs: Date.UTC(2016, 0, 1),
    }
    render(
      <TimelineTable
        viewport={viewport}
        unit="month"
        lanes={specsForKeys(['messages', 'top_people'])}
        laneData={{
          messages: [{ bucket: '2014-01-01T00:00:00.000Z', count: 5 }],
          top_people: {
            contacts: [
              {
                contact_id: 'c1',
                display_name: 'Alice',
                buckets: [{ bucket: '2014-01-01T00:00:00.000Z', count: 2 }],
              },
              {
                contact_id: 'c2',
                display_name: 'Bob',
                buckets: [{ bucket: '2014-01-01T00:00:00.000Z', count: 4 }],
              },
            ],
          },
        }}
      />,
    )

    const table = screen.getByTestId('timeline-table')
    expect(table.querySelector('[data-contact-col="c1"]')?.textContent).toBe('Alice')
    expect(table.querySelector('[data-contact-col="c2"]')?.textContent).toBe('Bob')
    expect(table.querySelector('[data-contact-cell="c1"]')?.textContent).toBe('2')
    expect(table.querySelector('[data-contact-cell="c2"]')?.textContent).toBe('4')
    expect(screen.getByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
  })

  it('includes per-topic columns for topics lane', () => {
    const viewport = {
      fromMs: Date.UTC(2014, 0, 1),
      toMs: Date.UTC(2016, 0, 1),
    }
    render(
      <TimelineTable
        viewport={viewport}
        unit="month"
        lanes={specsForKeys(['messages', 'topics'])}
        laneData={{
          messages: [{ bucket: '2014-01-01T00:00:00.000Z', count: 5 }],
          topics: {
            topics: [
              {
                topic_id: 't1',
                label: 'House',
                origin: 'automatic',
                buckets: [{ bucket: '2014-01-01T00:00:00.000Z', count: 3 }],
              },
              {
                topic_id: 't2',
                label: 'Travel',
                origin: 'curated',
                buckets: [{ bucket: '2014-01-01T00:00:00.000Z', count: 7 }],
              },
            ],
          },
        }}
      />,
    )

    const table = screen.getByTestId('timeline-table')
    expect(table.querySelector('[data-topic-col="t1"]')?.textContent).toBe('House')
    expect(table.querySelector('[data-topic-col="t2"]')?.textContent).toBe('Travel')
    expect(table.querySelector('[data-topic-cell="t1"]')?.textContent).toBe('3')
    expect(table.querySelector('[data-topic-cell="t2"]')?.textContent).toBe('7')
  })
})
