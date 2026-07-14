import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router'

/**
 * Page-level `R` binding: from Chronicle open Research Desk with current scope.
 * Mounted at the app root (allowed surface) so chronicle/* stays untouched.
 */
export function ResearchNavShortcut() {
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return
      }
      if (e.key !== 'r' && e.key !== 'R') return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const path = location.pathname
      if (path !== '/' && path !== '/chronicle') return
      e.preventDefault()
      navigate('/research')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [location.pathname, navigate])

  return null
}
