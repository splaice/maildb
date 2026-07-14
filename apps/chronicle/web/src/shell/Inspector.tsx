import { InspectorPanel } from '../inspector/InspectorPanel'
import { useWorkingSetStore } from '../workingset/store'

/**
 * Evidence inspector host. Content swaps on selection kind (bucket / message / none).
 * Bucket count and unit are optional context; unit defaults to month in the panel.
 */
export function Inspector() {
  // unit is refined by ChroniclePage via a lightweight store field if needed;
  // for now InspectorPanel defaults unit for date_to computation.
  const selection = useWorkingSetStore((s) => s.selection)
  void selection // re-render on selection change

  return (
    <aside
      className="flex min-h-0 flex-col border-l border-steel bg-graphite-900 p-3"
      style={{ width: 360 }}
      aria-label="Evidence inspector"
      data-testid="evidence-inspector"
    >
      <h2 className="mb-2 text-text-muted">Inspector</h2>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <InspectorPanel />
      </div>
    </aside>
  )
}
