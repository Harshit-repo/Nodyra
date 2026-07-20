import { FileArrowUp, WarningCircle } from "@phosphor-icons/react";
import { useRef, useState } from "react";

import { api, errorMessage } from "./api";
import type { WorkflowImportFormat, WorkflowImportPreview } from "./types";
import { useModalA11y } from "./useModalA11y";

const MAX_SOURCE_BYTES = 2_000_000;

export function WorkflowImportModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (workflowId: string) => void;
}) {
  const [format, setFormat] = useState<WorkflowImportFormat>("n8n");
  const [name, setName] = useState("Imported workflow");
  const [source, setSource] = useState("");
  const [preview, setPreview] = useState<WorkflowImportPreview | null>(null);
  const [allowPartial, setAllowPartial] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  async function readFile(file: File | undefined): Promise<void> {
    if (!file) return;
    if (file.size > MAX_SOURCE_BYTES) {
      setError("Import source exceeds the 2 MB safety limit.");
      return;
    }
    setSource(await file.text());
    setPreview(null);
    setError("");
    if (name === "Imported workflow") {
      setName(file.name.replace(/\.(json|py)$/i, "") || name);
    }
  }

  async function analyze(): Promise<void> {
    if (!source.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      setPreview(await api.previewWorkflowImport({
        source,
        source_format: format,
        allow_partial: allowPartial,
      }));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function importWorkflow(): Promise<void> {
    if (!preview?.importable || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.importWorkflow({
        name: name.trim() || "Imported workflow",
        source,
        source_format: format,
        allow_partial: allowPartial,
      });
      onImported(result.workflow_id);
    } catch (reason) {
      setError(errorMessage(reason));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal workflow-import-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="workflow-import-title"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="workflow-import-title">Import workflow</h2>
        <p className="muted">
          Analyze first. Nodyra never executes imported source during compatibility review.
        </p>

        <div className="workflow-import-fields">
          <label>
            <span>Source format</span>
            <select className="field-input" value={format} onChange={(event) => {
              setFormat(event.target.value as WorkflowImportFormat);
              setPreview(null);
            }}>
              <option value="n8n">n8n workflow JSON</option>
              <option value="nodyra_module">Nodyra module export</option>
              <option value="python_script">Standalone Python script</option>
              <option value="airflow">Airflow DAG Python</option>
              <option value="prefect">Prefect flow Python</option>
            </select>
          </label>
          <label>
            <span>Workflow name</span>
            <input className="field-input" value={name} maxLength={200} onChange={(event) => setName(event.target.value)} />
          </label>
        </div>

        <label className="workflow-import-file btn">
          <FileArrowUp size={16} aria-hidden="true" />
          Choose .json or .py file
          <input type="file" accept=".json,.py,application/json,text/x-python" onChange={(event) => void readFile(event.target.files?.[0])} />
        </label>
        <textarea
          className="field-input workflow-import-source"
          value={source}
          maxLength={MAX_SOURCE_BYTES}
          spellCheck={false}
          aria-label="Workflow import source"
          placeholder="Paste workflow JSON or Python source"
          onChange={(event) => {
            setSource(event.target.value);
            setPreview(null);
          }}
        />

        {format !== "nodyra_module" && (
          <label className="workflow-import-partial">
            <input type="checkbox" checked={allowPartial} onChange={(event) => {
              setAllowPartial(event.target.checked);
              setPreview(null);
            }} />
            Allow a partial draft after I review all manual and unsupported findings
          </label>
        )}

        {preview && (
          <section className="workflow-import-report" aria-labelledby="workflow-import-report-title">
            <h3 id="workflow-import-report-title">Compatibility report</h3>
            <div className="workflow-import-summary">
              {Object.entries(preview.summary).map(([status, count]) => (
                <span key={status} data-status={status}>{count} {status}</span>
              ))}
            </div>
            <ul>
              {preview.findings.map((finding, index) => (
                <li key={`${finding.source_id}-${index}`}>
                  <strong>{finding.source_id}</strong>
                  <span data-status={finding.status}>{finding.status}</span>
                  <p>{finding.message}</p>
                </li>
              ))}
            </ul>
          </section>
        )}

        {error && <p className="error-text" role="alert"><WarningCircle size={16} /> {error}</p>}

        <div className="modal-actions">
          <button className="btn btn-ghost" type="button" onClick={onClose}>Cancel</button>
          <button className="btn" type="button" disabled={busy || !source.trim()} onClick={() => void analyze()}>
            {busy ? "Analyzing…" : "Analyze compatibility"}
          </button>
          <button className="btn btn-primary" type="button" disabled={busy || !preview?.importable} onClick={() => void importWorkflow()}>
            Import reviewed draft
          </button>
        </div>
      </div>
    </div>
  );
}
