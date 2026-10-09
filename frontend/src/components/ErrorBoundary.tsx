import { Component, type ErrorInfo, type ReactNode } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

/**
 * Catches render/lifecycle errors anywhere in the tree so a single bad
 * component can't blank the whole SPA. React only supports error boundaries
 * as class components; this is the one intentional exception to the
 * "one functional component per file" convention.
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Unhandled UI error:', error, info.componentStack)
  }

  handleReload = (): void => {
    window.location.reload()
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="empty-profile" role="alert">
        <div className="empty-profile-icon" aria-hidden="true">
          ⚠️
        </div>
        <h1 className="empty-profile-title">Something went wrong</h1>
        <p className="empty-profile-text">
          The dashboard hit an unexpected error. Reloading usually fixes it.
        </p>
        <button type="button" className="ai-scan-btn" onClick={this.handleReload}>
          Reload
        </button>
      </div>
    )
  }
}