import { A11yModal } from "./A11yModal";

export type WorkflowExecutionMode = "inherit" | "sandboxed" | "standard";

export interface SandboxResources {
  memory_mb?: number;
  cpu?: number;
  tmpfs_mb?: number;
}

export interface WorkflowSettingsModalProps {
  runTimeout: string;
  onRunTimeoutChange: (v: string) => void;
  executionMode: WorkflowExecutionMode;
  onExecutionModeChange: (v: WorkflowExecutionMode) => void;
  sandboxResources: SandboxResources;
  onSandboxResourcesChange: (v: SandboxResources) => void;
  mcpEnabled: boolean;
  onMcpEnabledChange: (v: boolean) => void;
  mcpToolName: string;
  onMcpToolNameChange: (v: string) => void;
  mcpDescription: string;
  onMcpDescriptionChange: (v: string) => void;
  onClose: () => void;
}

export function WorkflowSettingsModal(props: WorkflowSettingsModalProps) {
  const {
    runTimeout, onRunTimeoutChange,
    executionMode, onExecutionModeChange,
    sandboxResources, onSandboxResourcesChange,
    mcpEnabled, onMcpEnabledChange,
    mcpToolName, onMcpToolNameChange,
    mcpDescription, onMcpDescriptionChange,
    onClose,
  } = props;

  const setResource = (key: keyof SandboxResources, value: string) => {
    const next = { ...sandboxResources };
    if (value === "") {
      delete next[key];
    } else {
      next[key] = Number(value);
    }
    onSandboxResourcesChange(next);
  };

  return (
    <A11yModal
      className="workflow-settings-modal"
      titleId="workflow-settings-title"
      title="Workflow settings"
      onClose={onClose}
    >
      <div className="workflow-settings-body">
        <section className="ws-section">
          <div className="ws-section-head">
            <h4>Execution</h4>
            <p>How runs of this workflow behave.</p>
          </div>
          <label className="field ws-field">
            <span className="ws-field-label">
              Run timeout
              <span className="ws-field-unit">seconds</span>
            </span>
            <input
              type="number"
              min={0}
              step={1}
              value={runTimeout}
              placeholder="No timeout"
              aria-label="Run timeout (seconds)"
              onChange={(e) => onRunTimeoutChange(e.target.value)}
            />
            <span className="ws-field-hint">
              Leave empty for no limit. Runs that exceed this are cancelled.
            </span>
          </label>

          <label className="field ws-field">
            <span className="ws-field-label">Execution isolation</span>
            <select
              value={executionMode}
              aria-label="Execution isolation"
              onChange={(e) => onExecutionModeChange(e.target.value as WorkflowExecutionMode)}
            >
              <option value="inherit">Deployment default</option>
              <option value="sandboxed">Always sandboxed (hardened container)</option>
              <option value="standard">Standard pool (trusted, fastest)</option>
            </select>
            {executionMode === "sandboxed" && (
              <span className="ws-field-hint">
                Runs execute in a disposable container. If no sandbox is
                available on the worker, runs are refused rather than downgraded.
              </span>
            )}
          </label>

          {executionMode === "sandboxed" && (
            <div className="settings-row-3col">
              <label className="field ws-field">
                <span className="ws-field-label">
                  Memory
                  <span className="ws-field-unit">MB</span>
                </span>
                <input
                  type="number"
                  min={128}
                  value={sandboxResources.memory_mb ?? ""}
                  placeholder="1024"
                  aria-label="Sandbox memory (MB)"
                  onChange={(e) => setResource("memory_mb", e.target.value)}
                />
              </label>
              <label className="field ws-field">
                <span className="ws-field-label">CPU cores</span>
                <input
                  type="number"
                  min={0.25}
                  step={0.25}
                  value={sandboxResources.cpu ?? ""}
                  placeholder="1"
                  aria-label="Sandbox CPU cores"
                  onChange={(e) => setResource("cpu", e.target.value)}
                />
              </label>
              <label className="field ws-field">
                <span className="ws-field-label">
                  Scratch /tmp
                  <span className="ws-field-unit">MB</span>
                </span>
                <input
                  type="number"
                  min={64}
                  value={sandboxResources.tmpfs_mb ?? ""}
                  placeholder="256"
                  aria-label="Sandbox /tmp (MB)"
                  onChange={(e) => setResource("tmpfs_mb", e.target.value)}
                />
              </label>
              <p className="ws-field-hint settings-row-note">
                Blank uses the deployment default. Requests above the deployment
                ceiling are rejected.
              </p>
            </div>
          )}
        </section>

        <section className="ws-section">
          <div className="ws-section-head">
            <h4>AI agent access</h4>
            <p>Let AI agents discover and call this workflow over MCP.</p>
          </div>
          <label className="field-toggle ws-toggle">
            <input
              type="checkbox"
              checked={mcpEnabled}
              aria-label="Expose as MCP tool"
              onChange={(e) => onMcpEnabledChange(e.target.checked)}
            />
            <span className="field-toggle-track" />
            <span className="ws-toggle-label">Expose as MCP tool</span>
          </label>

          {mcpEnabled && (
            <div className="ws-subfields">
              <label className="field ws-field">
                <span className="ws-field-label">Tool name</span>
                <input
                  value={mcpToolName}
                  placeholder="Auto-generated from workflow name"
                  aria-label="MCP tool name"
                  onChange={(e) => onMcpToolNameChange(e.target.value)}
                />
              </label>
              <label className="field ws-field">
                <span className="ws-field-label">Tool description</span>
                <textarea
                  value={mcpDescription}
                  rows={2}
                  placeholder="Shown to calling AI agents so they know when to use it"
                  aria-label="MCP tool description"
                  onChange={(e) => onMcpDescriptionChange(e.target.value)}
                />
              </label>
            </div>
          )}
        </section>

        <div className="modal-actions">
          <button type="button" className="btn btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </A11yModal>
  );
}
