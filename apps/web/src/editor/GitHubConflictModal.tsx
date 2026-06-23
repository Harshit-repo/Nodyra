import { useState } from "react";
import { useResolveGithubConflictMutation } from "../queries";
import { useToast } from "../ToastProvider";

interface Props {
  workflowId: string;
  workflowName: string;
  onClose: () => void;
}

export function GitHubConflictModal({ workflowId, workflowName, onClose }: Props) {
  const { notify } = useToast();
  const resolve = useResolveGithubConflictMutation();
  const [resolving, setResolving] = useState<"noodle" | "github" | null>(null);

  async function handleResolve(side: "noodle" | "github") {
    setResolving(side);
    try {
      await resolve.mutateAsync({ workflowId, side });
      notify(
        side === "noodle"
          ? "Kept Noodle version — GitHub will be overwritten on next sync."
          : "GitHub version applied — draft updated.",
        "success",
      );
      onClose();
    } catch {
      notify("Failed to resolve conflict. Please try again.", "error");
    } finally {
      setResolving(null);
    }
  }

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal
      aria-label="Resolve GitHub sync conflict"
    >
      <div className="modal-box conflict-modal">
        <header className="modal-header">
          <h2>GitHub Sync Conflict</h2>
          <button
            type="button"
            className="btn-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </header>

        <p className="conflict-description">
          Both <strong>{workflowName}</strong> in Noodle and the file on GitHub were
          edited since the last sync. Choose which version to keep.
        </p>

        <div className="conflict-actions">
          <div className="conflict-option">
            <h3>Keep Noodle version</h3>
            <p>Your current draft stays as-is. GitHub will be overwritten on the next push.</p>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleResolve("noodle")}
              disabled={resolving !== null}
            >
              {resolving === "noodle" ? "Applying…" : "Keep Noodle"}
            </button>
          </div>

          <div className="conflict-divider" aria-hidden>
            or
          </div>

          <div className="conflict-option">
            <h3>Take GitHub version</h3>
            <p>The GitHub file replaces your current draft. Your unsaved edits will be lost.</p>
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => void handleResolve("github")}
              disabled={resolving !== null}
            >
              {resolving === "github" ? "Applying…" : "Take GitHub"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
