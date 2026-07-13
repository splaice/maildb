import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'

import { useSession } from './useSession'

export function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { isLoading, isSuccess, isError } = useSession()

  if (isLoading) {
    return (
      <div
        className="flex min-h-full items-center justify-center bg-graphite-950 text-text-muted"
        aria-busy="true"
      >
        Checking session…
      </div>
    )
  }

  if (isError || !isSuccess) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return children
}
