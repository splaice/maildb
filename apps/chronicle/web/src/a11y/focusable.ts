/** Collect tab-order candidates in document order (structural, not pixel). */

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function isVisible(el: HTMLElement): boolean {
  if (el.hidden) return false
  if (el.getAttribute('aria-hidden') === 'true') return false
  // Skip zero-size non-skip-link elements that are display:none
  const style = window.getComputedStyle(el)
  if (style.display === 'none' || style.visibility === 'hidden') return false
  return true
}

export function getFocusableElements(root: ParentNode = document): HTMLElement[] {
  const nodes = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
  return nodes.filter(isVisible)
}

export function classifyFocusTarget(el: HTMLElement): string {
  if (el.classList.contains('skip-link') || /skip to main/i.test(el.textContent ?? '')) {
    return 'skip-link'
  }
  if (
    el.closest('header') ||
    el.getAttribute('data-testid')?.startsWith('command-bar') ||
    el.getAttribute('aria-label') === 'Universal search' ||
    /logout/i.test(el.textContent ?? '') ||
    el.closest('[data-testid="command-bar-mode"]')
  ) {
    return 'command-bar'
  }
  if (el.closest('nav[aria-label="Primary"]')) {
    return 'nav'
  }
  if (el.closest('main#main') || el.closest('main')) {
    return 'main'
  }
  return 'other'
}
