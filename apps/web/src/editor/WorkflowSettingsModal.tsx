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
        <label className="field">
          <span>Run timeout (seconds)</span>
          <input
            type="number"
            min={0}
            step={1}
            value={runTimeout}
            placeholder="No timeout"
            aria-label="Run timeout (seconds)"
            onChange={(e) => onRunTimeoutChange(e.target.value)}
          />
        </label>

        <label className="field-toggle">
          <input
            type="checkbox"
            checked={mcpEnabled}
            aria-label="Expose as MCP tool"
            onChange={(e) => onMcpEnabledChange(e.target.checked)}
          />
          <span>Expose as MCP tool (AI agents can call this workflow)</span>
        </label>

        {mcpEnabled && (
          <>
            <label className="field">
              <span>Tool name</span>
              <input
                value={mcpToolName}
                placeholder="Auto-generated from workflow name"
                aria-label="MCP tool name"
                onChange={(e) => onMcpToolNameChange(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Tool description</span>
              <input
                value={mcpDescription}
                placeholder="Shown to calling AI agents"
                aria-label="MCP tool description"
                onChange={(e) => onMcpDescriptionChange(e.target.value)}
              />
            </label>
          </>
        )}

        <div className="modal-actions">
          <button type="button" className="btn btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </A11yModal>
  );
}
