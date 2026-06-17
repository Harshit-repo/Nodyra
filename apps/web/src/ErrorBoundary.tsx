import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Bump this (e.g. with the route pathname) to clear the error on navigation. */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

/**
 * Catches render-time exceptions anywhere below it so a single bad component
 * doesn't blank the entire SPA (white screen). Shows a recoverable fallback
 * with a reload affordance. Changing `resetKey` (we key it on the route)
 * clears the error so navigating away recovers without a hard reload.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep a console trail for support/debugging; no user secrets are logged.
    console.error("Unhandled UI error:", error, info.componentStack);
  }

  componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div className="app-error" role="alert">
        <div className="app-error-card">
          <div className="app-error-title">Something went wrong</div>
          <div className="app-error-text">
            The page hit an unexpected error. Reloading usually fixes it.
          </div>
          <div className="app-error-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => window.location.reload()}
            >
              Reload
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => this.setState({ error: null })}
            >
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }
}
