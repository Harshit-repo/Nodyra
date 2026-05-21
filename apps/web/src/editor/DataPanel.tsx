import type { ReactNode } from "react";
import { useState } from "react";

/**
 * Side panel of the NDV (Input or Output). Shows the value as either a
 * tree-view JSON browser or, when the data is shaped like a list of records,
 * an n8n-style table. Auto-unwraps single-output ports so the viewer sees the
 * items directly. When ``dragPrefix`` is set, leaf paths become draggable and
 * carry an ``{{ <prefix>.<path> }}`` expression that can be dropped into any
 * parameter text field.
 */

function unwrapSingleOutput(value: unknown): unknown {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const keys = Object.keys(value as Record<string, unknown>);
    if (keys.length === 1) return (value as Record<string, unknown>)[keys[0]];
  }
  return value;
}

function isListOfRecords(value: unknown): value is Record<string, unknown>[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (item) => item !== null && typeof item === "object" && !Array.isArray(item),
    )
  );
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value)
  );
}

function isTableable(value: unknown): boolean {
  return isListOfRecords(value) || isPlainObject(value);
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

const IDENT_RE = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

function buildExpression(prefix: string, path: (string | number)[]): string {
  let body = prefix;
  for (const seg of path) {
    if (typeof seg === "number") {
      body += `[${seg}]`;
    } else if (IDENT_RE.test(seg)) {
      body += `.${seg}`;
    } else {
      body += `[${JSON.stringify(seg)}]`;
    }
  }
  return `{{ ${body} }}`;
}

function startExpressionDrag(
  e: React.DragEvent<HTMLElement>,
  expr: string,
): void {
  e.dataTransfer.setData("text/plain", expr);
  e.dataTransfer.setData("application/x-noodle-expression", expr);
  e.dataTransfer.effectAllowed = "copy";
  // Render a small chip as the drag ghost so the user sees what they're
  // about to drop.
  const ghost = document.createElement("div");
  ghost.className = "expr-drag-ghost";
  ghost.textContent = expr;
  document.body.appendChild(ghost);
  e.dataTransfer.setDragImage(ghost, 12, 12);
  // The browser keeps the ghost alive for the drag — clean up next tick.
  window.setTimeout(() => ghost.remove(), 0);
}

function primitiveClass(value: unknown): string {
  if (value === null) return "json-tree-null";
  return `json-tree-${typeof value}`;
}

function JsonTreeValue({
  value,
  path,
  dragPrefix,
}: {
  value: unknown;
  path: (string | number)[];
  dragPrefix?: string;
}): JSX.Element {
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="json-tree-empty">[]</span>;
    }
    return (
      <div className="json-tree-block">
        {value.map((item, i) => (
          <JsonTreeRow
            key={i}
            label={String(i)}
            value={item}
            path={[...path, i]}
            dragPrefix={dragPrefix}
          />
        ))}
      </div>
    );
  }
  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) {
      return <span className="json-tree-empty">{"{}"}</span>;
    }
    return (
      <div className="json-tree-block">
        {entries.map(([k, v]) => (
          <JsonTreeRow
            key={k}
            label={k}
            value={v}
            path={[...path, k]}
            dragPrefix={dragPrefix}
          />
        ))}
      </div>
    );
  }
  return (
    <span className={`json-tree-prim ${primitiveClass(value)}`}>
      {value === null ? "null" : JSON.stringify(value)}
    </span>
  );
}

function JsonTreeRow({
  label,
  value,
  path,
  dragPrefix,
}: {
  label: string;
  value: unknown;
  path: (string | number)[];
  dragPrefix?: string;
}) {
  const draggable = Boolean(dragPrefix);
  const expr = dragPrefix ? buildExpression(dragPrefix, path) : undefined;
  return (
    <div className="json-tree-row">
      <span
        className={`json-tree-key${draggable ? " draggable" : ""}`}
        draggable={draggable}
        onDragStart={
          expr ? (e) => startExpressionDrag(e, expr) : undefined
        }
        title={
          draggable
            ? `Drag into a parameter to insert ${expr}`
            : undefined
        }
      >
        {draggable && <span className="drag-grip" aria-hidden>⠿</span>}
        {label}
      </span>
      <span className="json-tree-colon">:</span>
      <JsonTreeValue value={value} path={path} dragPrefix={dragPrefix} />
    </div>
  );
}

function JsonTree({
  data,
  dragPrefix,
}: {
  data: unknown;
  dragPrefix?: string;
}) {
  return (
    <div className="json-tree">
      <JsonTreeValue value={data} path={[]} dragPrefix={dragPrefix} />
    </div>
  );
}

