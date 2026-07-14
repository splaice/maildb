/**
 * Central guard: shortcuts must never fire while typing in form fields.
 */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false
  const tag = target.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (target.isContentEditable) return true
  // role=textbox (contenteditable-ish comboboxes)
  if (target.getAttribute('role') === 'textbox') return true
  return false
}
