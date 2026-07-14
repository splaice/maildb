export function ScopeBar() {
  return (
    <div
      className="col-span-3 flex items-center gap-2 border-b border-steel bg-graphite-900 px-3"
      style={{ height: 44 }}
      role="region"
      aria-label="Working set scope"
    >
      <span className="rounded-md border border-steel bg-graphite-800 px-2 py-1 text-text-primary">
        Archive: all mailboxes
      </span>
      <button
        type="button"
        disabled
        className="rounded-md border border-steel px-2 py-1 text-text-muted disabled:cursor-not-allowed disabled:opacity-60"
      >
        Reset working set
      </button>
    </div>
  )
}