function DataTable({
  data,
  dragPrefix,
}: {
  data: unknown;
  dragPrefix?: string;
}) {
  if (isListOfRecords(data)) {
    const columns = Array.from(
      new Set(data.flatMap((row) => Object.keys(row))),
    );
    // For a list, each row's iteration sees $json as that record.
    // Headers carry $json.col; cells carry $json.col too — both yield the
    // same per-item path, which is what the user almost always wants.
    return (
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th className="data-table-index">#</th>
              {columns.map((col) => {
                const expr = dragPrefix
                  ? buildExpression(dragPrefix, [col])
                  : undefined;
                return (
                  <th
                    key={col}
                    className={expr ? "draggable" : undefined}
                    draggable={Boolean(expr)}
                    onDragStart={
                      expr ? (e) => startExpressionDrag(e, expr) : undefined
                    }
                    title={expr ? `Drag to insert ${expr}` : undefined}
                  >
                    {expr && <span className="drag-grip" aria-hidden>⠿</span>}
                    {col}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i}>
                <td className="data-table-index">{i}</td>
                {columns.map((col) => {
                  const expr = dragPrefix
                    ? buildExpression(dragPrefix, [col])
                    : undefined;
                  return (
                    <td
                      key={col}
                      title={formatCell(row[col])}
                      className={expr ? "draggable-cell" : undefined}
                      draggable={Boolean(expr)}
                      onDragStart={
                        expr ? (e) => startExpressionDrag(e, expr) : undefined
                      }
                    >
                      {formatCell(row[col])}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (isPlainObject(data)) {
    return (
      <div className="data-table-wrap">
        <table className="data-table data-table-kv">
          <tbody>
            {Object.entries(data).map(([key, value]) => {
              const expr = dragPrefix
                ? buildExpression(dragPrefix, [key])
                : undefined;
              return (
                <tr key={key}>
                  <th
                    className={expr ? "draggable" : undefined}
                    draggable={Boolean(expr)}
                    onDragStart={
                      expr ? (e) => startExpressionDrag(e, expr) : undefined
                    }
                    title={expr ? `Drag to insert ${expr}` : undefined}
                  >
                    {expr && <span className="drag-grip" aria-hidden>⠿</span>}
                    {key}
                  </th>
                  <td title={formatCell(value)}>{formatCell(value)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }
  return <pre className="data-json">{JSON.stringify(data, null, 2)}</pre>;
}

export function DataPanel({
  title,
  data,
  emptyMessage,
  footer,
  dragPrefix,
  logs,
  durationMs,
}: {
  title: string;
  data: unknown;
  emptyMessage?: string;
  footer?: ReactNode;
  dragPrefix?: string;
  logs?: string[];
  durationMs?: number | null;
}) {
  const display = unwrapSingleOutput(data);
  const canTable = isTableable(display);
  const hasLogs = logs !== undefined && logs.length > 0;
  const [view, setView] = useState<"json" | "table" | "logs">(
    canTable ? "table" : "json",
  );
  const empty = data === undefined || data === null;
  let effectiveView: "json" | "table" | "logs" = view;
  if (view === "table" && !canTable) effectiveView = "json";
  if (view === "logs" && !hasLogs) effectiveView = canTable ? "table" : "json";

  return (
    <section className="ndv-panel">
      <header className="ndv-panel-head">
        <h3>
          {title}
          {typeof durationMs === "number" && (
            <span className="ndv-duration" title="Execution time">
              {durationMs} ms
            </span>
          )}
        </h3>
        <div className="data-view-toggle">
          <button
            type="button"
            className={effectiveView === "json" ? "active" : ""}
            onClick={() => setView("json")}
          >
            JSON
          </button>
          <button
            type="button"
            className={effectiveView === "table" ? "active" : ""}
            onClick={() => canTable && setView("table")}
            disabled={!canTable}
            title={canTable ? "Show as a table" : "Data is not tabular"}
          >
            Table
          </button>
          {logs !== undefined && (
            <button
              type="button"
              className={effectiveView === "logs" ? "active" : ""}
              onClick={() => hasLogs && setView("logs")}
              disabled={!hasLogs}
              title={hasLogs ? "Show captured logs" : "No logs for this node"}
            >
              Logs{hasLogs ? ` (${logs.length})` : ""}
            </button>
          )}
        </div>
      </header>
      <div className="ndv-panel-body">
        {effectiveView === "logs" ? (
          <pre className="data-json data-logs">{(logs ?? []).join("\n")}</pre>
        ) : empty ? (
          <p className="ndv-panel-empty muted">
            {emptyMessage ?? "No data yet."}
          </p>
        ) : effectiveView === "table" ? (
          <DataTable data={display} dragPrefix={dragPrefix} />
        ) : (
          <JsonTree data={display} dragPrefix={dragPrefix} />
        )}
        {dragPrefix && !empty && effectiveView !== "logs" && (
          <p className="ndv-panel-hint muted">
            Tip: drag any field into a parameter to insert{" "}
            <code>{`{{ ${dragPrefix}.field }}`}</code>.
          </p>
        )}
      </div>
      {footer && <footer className="ndv-panel-foot">{footer}</footer>}
    </section>
  );
}
