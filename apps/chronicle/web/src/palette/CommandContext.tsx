import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from 'react'
import { useNavigate } from 'react-router'

import { CommandRegistry, type Command } from './commandRegistry'
import { staticRouteCommands } from './staticRoutes'

const RegistryContext = createContext<CommandRegistry | null>(null)

export function CommandRegistryProvider({ children }: { children: ReactNode }) {
  const registry = useMemo(() => new CommandRegistry(), [])

  // Static routes/actions always registered.
  useEffect(() => {
    const unsubs = staticRouteCommands().map((c) => registry.register(c))
    return () => {
      for (const u of unsubs) u()
    }
  }, [registry])

  return (
    <RegistryContext.Provider value={registry}>{children}</RegistryContext.Provider>
  )
}

export function useCommandRegistry(): CommandRegistry | null {
  return useContext(RegistryContext)
}

/** Register commands for the lifetime of the calling component. No-op without provider. */
export function useRegisterCommands(commands: Command[]): void {
  const registry = useContext(RegistryContext)
  const key = commands.map((c) => c.id).join('|')
  useEffect(() => {
    if (!registry) return
    const unsubs = commands.map((c) => registry.register(c))
    return () => {
      for (const u of unsubs) u()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key captures set
  }, [registry, key])
}

/** Listen for reset-scope from palette static action. */
export function useResetScopeListener(clearScope: () => void): void {
  useEffect(() => {
    const onReset = () => clearScope()
    window.addEventListener('chronicle:reset-scope', onReset)
    return () => window.removeEventListener('chronicle:reset-scope', onReset)
  }, [clearScope])
}

/** Navigate helper for command context builders. */
export function useCommandNavigate(): (to: string) => void {
  const navigate = useNavigate()
  return navigate
}
