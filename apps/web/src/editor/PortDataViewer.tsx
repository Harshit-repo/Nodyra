import type { Edge } from "@xyflow/react";
import type { CSSProperties, MouseEvent as ReactMouseEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { DatasetSqlModal } from "./DatasetSqlModal";

import { useEditor, type NoodleNode } from "./store";
import {
  artifactDownloadUrl,
  artifactInlineUrl,
  artifactMediaKind,
  artifactSummary,
  asArtifactRef,
  formatBytes,
} from "./artifactValues";
import {
  asDatasetRef,
  datasetDownloadUrl,
  datasetSummary,
  type DatasetRef,
} from "./datasetValues";
import {
  asTypedEnvelope,
  formatTypedCell,
  typedDisplayValue,
  typedLabel,
  typedRecords,
  typedSummary,
} from "./typedValues";
import { asChartRef, asReportRef } from "./chartValues";
import { ChartView } from "./ChartView";
import { ReportView } from "./ReportView";

const MIN_HEIGHT = 160;
const DEFAULT_HEIGHT = 248;
const MAX_HEIGHT = 430;
const EMPTY_HEIGHT = 112;
const COLLAPSED_HEIGHT = 44;

interface PortConnectionPreview {
  edge: Edge;
  value: unknown;
  hasValue: boolean;
}

function outputNames(node: NoodleNode): string[] {
  return node.data.outputsOverride ?? node.data.manifest?.outputs.map((o) => o.name) ?? [];
}

function valueAtPort(
  outputs: unknown,
  port: string,
): { value: unknown; hasValue: boolean } {
  if (outputs && typeof outputs === "object" && !Array.isArray(outputs)) {
    const record = outputs as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(record, port)) {
      return { value: record[port], hasValue: true };
    }
  }
  return { value: undefined, hasValue: false };
}

function pretty(value: unknown): string {
  if (value === undefined) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isRecordList(value: unknown): value is Record<string, unknown>[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => isPlainRecord(item))
  );
}

function findRecordList(
  value: unknown,
): { label: string | null; rows: Record<string, unknown>[] } | null {
  const envelope = asTypedEnvelope(value);
  if (envelope?.type === "dataframe") {
    const rows = typedRecords(envelope);
    return rows ? { label: "DataFrame", rows } : null;
  }
  const display = typedDisplayValue(value);
  if (display !== value) return findRecordList(display);
  if (isRecordList(value)) return { label: null, rows: value };
  if (!isPlainRecord(value)) return null;

  for (const key of ["records", "rows", "items", "data", "results"]) {
    const candidate = value[key];
    if (isRecordList(candidate)) return { label: key, rows: candidate };
  }
  return null;
}

