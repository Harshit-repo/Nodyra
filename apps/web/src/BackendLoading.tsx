/**
 * Full-screen loader shown while the auth bootstrap is in flight. When the API
 * isn't reachable yet (`retrying`), it keeps the loader up with a "connecting"
 * message instead of letting the app render against a dead backend.
 */
export function BackendLoading({ retrying }: { retrying: boolean }) {
  return (
    <div className="backend-loading">
      <div className="backend-loading-card">
        <div className="backend-loading-mark">Noodle</div>
        <div className="backend-loading-spinner" aria-hidden />
        <div className="backend-loading-text">
          {retrying ? "Connecting to the server…" : "Loading…"}
        </div>
        {retrying && (
          <div className="backend-loading-sub">
            The backend is still starting — retrying automatically
            <span className="backend-loading-dots" aria-hidden>
              <span>.</span>
              <span>.</span>
              <span>.</span>
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
