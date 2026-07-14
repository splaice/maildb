import { useMemo } from 'react'
import { useLocation, useParams } from 'react-router'

import type { Command } from './commandRegistry'
import { useRegisterCommands } from './CommandContext'

/**
 * Context-specific palette commands that don't belong to a single page module
 * (workspace export when on a workspace route).
 */
export function ContextCommands() {
  const location = useLocation()
  const params = useParams()

  const commands = useMemo((): Command[] => {
    const list: Command[] = []
    const wsMatch = /^\/workspaces\/([^/]+)/.exec(location.pathname)
    if (wsMatch) {
      const id = wsMatch[1]!
      list.push({
        id: 'workspace.export',
        title: 'Export workspace…',
        group: 'Actions',
        keywords: ['export', 'download'],
        run: (ctx) => {
          // Navigate to workspace with export focus; page owns the menu.
          ctx.navigate(`/workspaces/${encodeURIComponent(id)}`)
          window.dispatchEvent(
            new CustomEvent('chronicle:export-workspace', { detail: { id } }),
          )
        },
      })
    }
    void params
    return list
  }, [location.pathname, params])

  useRegisterCommands(commands)
  return null
}
