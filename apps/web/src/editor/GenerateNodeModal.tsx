import { Spinner, Sparkle, X } from "@phosphor-icons/react";
import { memo, useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { GenerateNodeResponse } from "../types";

interface GenerateNodeModalProps {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  environments: Array<{ id: string; name: string }>;
  defaultScope?: string;
  defaultScopeId?: string;
}

export const GenerateNodeModal = memo(function GenerateNodeModal({
  open,
  onClose,
  onSaved,
  environments,
  defaultScope = "environment",
  defaultScopeId,
}: GenerateNodeModalProps) {
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState(defaultScope);
  const [scopeId, setScopeId] = useState(defaultScopeId ?? "");
  const [generated, setGenerated] = useState<GenerateNodeResponse | null>(null);
  const [editableCode, setEditableCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const modalRef = useRef<HTMLDivElement | null>(null);

  // Reset state when opening
  useEffect(() => {
    if (open) {
      setDescription("");
      setScope(defaultScope);
      setScopeId(defaultScopeId ?? "");
      setGenerated(null);
      setEditableCode("");
      setLoading(false);
      setSaving(false);
      setError(null);
    }
  }, [open, defaultScope, defaultScopeId]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const handleGenerate = useCallback(async () => {
    if (!description.trim()) return;
    setLoading(true);
    setError(null);
    setGenerated(null);
    try {
      const result = await api.generateNode({
        description: description.trim(),
        scope,
        scope_id: scope === "environment" ? scopeId || null : null,
      });
      setGenerated(result);
      setEditableCode(result.code);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to generate node");
    } finally {
      setLoading(false);
    }
  }, [description, scope, scopeId]);

  const handleRegenerate = useCallback(async () => {
    if (!description.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.generateNode({
        description: description.trim(),
        scope,
        scope_id: scope === "environment" ? scopeId || null : null,
      });
      setGenerated(result);
      setEditableCode(result.code);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to regenerate node");
    } finally {
      setLoading(false);
    }
  }, [description, scope, scopeId]);

  const handleSave = useCallback(async () => {
    if (!generated) return;
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        scope,
        name: `${generated.node_name}.py`,
        contents: editableCode,
        metadata: { ai_generated: true, description: description.trim() },
      };
      if (scope === "environment") {
        body.environment_id = scopeId || undefined;
      }
      await api.createCodeModule(body as Parameters<typeof api.createCodeModule>[0]);
      onSaved();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save node");
    } finally {
      setSaving(false);
    }
  }, [generated, editableCode, description, scope, scopeId, onSaved, onClose]);

  if (!open) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="generate-node-modal"
        ref={modalRef}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Generate AI Node"
      >
        <div className="generate-node-modal-header">
          <h2>
            <Sparkle size={18} weight="fill" className="sparkle-icon" />
            Generate AI Node
          </h2>
          <button
            type="button"
            className="modal-close-btn"
            aria-label="Close"
            onClick={onClose}
          >
            <X size={16} weight="bold" />
          </button>
        </div>

        <div className="generate-node-modal-body">
          <label className="generate-node-label">
            Describe the node you want to create:
          </label>
          <textarea
            className="generate-node-textarea"
            placeholder="e.g. Call the Resend API to send emails with attachments"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            disabled={loading}
          />

          <div className="generate-node-scope-row">
            <label className="generate-node-label">Scope:</label>
            <select
              className="generate-node-select"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              disabled={loading}
            >
              <option value="environment">Environment</option>
              <option value="global">Global</option>
            </select>
            {scope === "environment" && (
              <select
                className="generate-node-select"
                value={scopeId}
                onChange={(e) => setScopeId(e.target.value)}
                disabled={loading || environments.length === 0}
              >
                <option value="">Select environment...</option>
                {environments.map((env) => (
                  <option key={env.id} value={env.id}>
                    {env.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          {!generated && !loading && (
            <button
              type="button"
              className="generate-node-btn"
              onClick={handleGenerate}
              disabled={!description.trim()}
            >
              <Sparkle size={16} weight="fill" />
              Generate Node
            </button>
          )}

          {loading && (
            <div className="generate-node-loading">
              <Spinner size={20} className="spinner" />
              <span>Generating node...</span>
            </div>
          )}

          {error && (
            <div className="generate-node-error">
              <X size={14} weight="bold" />
              {error}
            </div>
          )}

          {generated && !loading && (
            <div className="generate-node-result">
              <div className="generate-node-result-info">
                <span className="generate-node-badge">
                  {generated.is_template ? "Template" : "AI Generated"}
                </span>
                <span className="generate-node-name">{generated.node_name}</span>
                <span className="generate-node-id">{generated.node_id}</span>
              </div>

              {generated.warnings.length > 0 && (
                <div className="generate-node-warnings">
                  {generated.warnings.map((w, i) => (
                    <div key={i} className="generate-node-warning">
                      {w}
                    </div>
                  ))}
                </div>
              )}

              <div className="generate-node-ports">
                <span>Inputs: {Object.keys(generated.input_ports).join(", ") || "none"}</span>
                <span>Outputs: {Object.keys(generated.output_ports).join(", ") || "none"}</span>
              </div>

              <label className="generate-node-label">Generated code (editable):</label>
              <div className="generate-node-editor">
                <textarea
                  className="generate-node-code"
                  value={editableCode}
                  onChange={(e) => setEditableCode(e.target.value)}
                  rows={16}
                  spellCheck={false}
                />
              </div>

              <div className="generate-node-actions">
                <button
                  type="button"
                  className="generate-node-btn generate-node-btn--secondary"
                  onClick={handleRegenerate}
                >
                  Regenerate
                </button>
                <button
                  type="button"
                  className="generate-node-btn"
                  onClick={handleSave}
                  disabled={saving || !editableCode.trim()}
                >
                  {saving ? "Saving..." : "Save as Node"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
});
