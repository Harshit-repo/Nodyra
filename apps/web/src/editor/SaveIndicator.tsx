export type SaveState = "saving" | "saved" | "unsaved" | "error";

export function SaveIndicator({ state, onRetry }: { state: SaveState; onRetry?: () => void }) {
  if (state === "saving") {
    return (
      <span className="save-indicator is-saving">
        <span className="save-spinner" aria-hidden /> Saving…
      </span>
    );
  }
  if (state === "unsaved") {
    return (
      <span className="save-indicator is-unsaved">
        <span className="save-pendingdot" aria-hidden /> Unsaved edits
      </span>
    );
  }
  if (state === "error") {
    return (
      <span className="save-indicator is-error">
        ⚠ Save failed —{" "}
        <button type="button" className="save-retry" onClick={onRetry}>
          Retry
        </button>
      </span>
    );
  }
  return (
    <span className="save-indicator is-saved">
      <span className="save-check" aria-hidden>✓</span> All changes saved
    </span>
  );
}
