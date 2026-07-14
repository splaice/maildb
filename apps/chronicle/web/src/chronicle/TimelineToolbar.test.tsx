import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { TimelineToolbar } from './TimelineToolbar'

const viewport = {
  fromMs: Date.UTC(2014, 0, 1),
  toMs: Date.UTC(2019, 0, 1),
}

describe('TimelineToolbar', () => {
  it('buttons dispatch the right viewport transitions', () => {
    const onZoomIn = vi.fn()
    const onZoomOut = vi.fn()
    const onFitAll = vi.fn()
    const onZoomToSelection = vi.fn()
    const onClearSelection = vi.fn()
    const onToggleViewMode = vi.fn()
    const onFocusPeriod = vi.fn()

    render(
      <TimelineToolbar
        viewport={viewport}
        unit="month"
        brush={{ fromMs: viewport.fromMs, toMs: viewport.fromMs + 1e10 }}
        viewMode="canvas"
        onZoomIn={onZoomIn}
        onZoomOut={onZoomOut}
        onFitAll={onFitAll}
        onZoomToSelection={onZoomToSelection}
        onClearSelection={onClearSelection}
        onToggleViewMode={onToggleViewMode}
        onFocusPeriod={onFocusPeriod}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /zoom in/i }))
    fireEvent.click(screen.getByRole('button', { name: /zoom out/i }))
    fireEvent.click(screen.getByRole('button', { name: /fit all/i }))
    fireEvent.click(screen.getByRole('button', { name: /zoom to selection/i }))
    fireEvent.click(screen.getByRole('button', { name: /clear selection/i }))
    fireEvent.click(screen.getByRole('button', { name: /focus period/i }))
    fireEvent.click(screen.getByRole('button', { name: /view as table/i }))

    expect(onZoomIn).toHaveBeenCalledTimes(1)
    expect(onZoomOut).toHaveBeenCalledTimes(1)
    expect(onFitAll).toHaveBeenCalledTimes(1)
    expect(onZoomToSelection).toHaveBeenCalledTimes(1)
    expect(onClearSelection).toHaveBeenCalledTimes(1)
    expect(onFocusPeriod).toHaveBeenCalledTimes(1)
    expect(onToggleViewMode).toHaveBeenCalledTimes(1)
  })

  it('zoom-to-selection and focus-period disabled without brush', () => {
    render(
      <TimelineToolbar
        viewport={viewport}
        unit="month"
        brush={null}
        viewMode="canvas"
        onZoomIn={() => {}}
        onZoomOut={() => {}}
        onFitAll={() => {}}
        onZoomToSelection={() => {}}
        onClearSelection={() => {}}
        onToggleViewMode={() => {}}
        onFocusPeriod={() => {}}
      />,
    )

    expect(screen.getByRole('button', { name: /zoom to selection/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /clear selection/i })).toBeDisabled()
    expect(screen.getByTestId('focus-period-btn')).toBeDisabled()
  })

  it('focus-period enabled when brush exists', () => {
    render(
      <TimelineToolbar
        viewport={viewport}
        unit="month"
        brush={{ fromMs: 1, toMs: 2 }}
        viewMode="canvas"
        onZoomIn={() => {}}
        onZoomOut={() => {}}
        onFitAll={() => {}}
        onZoomToSelection={() => {}}
        onClearSelection={() => {}}
        onToggleViewMode={() => {}}
        onFocusPeriod={() => {}}
      />,
    )
    expect(screen.getByTestId('focus-period-btn')).not.toBeDisabled()
  })

  it('documents Alt+double-click zoom in toolbar hint', () => {
    render(
      <TimelineToolbar
        viewport={viewport}
        unit="month"
        brush={null}
        viewMode="canvas"
        onZoomIn={() => {}}
        onZoomOut={() => {}}
        onFitAll={() => {}}
        onZoomToSelection={() => {}}
        onClearSelection={() => {}}
        onToggleViewMode={() => {}}
      />,
    )
    expect(screen.getByTestId('toolbar-focus-hint')).toHaveTextContent(
      /Alt\+double-click/i,
    )
  })

  it('Compare button dispatches onCompare', () => {
    const onCompare = vi.fn()
    render(
      <TimelineToolbar
        viewport={viewport}
        unit="month"
        brush={null}
        viewMode="canvas"
        onZoomIn={() => {}}
        onZoomOut={() => {}}
        onFitAll={() => {}}
        onZoomToSelection={() => {}}
        onClearSelection={() => {}}
        onToggleViewMode={() => {}}
        onCompare={onCompare}
      />,
    )
    fireEvent.click(screen.getByTestId('compare-btn'))
    expect(onCompare).toHaveBeenCalledTimes(1)
  })

  it('Compare button disabled without onCompare', () => {
    render(
      <TimelineToolbar
        viewport={viewport}
        unit="month"
        brush={null}
        viewMode="canvas"
        onZoomIn={() => {}}
        onZoomOut={() => {}}
        onFitAll={() => {}}
        onZoomToSelection={() => {}}
        onClearSelection={() => {}}
        onToggleViewMode={() => {}}
      />,
    )
    expect(screen.getByTestId('compare-btn')).toBeDisabled()
  })

  it('documents Shift+C compare in toolbar hint', () => {
    render(
      <TimelineToolbar
        viewport={viewport}
        unit="month"
        brush={null}
        viewMode="canvas"
        onZoomIn={() => {}}
        onZoomOut={() => {}}
        onFitAll={() => {}}
        onZoomToSelection={() => {}}
        onClearSelection={() => {}}
        onToggleViewMode={() => {}}
        onCompare={() => {}}
      />,
    )
    expect(screen.getByTestId('toolbar-focus-hint')).toHaveTextContent(/Shift\+C/)
  })
})
