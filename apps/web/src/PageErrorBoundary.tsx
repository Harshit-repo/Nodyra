import { Component, type ErrorInfo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Plugs } from "@phosphor-icons/react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Page error:", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page-error" role="alert">
        <div className="app-error-card">
          <Plugs size={40} className="page-error-icon" />
          <div className="app-error-title">This page ran into a problem</div>
          <div className="app-error-text">
            The error is contained here — the rest of the app is unaffected.
          </div>
          <div className="app-error-actions">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => this.setState({ error: null })}
            >
              Try again
            </button>
            <Link to="/" className="btn btn-primary">
              Back to workflows
            </Link>
          </div>
        </div>
      </div>
    );
  }
}
