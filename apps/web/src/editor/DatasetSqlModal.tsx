import { useState } from "react";

import { api } from "../api";
import type { DatasetQueryResult } from "../types";
import type { DatasetRef } from "./datasetValues";
import { datasetSummary } from "./datasetValues";

/**
 * "Open in SQL" explorer — runs read-only DuckDB queries against the Parquet
 * file backing a DatasetRef. The dataset is exposed to the query as the views
 * ``dataset`` and ``input``. Results are hard-capped server-side.
 */
export function DatasetSqlModal({
  dataset,
  onClose,
}: {
  dataset: DatasetRef;
  onClose: () => void;
}) {
  const [sql, setSql] = useState("SELECT * FROM dataset LIMIT 100");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DatasetQueryResult | null>(null);

  const artifactId = dataset.artifact.artifact_id;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.queryDataset(artifactId, sql, 200);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      void run();
    }
  }

  const columns = result?.columns.map((c) => c.name) ?? [];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide dataset-sql-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2>SQL explorer</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="modal-body">
          <p className="field-desc">
            Read-only DuckDB query over <strong>{datasetSummary(dataset)}</strong>.
            The dataset is available as <code>dataset</code> or <code>input</code>.
            Press <kbd>Ctrl</kbd>+<kbd>Enter</kbd> to run.
          </p>
          <textarea
            className="dataset-sql-input"
            value={sql}
            spellCheck={false}
            rows={4}
            onChange={(e) => setSql(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="SELECT * FROM dataset LIMIT 100"
          />
          <div className="dataset-sql-actions">
            <button className="btn btn-primary btn-sm" onClick={run} disabled={busy}>
              {busy ? "Running…" : "Run query"}
            </button>
            {result && (
              <span className="muted">
                {result.row_count} row{result.row_count === 1 ? "" : "s"}
                {result.truncated ? " (capped)" : ""} · {result.elapsed_ms} ms
              </span>
            )}
          </div>
          {error && <div className="dataset-sql-error">{error}</div>}
          {result && (
            <div className="data-table-wrap dataset-sql-result">
              {result.rows.length === 0 ? (
                <div className="muted">Query returned no rows.</div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th className="data-table-index">#</th>
                      {columns.map((col) => {
                        const type = result.columns.find((c) => c.name === col)?.type;
                        return (
                          <th key={col} title={type}>
                            {col}
                            {type && <span className="col-dtype">{type}</span>}
                          </th>
                        );
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((row, i) => (
                      <tr key={i}>
                        <td className="data-table-index">{i}</td>
                        {columns.map((col) => (
                          <td key={col} title={fmt(row[col])}>
                            {fmt(row[col])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function fmt(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