function formatCell(value: unknown): string {
  const artifact = asArtifactRef(value);
  if (artifact) return `Artifact: ${artifact.name} (${formatBytes(artifact.size_bytes)})`;
  const typed = formatTypedCell(value);
  if (typed) return typed;
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function valueSummary(value: unknown): string {
  const dataset = asDatasetRef(value);
  if (dataset) return `Dataset · ${datasetSummary(dataset)}`;
  const artifact = asArtifactRef(value);
  if (artifact) return `Artifact · ${artifactSummary(artifact)}`;
  const envelope = asTypedEnvelope(value);
  if (envelope) return `${typedLabel(envelope)} · ${typedSummary(envelope)}`;
  const table = findRecordList(value);
  if (table) return `${table.rows.length} row${table.rows.length === 1 ? "" : "s"}`;
  if (Array.isArray(value)) return `${value.length} item${value.length === 1 ? "" : "s"}`;
  if (isPlainRecord(value)) {
    const count = Object.keys(value).length;
    return `${count} key${count === 1 ? "" : "s"}`;
  }
  if (typeof value === "string") return `${value.length} char${value.length === 1 ? "" : "s"}`;
  if (value === null) return "null";
  return typeof value;
}

function edgeLabel(edge: Edge | undefined, direction: "input" | "output"): string {
  if (!edge) return direction === "input" ? "not connected" : "no connection";
  if (direction === "input") {
    return `${edge.source}.${edge.sourceHandle ?? "main"}`;
  }
  return `${edge.target}.${edge.targetHandle ?? "input"}`;
}

function edgeSummary(
  edges: Edge[],
  direction: "input" | "output",
): string {
  if (edges.length === 0) return direction === "input" ? "not connected" : "no connection";
  if (edges.length === 1) return edgeLabel(edges[0], direction);
  return `${edges.length} connection${edges.length === 1 ? "" : "s"}`;
}

function isMemoryNode(node: NoodleNode): boolean {
  const manifest = node.data.manifest;
  if (!manifest) return false;
  const label = `${manifest.id} ${manifest.name}`.toLowerCase();
  return (
    label.includes("memory") ||
    manifest.outputs.some((port) => port.data_kind === "ai_memory")
  );
}

function MemorySummary({
  node,
  outputs,
}: {
  node: NoodleNode;
  outputs: unknown;
}) {
  const memoryPort =
    node.data.manifest.outputs.find((port) => port.data_kind === "ai_memory") ??
    node.data.manifest.outputs.find((port) => port.name.toLowerCase().includes("memory"));
  const memoryValue = memoryPort ? valueAtPort(outputs, memoryPort.name) : null;
  const typed = memoryValue?.hasValue ? asTypedEnvelope(memoryValue.value) : null;
  const systemPrompt = String(node.data.params.system_prompt ?? "").trim();
  const windowSize = node.data.params.window;
  const adapterLabel =
    typed?.python_type ||
    (memoryValue?.hasValue ? valueSummary(memoryValue.value) : node.data.manifest.name);

  return (
    <section className="port-data-memory" aria-label="Selected memory node summary">
      <div className="port-data-memory-head">
        <span className="port-data-kind memory">memory</span>
        <strong>{node.data.manifest.name}</strong>
        <span>{memoryPort ? `${memoryPort.name} port` : "memory supplier"}</span>
      </div>
      <div className="port-data-memory-grid">
        <span>
          <strong>Adapter</strong>
          {adapterLabel}
        </span>
        <span>
          <strong>Window</strong>
          {windowSize === null || windowSize === undefined || windowSize === ""
            ? "default"
            : String(windowSize)}
        </span>
        <span>
          <strong>Seed</strong>
          {systemPrompt ? `${systemPrompt.length} chars` : "none"}
        </span>
        <span>
          <strong>State</strong>
          {memoryValue?.hasValue ? "materialized" : "run to inspect"}
        </span>
      </div>
      {typed?.repr && <pre className="port-data-memory-repr">{typed.repr}</pre>}
    </section>
  );
}

function DatasetCard({ dataset }: { dataset: DatasetRef }) {
  const [sqlOpen, setSqlOpen] = useState(false);
  const columns = dataset.schema?.map((s) => s.name) ?? [];
  const previewRows = dataset.preview ?? [];
  const shownColumns = columns.slice(0, 6);
  const hiddenColumns = columns.length - shownColumns.length;
  const shownRows = previewRows.slice(0, 5);
  return (
    <div className="port-data-artifact-preview">
      <div className="artifact-card-head">
        <span className="artifact-badge">Dataset</span>
        <strong>{dataset.artifact.name}</strong>
      </div>
      <div className="artifact-card-meta">
        <span>{datasetSummary(dataset)}</span>
        <span>{dataset.format}</span>
      </div>
      <div className="dataset-card-actions">
        <a href={datasetDownloadUrl(dataset)}>Download {dataset.format}</a>
        <button type="button" className="btn btn-sm dataset-sql-btn" onClick={() => setSqlOpen(true)}>
          Open SQL explorer
        </button>
      </div>
      {shownRows.length > 0 && (
        <div className="port-data-table-preview">
          <div className="port-data-table-label">Preview</div>
          <div className="port-data-table-wrap">
            <table>
              <thead>
                <tr>
                  {shownColumns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                  {hiddenColumns > 0 && <th>+{hiddenColumns}</th>}
                </tr>
              </thead>
              <tbody>
                {shownRows.map((row, index) => (
                  <tr key={index}>
                    {shownColumns.map((column) => (
                      <td key={column} title={formatCell(row[column])}>
                        {formatCell(row[column])}
                      </td>
                    ))}
                    {hiddenColumns > 0 && <td />}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="port-data-hint muted">
        Keep DatasetRef wires purple for big tables. Use <strong>Dataset To Records</strong> only when a later node needs inline rows.
      </div>
      {sqlOpen && <DatasetSqlModal dataset={dataset} onClose={() => setSqlOpen(false)} />}
    </div>
  );
}

function PortArtifactMedia({
  refValue,
}: {
  refValue: NonNullable<ReturnType<typeof asArtifactRef>>;
}) {
  const kind = artifactMediaKind(refValue);
  const inlineUrl = artifactInlineUrl(refValue);
  if (kind === "image")
    return (
      <img className="artifact-media-image" src={inlineUrl} alt={refValue.name} />
    );
  if (kind === "pdf")
    return (
      <iframe className="artifact-media-pdf" src={inlineUrl} title={refValue.name} />
    );
  if (kind === "audio")
    return <audio className="artifact-media-audio" controls src={inlineUrl} />;
  if (kind === "video")
    return <video className="artifact-media-video" controls src={inlineUrl} />;
  return null;
}

function PortValuePreview({ value }: { value: unknown }) {
  const chart = asChartRef(value);
  if (chart) return <ChartView chart={chart} />;
  const report = asReportRef(value);
  if (report) return <ReportView report={report} />;
  const dataset = asDatasetRef(value);
  if (dataset) return <DatasetCard dataset={dataset} />;
  const artifact = asArtifactRef(value);
  if (artifact) {
    const previewTable = findRecordList(artifact.preview);
    return (
      <div className="port-data-artifact-preview">
        <div className="artifact-card-head">
          <span className="artifact-badge">Artifact</span>
          <strong>{artifact.name}</strong>
        </div>
        <div className="artifact-card-meta">
          <span>{artifactSummary(artifact)}</span>
          <span>{artifact.content_type}</span>
        </div>
        <a href={artifactDownloadUrl(artifact)}>Download</a>
        <PortArtifactMedia refValue={artifact} />
        {previewTable && (
          <div className="port-data-table-preview">
            <div className="port-data-table-label">Preview</div>
            <div className="port-data-table-wrap">
              <table>
                <tbody>
                  {previewTable.rows.slice(0, 3).map((row, index) => (
                    <tr key={index}>
                      {Object.keys(row).slice(0, 4).map((column) => (
                        <td key={column}>{formatCell(row[column])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    );
  }
  const envelope = asTypedEnvelope(value);
  const table = findRecordList(value);
  if (!table) {
    if (envelope) {
      return (
        <div className="port-data-typed-preview">
          <span className="typed-badge">{typedLabel(envelope)}</span>
          <span>{typedSummary(envelope)}</span>
          {envelope.restorable === false && <span>not restorable</span>}
          {envelope.type === "object" && envelope.repr && (
            <pre className="port-data-json">{envelope.repr}</pre>
          )}
        </div>
      );
    }
    return <pre className="port-data-json">{pretty(value)}</pre>;
  }

  const columns = Array.from(new Set(table.rows.flatMap((row) => Object.keys(row))));
  const shownColumns = columns.slice(0, 6);
  const shownRows = table.rows.slice(0, 4);
  const hiddenColumns = columns.length - shownColumns.length;
  const hiddenRows = table.rows.length - shownRows.length;

  return (
    <div className="port-data-table-preview">
      {table.label && (
        <div className="port-data-table-label">
          {table.label === "DataFrame" ? (
            <>
              <span className="typed-badge">DataFrame</span>
              {envelope && <span>{typedSummary(envelope)}</span>}
            </>
          ) : (
            table.label
          )}
        </div>
      )}
      <div className="port-data-table-wrap">
        <table>
          <thead>
            <tr>
              {shownColumns.map((column) => (
                <th key={column}>{column}</th>
              ))}
              {hiddenColumns > 0 && <th>+{hiddenColumns}</th>}
            </tr>
          </thead>
          <tbody>
            {shownRows.map((row, index) => (
              <tr key={index}>
                {shownColumns.map((column) => (
                  <td key={column} title={formatCell(row[column])}>
                    {formatCell(row[column])}
                  </td>
                ))}
                {hiddenColumns > 0 && <td />}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hiddenRows > 0 && (
        <div className="port-data-table-more">+{hiddenRows} more rows</div>
      )}
    </div>
  );
}

function PortCard({
  name,
  direction,
  connectedEdges,
  inputConnections,
  value,
  hasValue,
  pinned,
}: {
  name: string;
  direction: "input" | "output";
  connectedEdges: Edge[];
  inputConnections?: PortConnectionPreview[];
  value: unknown;
  hasValue: boolean;
  pinned?: boolean;
}) {
  const connections = inputConnections ?? [];
  const connectionWithValue = connections.find((connection) => connection.hasValue);
  const primaryValue = connectionWithValue?.value ?? value;
  const hasPreviewValue = hasValue || Boolean(connectionWithValue);
  const [open, setOpen] = useState(hasPreviewValue || Boolean(pinned));
  useEffect(() => {
    if (hasPreviewValue || pinned) setOpen(true);
  }, [hasPreviewValue, pinned]);
  const summary = hasPreviewValue
    ? valueSummary(primaryValue)
    : edgeSummary(connectedEdges, direction);
  const className = [
    "port-data-card",
    hasPreviewValue ? "has-data" : "is-empty",
    connectedEdges.length > 0 ? "is-connected" : "is-disconnected",
  ].join(" ");

  return (
    <details
      className={className}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="port-data-card-head">
        <span className="port-data-caret" aria-hidden>›</span>
        <span className={`port-data-kind ${direction}`}>{direction}</span>
        <span className="port-data-name">{name}</span>
        {hasPreviewValue && <span className="port-data-badge data">data</span>}
        <span className="port-data-badge meta">{summary}</span>
        {connectedEdges.length > 1 && (
          <span className="port-data-badge">{connectedEdges.length} wires</span>
        )}
        {pinned && <span className="port-data-badge">pinned</span>}
      </summary>
      <div className="port-data-card-body">
        <div className="port-data-wire">{edgeSummary(connectedEdges, direction)}</div>
        {connectedEdges.length > 1 && (
          <div className="port-data-connection-list">
            {connectedEdges.map((edge) => (
              <span key={edge.id}>
                {direction === "input"
                  ? `${edge.source}.${edge.sourceHandle ?? "main"}`
                  : `${edge.target}.${edge.targetHandle ?? "input"}`}
              </span>
            ))}
          </div>
        )}
        {direction === "input" && connections.length > 1 ? (
          <div className="port-data-connection-previews">
            {connections.map((connection) => (
              <div className="port-data-connection-preview" key={connection.edge.id}>
                <div className="port-data-wire">
                  {connection.edge.source}.{connection.edge.sourceHandle ?? "main"}
                </div>
                {connection.hasValue ? (
                  <PortValuePreview value={connection.value} />
                ) : (
                  <p className="port-data-empty muted">No data on this wire yet.</p>
                )}
              </div>
            ))}
          </div>
        ) : hasPreviewValue ? (
          <PortValuePreview value={primaryValue} />
        ) : (
          <p className="port-data-empty muted">
            {connectedEdges.length > 0
              ? "No data on this port yet."
              : direction === "input"
                ? "This input has no wire."
                : "This output has no downstream wire."}
          </p>
        )}
      </div>
    </details>
  );
}

function clampHeight(value: number): number {
  return Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, value));
}

export function PortDataViewer() {
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [collapsed, setCollapsed] = useState(false);
  const dragStart = useRef<{ y: number; height: number } | null>(null);
  const selectedId = useEditor((s) => s.selectedId);
  // Manifest-less nodes (metanode boundary bars) aren't inspectable data nodes —
  // treat them as no-selection so the panel never reads an absent manifest.
  const node = useEditor((s) => {
    const found = s.nodes.find((n) => n.id === selectedId);
    return found?.data.manifest ? found : undefined;
  });
  const edges = useEditor((s) => s.edges);
  const runOutputs = useEditor((s) => s.runOutputs);
  const runStatus = useEditor((s) => (selectedId ? s.runStatus[selectedId] : null));
  const pinned = useEditor((s) => (selectedId ? s.pinned[selectedId] : undefined));
  const compactEmpty = !node && !collapsed;
  const effectiveHeight = collapsed
    ? COLLAPSED_HEIGHT
    : compactEmpty
      ? EMPTY_HEIGHT
      : height;

  useEffect(() => {
    document.documentElement.style.setProperty(
      "--port-data-height-global",
      `${effectiveHeight}px`,
    );
  }, [effectiveHeight]);

  useEffect(() => {
    function onMove(event: MouseEvent) {
      if (!dragStart.current) return;
      const delta = dragStart.current.y - event.clientY;
      setHeight(clampHeight(dragStart.current.height + delta));
    }

    function onUp() {
      if (!dragStart.current) return;
      dragStart.current = null;
      document.body.classList.remove("is-resizing-vertical");
    }

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.classList.remove("is-resizing-vertical");
    };
  }, []);

  function startResize(event: ReactMouseEvent<HTMLDivElement>) {
    if (collapsed || compactEmpty) return;
    dragStart.current = { y: event.clientY, height };
    document.body.classList.add("is-resizing-vertical");
    event.preventDefault();
  }

  const inputPorts = node?.data.manifest?.inputs.map((p) => p.name) ?? [];
  const outputs = node && pinned !== undefined ? pinned.payload : node ? runOutputs[node.id] : undefined;
  const selectedOutputNames = node?.data.manifest ? outputNames(node) : [];
  const dataPortCount = selectedOutputNames.filter(
    (port) => valueAtPort(outputs, port).hasValue,
  ).length;

  return (
    <aside
      className={[
        "port-data-viewer",
        collapsed ? "is-collapsed" : "",
        compactEmpty ? "is-empty" : "",
      ].filter(Boolean).join(" ")}
      style={
        {
          "--port-data-height": `${effectiveHeight}px`,
        } as CSSProperties
      }
    >
      <div
        className="port-data-resize"
        role="separator"
        aria-orientation="horizontal"
        title="Drag to resize"
        onMouseDown={startResize}
      />
      <header className="port-data-head">
        <div>
          <h2>Port Data</h2>
          <span>
            {node?.data.manifest ? `${node.data.manifest.name} · ${node.id}` : "Select a node to inspect live port data"}
          </span>
        </div>
        <div className="port-data-head-right">
          {node && (
            <div className="port-data-stats" aria-label="Selected node port summary">
              <span>{inputPorts.length} in</span>
              <span>{selectedOutputNames.length} out</span>
              <span>{dataPortCount} with data</span>
            </div>
          )}
          {runStatus && (
            <span className={`run-pill status-run-${runStatus}`}>
              {runStatus === "running" && <span className="node-spinner" />}
              {runStatus}
            </span>
          )}
          <button
            className="port-data-collapse"
            type="button"
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? "Expand" : "Collapse"}
          </button>
        </div>
      </header>

      {!node ? (
        <div className="port-data-empty-state">
          <strong>No node selected</strong>
          <span>Click a node to inspect every input and exit port in this panel.</span>
        </div>
      ) : (
        <>
          {isMemoryNode(node) && <MemorySummary node={node} outputs={outputs} />}
          <div className="port-data-columns">
            <section className="port-data-column">
              <div className="port-data-column-head">
                <span>Input ports</span>
                <span>{inputPorts.length}</span>
              </div>
              {inputPorts.length === 0 ? (
                <p className="port-data-empty muted">This node has no input ports.</p>
              ) : (
                inputPorts.map((port) => {
                  const incoming = edges.filter(
                    (edge) =>
                      edge.target === node.id && (edge.targetHandle ?? "input") === port,
                  );
                  const inputConnections = incoming.map((edge) => {
                    const upstream = runOutputs[edge.source];
                    const sourcePort = edge.sourceHandle ?? "main";
                    const { value, hasValue } = valueAtPort(upstream, sourcePort);
                    return { edge, value, hasValue };
                  });
                  const firstValue =
                    inputConnections.find((connection) => connection.hasValue) ??
                    inputConnections[0];
                  return (
                    <PortCard
                      key={port}
                      name={port}
                      direction="input"
                      connectedEdges={incoming}
                      inputConnections={inputConnections}
                      value={firstValue?.value}
                      hasValue={Boolean(firstValue?.hasValue)}
                    />
                  );
                })
              )}
            </section>

            <section className="port-data-column">
              <div className="port-data-column-head">
                <span>Exit ports</span>
                <span>{selectedOutputNames.length}</span>
              </div>
              {selectedOutputNames.map((port) => {
                const outgoing = edges.filter(
                  (edge) =>
                    edge.source === node.id && (edge.sourceHandle ?? "main") === port,
                );
                const { value, hasValue } = valueAtPort(outputs, port);
                return (
                  <PortCard
                    key={port}
                    name={port}
                    direction="output"
                    connectedEdges={outgoing}
                    value={value}
                    hasValue={hasValue}
                    pinned={pinned !== undefined}
                  />
                );
              })}
            </section>
          </div>
        </>
      )}
    </aside>
  );
}
