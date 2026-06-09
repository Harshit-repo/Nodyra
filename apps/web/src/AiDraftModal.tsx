import { useRef } from "react";

import type { AiFixStrategy, AiWorkflowDraftResponse } from "./types";
import { useModalA11y } from "./useModalA11y";

interface AiDraftModalProps {
  mode: "draft" | "fix";
  prompt: string;
  preview: AiWorkflowDraftResponse | null;
  busy: boolean;
  fixStrategy: AiFixStrategy;
  onPromptChange: (value: string) => void;
  onFixStrategyChange: (value: AiFixStrategy) => void;
  onPreview: () => void;
  onApply: () => void;
  onClose: () => void;
}

export function AiDraftModal({
  mode,
  prompt,
  preview,
  busy,
  fixStrategy,
  onPromptChange,
  onFixStrategyChange,
  onPreview,
  onApply,
  onClose,
}: AiDraftModalProps) {
  const isFix = mode === "fix";
  const title = isFix ? "Fix with AI" : "AI workflow draft";
  const previewLabel = isFix
    ? fixStrategy === "replacement"
      ? "Preview replacement"
      : "Preview repair"
    : "Preview draft";
  const applyLabel = isFix
    ? fixStrategy === "replacement"
      ? "Apply replacement"
      : "Apply repair"
    : "Apply draft";
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="modal modal-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-draft-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="ai-draft-title">{title}</h2>
          <button
            className="btn btn-sm btn-ghost"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
          >
            ✕
          </button>
        </header>
        <div className="modal-body">
          <p className="field-desc">
            {isFix
              ? "Use AI to repair the current graph. Choose a minimal patch or a cleaner replacement proposal, then review before applying."
              : "Creates a normal editable draft on the canvas. Review credentials and parameters before publishing."}
          </p>
          {isFix && (
            <div className="ai-preview-list" role="group" aria-label="Fix strategy">
              <label className="field-toggle">
                <input
                  type="radio"
                  name="ai-fix-strategy"
                  checked={fixStrategy === "minimal"}
                  onChange={() => onFixStrategyChange("minimal")}
                  disabled={busy}
                />
                <span className="field-toggle-text">
                  <strong>Minimal repair</strong> — preserve the current graph and change as little as possible.
                </span>
              </label>
              <label className="field-toggle">
                <input
                  type="radio"
                  name="ai-fix-strategy"
                  checked={fixStrategy === "replacement"}
                  onChange={() => onFixStrategyChange("replacement")}
                  disabled={busy}
                />
                <span className="field-toggle-text">
                  <strong>Replacement proposal</strong> — allow a cleaner draft if the current graph is too fragile.
                </span>
              </label>
            </div>
          )}
          <textarea
            className="field-input field-code"
            rows={6}
            value={prompt}
            onChange={(e) => onPromptChange(e.target.value)}
            placeholder="When a GitHub issue is opened, summarize it with OpenAI and post to Slack."
            spellCheck={false}
            aria-label={isFix ? "Fix instructions" : "Workflow description"}
          />
          {preview && (
            <div className="ai-preview">
              <div className="ai-preview-head">
                <strong>{preview.graph.nodes.length} nodes</strong>
                <span>{preview.graph.edges.length} connections</span>
              </div>
              <p>{preview.explanation}</p>
              <p className="field-desc">
                Planner: {preview.planner.replaceAll("_", " ")} · Confidence: {preview.confidence}
                {preview.mode === "fix" ? ` · ${fixStrategy === "replacement" ? "Replacement" : "Minimal repair"}` : ""}
              </p>
              {preview.focus_node_id && (
                <p className="field-desc">Focus node: {preview.focus_node_id}</p>
              )}
              {preview.change_summary.length > 0 && (
                <ul className="ai-preview-list">
                  {preview.change_summary.map((change) => (
                    <li key={change}>{change}</li>
                  ))}
                </ul>
              )}
              {preview.missing_credentials.length > 0 && (
                <p className="error-text">
                  Missing credentials: {preview.missing_credentials.join(", ")}
                </p>
              )}
              {preview.required_packages.length > 0 && (
                <p className="field-desc">
                  Packages: {preview.required_packages.join(", ")}
                </p>
              )}
              {preview.assumptions.length > 0 && (
                <ul className="ai-preview-list">
                  {preview.assumptions.map((assumption) => (
                    <li key={assumption}>{assumption}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        <footer className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn" onClick={onPreview} disabled={busy || !prompt.trim()}>
            {busy ? "Building..." : previewLabel}
          </button>
          {preview && (
            <button className="btn btn-primary" onClick={onApply} disabled={busy}>
              {applyLabel}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
