export function Inspector() {
  return (
    <aside
      className="flex flex-col border-l border-steel bg-graphite-900 p-3"
      style={{ width: 360 }}
      aria-label="Evidence inspector"
    >
      <h2 className="mb-2 text-text-muted">Inspector</h2>
      <p className="text-text-muted">Select a mark to inspect evidence</p>
    </aside>
  )
}
