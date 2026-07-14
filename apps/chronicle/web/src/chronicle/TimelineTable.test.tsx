import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

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
        messages={[
          { bucket: '2014-01-01T00:00:00.000Z', count: 12 },
          { bucket: '2014-02-01T00:00:00.000Z', count: 8 },
        ]}
        attachments={[
          { bucket: '2014-01-01T00:00:00.000Z', count: 3 },
          { bucket: '2014-03-01T00:00:00.000Z', count: 1 },
        ]}
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
})
