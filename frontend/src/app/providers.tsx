import type { ReactNode } from 'react'
import ErrorBoundary from '../components/ErrorBoundary'
import { SyncContext } from '../context/SyncContext'

interface AppProvidersProps {
  children: ReactNode
  syncIntervalSeconds: number
  refreshKey: number
}

/** App-wide providers: error boundary + sync context. */
export default function AppProviders({ children, syncIntervalSeconds, refreshKey }: AppProvidersProps) {
  return (
    <ErrorBoundary>
      <SyncContext.Provider value={{ syncIntervalSeconds, refreshKey }}>{children}</SyncContext.Provider>
    </ErrorBoundary>
  )
}
