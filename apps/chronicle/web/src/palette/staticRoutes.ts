import type { Command } from './commandRegistry'

/** Static route commands always available in the palette. */
export function staticRouteCommands(): Command[] {
  return [
    {
      id: 'route.chronicle',
      title: 'Go to Chronicle',
      group: 'Routes',
      keywords: ['timeline', 'home'],
      run: (ctx) => ctx.navigate('/'),
    },
    {
      id: 'route.research',
      title: 'Go to Research',
      group: 'Routes',
      keywords: ['search', 'desk'],
      run: (ctx) => ctx.navigate('/research'),
    },
    {
      id: 'route.topics',
      title: 'Go to Topics',
      group: 'Routes',
      keywords: ['atlas'],
      run: (ctx) => ctx.navigate('/topics'),
    },
    {
      id: 'route.people',
      title: 'Go to People',
      group: 'Routes',
      keywords: ['contacts'],
      run: (ctx) => ctx.navigate('/people'),
    },
    {
      id: 'route.files',
      title: 'Go to Files',
      group: 'Routes',
      keywords: ['attachments'],
      run: (ctx) => ctx.navigate('/files'),
    },
    {
      id: 'route.workspaces',
      title: 'Go to Workspaces',
      group: 'Routes',
      keywords: ['notebook'],
      run: (ctx) => ctx.navigate('/workspaces'),
    },
    {
      id: 'route.data-health',
      title: 'Go to Data Health',
      group: 'Routes',
      keywords: ['coverage', 'health'],
      run: (ctx) => ctx.navigate('/data-health'),
    },
    {
      id: 'route.settings',
      title: 'Go to Settings',
      group: 'Routes',
      keywords: ['preferences'],
      run: (ctx) => ctx.navigate('/settings'),
    },
    {
      id: 'action.reset-scope',
      title: 'Reset scope',
      group: 'Actions',
      keywords: ['clear', 'filters'],
      run: () => {
        window.dispatchEvent(new CustomEvent('chronicle:reset-scope'))
      },
    },
  ]
}
