import { A11yModal } from "./A11yModal";

export interface WorkflowSettingsModalProps {
  runTimeout: string;
  onRunTimeoutChange: (v: string) => void;
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
    mcpEnabled, onMcpEnabledChange,
    mcpToolName, onMcpToolNameChange,
    mcpDescription, onMcpDescriptionChange,
    onClose,
  } = props;

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
