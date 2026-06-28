import { ArrowLineDown, ArrowLineUp } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import type { NodeVariableInfo } from "../types";
import type { TokenUsage } from "./store";
import { estimateCost, formatCost, shortModelLabel } from "./tokenCost";
import {
  artifactDownloadUrl,
  artifactInlineUrl,
  artifactMediaKind,
  artifactSummary,
  asArtifactRef,
  formatBytes,
} from "./artifactValues";
import { asDatasetRef } from "./datasetValues";
import type { DatasetRef } from "./datasetValues";
import { datasetDownloadUrl } from "./datasetValues";
import { DatasetSqlModal } from "./DatasetSqlModal";
import { asChartRef, asReportRef } from "./chartValues";
import { ChartView } from "./ChartView";
import { ReportView } from "./ReportView";
import {
  asTypedEnvelope,
  formatTypedCell,
  typedDisplayValue,
  typedLabel,
  typedRecords,
  typedSummary,
  typedValueBody,
} from "./typedValues";

/**
 * Side panel of the NDV (Input or Output). Shows the value as either a
 * tree-view JSON browser or, when the data is shaped like a list of records,
 * a tabular view. Auto-unwraps single-output ports so the viewer sees the
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
  if (asArtifactRef(value)) return false;
  const dataset = asDatasetRef(value);
  if (dataset) return (dataset.preview ?? []).length > 0;
  const envelope = asTypedEnvelope(value);
  if (envelope?.type === "dataframe" && typedRecords(envelope)) return true;
  const display = typedDisplayValue(value);
  return (
    isListOfRecords(display) ||
    findNestedRecordList(display) !== null ||
    isPlainObject(display)
  );
}

function formatCell(value: unknown): string {
  const artifact = asArtifactRef(value);
  if (artifact) return `Artifact: ${artifact.name} (${formatBytes(artifact.size_bytes)})`;
  const typed = formatTypedCell(value);
  if (typed) return typed;
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

// Hard cap on the rendered JSON string. The backend caps payloads, but a
// pathological object would still freeze the main thread in `JSON.stringify`
// and balloon the DOM — clamp the rendered text so the panel stays responsive.
const MAX_JSON_CHARS = 100_000;

// Virtualization thresholds for the interactive tree view:
// - Strings longer than this get a "Show more" toggle.
// - Per-level item count above this gets a "Show all N" expander.
const TRUNCATE_STRING_LENGTH = 200;
const MAX_ITEMS_PER_LEVEL = 50;

/* ── Truncated long string with Show more toggle ── */

function TruncatedJsonString({ text }: { text: string }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  if (expanded) {
    return (
      <>
        <span className="json-tree-string">{JSON.stringify(text)}</span>
        <button
          type="button"
          className="json-tree-toggle"
          onClick={() => setExpanded(false)}
        >
          Show less
        </button>
      </>
    );
  }
  const truncated = text.slice(0, TRUNCATE_STRING_LENGTH);
  // Render as a JSON string with ellipsis and closing quote
  const display = JSON.stringify(truncated).replace(/"$/, '…"');
  return (
    <>
      <span className="json-tree-string">{display}</span>
      <button
        type="button"
        className="json-tree-toggle"
        onClick={() => setExpanded(true)}
      >
        Show more ({text.length.toLocaleString()} chars)
      </button>
    </>
  );
}

export type CoerceTarget = "string" | "number" | "boolean" | "json";

export function coercePreview(value: unknown, target: CoerceTarget): unknown {
  // Flatten single-output wrapper first (mirrors unwrapSingleOutput)
  const v = value !== null && typeof value === "object" && !Array.isArray(value)
    ? (() => {
        const keys = Object.keys(value as object);
        return keys.length === 1 ? (value as Record<string, unknown>)[keys[0]] : value;
      })()
    : value;
  const s = v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
  if (target === "string") return s;
  if (target === "number") { const n = Number(s); return isNaN(n) ? "NaN" : n; }
  if (target === "boolean") {
    if (s === "" || s === "0" || s.toLowerCase() === "false" || s.toLowerCase() === "no") return false;
    return true;
  }
  if (target === "json") {
    try { return JSON.parse(s); }
    catch { return "[not serialisable]"; }
  }
  return v;
}

function pretty(value: unknown): string {
  let text: string | undefined;
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
  // `JSON.stringify` returns `undefined` (not a string, and without throwing)
  // for a top-level `undefined`/function/symbol — e.g. an unrun node whose
  // Input/Output panel data is still `undefined`. Guard before reading
  // `.length` so the panel renders its empty state instead of crashing.
  if (text === undefined) return value === undefined ? "" : String(value);
  if (text.length > MAX_JSON_CHARS) {
    return (
      text.slice(0, MAX_JSON_CHARS) +
      `\n\n… output truncated (${text.length.toLocaleString()} chars). Open the full artifact to see everything.`
    );
  }
  return text;
}

function findHtmlPreview(value: unknown): string | null {
  const display = typedDisplayValue(value);
  if (!isPlainObject(display)) return null;
  const mode = String(display.body_format ?? display.content_type ?? "").toLowerCase();
  for (const key of ["html_preview", "body_html", "html"]) {
    const candidate = display[key];
    if (typeof candidate === "string" && candidate.trim()) return candidate;
  }
  if (mode.includes("html") && typeof display.body === "string") {
    return display.body;
  }
  return null;
}

function copyText(text: string): void {
  void navigator.clipboard.writeText(text);
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

function findNestedRecordList(
  value: unknown,
): { key: string; rows: Record<string, unknown>[] } | null {
  if (!isPlainObject(value)) return null;
  const preferred = ["records", "rows", "items", "data", "results"];
  for (const key of preferred) {
    const candidate = value[key];
    if (isListOfRecords(candidate)) return { key, rows: candidate };
  }
  const tableEntries = Object.entries(value).filter(([, candidate]) =>
    isListOfRecords(candidate),
  );
  if (tableEntries.length !== 1) return null;
  const [key, rows] = tableEntries[0];
  return { key, rows: rows as Record<string, unknown>[] };
}

function primitiveClass(value: unknown): string {
  if (value === null) return "json-tree-null";
  return `json-tree-${typeof value}`;
}

function JsonTreeValue({
  value,
  path,
  dragPrefix,
  depth,
}: {
  value: unknown;
  path: (string | number)[];
  dragPrefix?: string;
  depth: number;
}): JSX.Element {
  const envelope = asTypedEnvelope(value);
  const artifact = asArtifactRef(value);
  if (artifact) {
    return (
      <ArtifactInlineValue
        refValue={artifact}
        path={path}
        dragPrefix={dragPrefix}
      />
    );
  }
  if (envelope) {
    return (
      <TypedInlineValue
        envelope={envelope}
        path={path}
        dragPrefix={dragPrefix}
      />
    );
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="json-tree-empty">[]</span>;
    }
    return (
      <JsonTreeValueArray
        items={value}
        path={path}
        dragPrefix={dragPrefix}
        depth={depth}
      />
    );
  }
  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) {
      return <span className="json-tree-empty">{"{}"}</span>;
    }
    return (
      <JsonTreeValueObject
        entries={entries}
        path={path}
        dragPrefix={dragPrefix}
        depth={depth}
      />
    );
  }
  // Primitive value — truncate long strings
  if (typeof value === "string" && value.length > TRUNCATE_STRING_LENGTH) {
    return <TruncatedJsonString text={value} />;
  }
  return (
    <span className={`json-tree-prim ${primitiveClass(value)}`}>
      {value === null ? "null" : JSON.stringify(value)}
    </span>
  );
}

/* ── Array block with per-level item limit ── */

function JsonTreeValueArray({
  items,
  path,
  dragPrefix,
  depth,
}: {
  items: unknown[];
  path: (string | number)[];
  dragPrefix?: string;
  depth: number;
}): JSX.Element {
  const [showAll, setShowAll] = useState(false);
  const limited = !showAll && items.length > MAX_ITEMS_PER_LEVEL;
  const displayed = limited ? items.slice(0, MAX_ITEMS_PER_LEVEL) : items;

  return (
    <div className="json-tree-block">
      {displayed.map((item, i) => (
        <JsonTreeRow
          key={i}
          label={String(i)}
          value={item}
          path={[...path, i]}
          dragPrefix={dragPrefix}
          depth={depth + 1}
        />
      ))}
      {limited && (
        <button
          type="button"
          className="json-tree-expand-all"
          onClick={() => setShowAll(true)}
        >
          Show all {items.length} items
        </button>
      )}
    </div>
  );
}

/* ── Object block with per-level key limit ── */

function JsonTreeValueObject({
  entries,
  path,
  dragPrefix,
  depth,
}: {
  entries: [string, unknown][];
  path: (string | number)[];
  dragPrefix?: string;
  depth: number;
}): JSX.Element {
  const [showAll, setShowAll] = useState(false);
  const limited = !showAll && entries.length > MAX_ITEMS_PER_LEVEL;
  const displayed = limited ? entries.slice(0, MAX_ITEMS_PER_LEVEL) : entries;

  return (
    <div className="json-tree-block">
      {displayed.map(([k, v]) => (
        <JsonTreeRow
          key={k}
          label={k}
          value={v}
          path={[...path, k]}
          dragPrefix={dragPrefix}
          depth={depth + 1}
        />
      ))}
      {limited && (
        <button
          type="button"
          className="json-tree-expand-all"
          onClick={() => setShowAll(true)}
        >
          Show all {entries.length} items
        </button>
      )}
    </div>
  );
}

function ArtifactMediaPreview({
  refValue,
}: {
  refValue: NonNullable<ReturnType<typeof asArtifactRef>>;
}) {
  const kind = artifactMediaKind(refValue);
  const [textBody, setTextBody] = useState<string | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const inlineUrl = artifactInlineUrl(refValue);

  useEffect(() => {
    if (kind !== "text") return;
    let cancelled = false;
    setTextBody(null);
    setTextError(null);
    fetch(inlineUrl)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((body) => {
        if (!cancelled) setTextBody(body.slice(0, 20000));
      })
      .catch((err) => {
        if (!cancelled) setTextError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [inlineUrl, kind]);

  if (kind === "none") return null;
  return (
    <div className="artifact-media">
      {kind === "image" && (
        <img className="artifact-media-image" src={inlineUrl} alt={refValue.name} />
      )}
      {kind === "pdf" && (
        <iframe
          className="artifact-media-pdf"
          src={inlineUrl}
          title={refValue.name}
        />
      )}
      {kind === "audio" && (
        <audio className="artifact-media-audio" controls src={inlineUrl} />
      )}
      {kind === "video" && (
        <video className="artifact-media-video" controls src={inlineUrl} />
      )}
      {kind === "text" && (
        <pre className="artifact-media-text">
          {textError
            ? `Could not load preview: ${textError}`
            : textBody ?? "Loading…"}
        </pre>
      )}
    </div>
  );
}

function ArtifactInlineValue({
  refValue,
  path,
  dragPrefix,
}: {
  refValue: NonNullable<ReturnType<typeof asArtifactRef>>;
  path: (string | number)[];
  dragPrefix?: string;
}) {
  const expr = dragPrefix ? buildExpression(dragPrefix, path) : undefined;
  return (
    <span
      className="artifact-inline"
      draggable={Boolean(expr)}
      onDragStart={expr ? (e) => startExpressionDrag(e, expr) : undefined}
      title={expr ? `Drag to insert ${expr}` : undefined}
    >
      <span className="artifact-badge">Artifact</span>
      <span className="artifact-name">{refValue.name}</span>
      <span className="typed-meta">{artifactSummary(refValue)}</span>
      <a
        href={artifactDownloadUrl(refValue)}
        onClick={(event) => event.stopPropagation()}
      >
        Download
      </a>
    </span>
  );
}

function TypedInlineValue({
  envelope,
  path,
  dragPrefix,
}: {
  envelope: NonNullable<ReturnType<typeof asTypedEnvelope>>;
  path: (string | number)[];
  dragPrefix?: string;
}) {
  const expr =
    dragPrefix && envelope.type !== "dataframe"
      ? buildExpression(dragPrefix, [...path, "value"])
      : undefined;
  const display =
    envelope.type === "object"
      ? envelope.repr ?? typedSummary(envelope)
      : typedSummary(envelope);
  return (
    <span
      className="typed-inline"
      draggable={Boolean(expr)}
      onDragStart={expr ? (e) => startExpressionDrag(e, expr) : undefined}
      title={expr ? `Drag to insert ${expr}` : undefined}
    >
      <span className="typed-badge">{typedLabel(envelope)}</span>
      <span className="typed-meta">{display}</span>
      {envelope.restorable === false && (
        <span className="typed-muted">not restorable</span>
      )}
    </span>
  );
}

function JsonTreeRow({
  label,
  value,
  path,
  dragPrefix,
  depth,
}: {
  label: string;
  value: unknown;
  path: (string | number)[];
  dragPrefix?: string;
  depth: number;
}) {
  const expandable =
    (Array.isArray(value) && value.length > 0) ||
    (isPlainObject(value) &&
      Object.keys(value as Record<string, unknown>).length > 0);
  const [collapsed, setCollapsed] = useState(depth >= 2);
  const draggable = Boolean(dragPrefix);
  const expr = dragPrefix ? buildExpression(dragPrefix, path) : undefined;

  let summary: string | null = null;
  if (collapsed && expandable) {
    if (Array.isArray(value)) {
      summary = `[${value.length} item${value.length !== 1 ? "s" : ""}]`;
    } else {
      const keys = Object.keys(value as Record<string, unknown>);
      summary = `{${keys.length} key${keys.length !== 1 ? "s" : ""}}`;
    }
  }

  return (
    <div className="json-tree-row">
      {expandable ? (
        <button
          type="button"
          className="json-tree-twist"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? "Expand" : "Collapse"}
          aria-expanded={!collapsed}
        >
          {collapsed ? "▸" : "▾"}
        </button>
      ) : (
        <span className="json-tree-twist-empty" aria-hidden />
      )}
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
      {expr && (
        <button
          type="button"
          className="json-tree-copy"
          title={`Copy ${expr}`}
          onClick={() => copyText(expr)}
        >
          copy
        </button>
      )}
      <span className="json-tree-colon">:</span>
      {collapsed && expandable ? (
        <span className="json-tree-summary">{summary}</span>
      ) : (
        <JsonTreeValue value={value} path={path} dragPrefix={dragPrefix} depth={depth + 1} />
      )}
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
      <JsonTreeValue value={data} path={[]} dragPrefix={dragPrefix} depth={0} />
    </div>
  );
}

function schemaRawType(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value; // "string" | "number" | "boolean" | "object"
}

function schemaTypeIcon(value: unknown): string {
  const t = schemaRawType(value);
  if (t === "string") return "T";
  if (t === "number") return "#";
  if (t === "boolean") return "⊤";
  if (t === "array") return "[]";
  if (t === "object") return "{}";
  return "∅"; // null / undefined
}

function schemaValuePreview(value: unknown): string | null {
  if (value === null) return "null";
  if (value === undefined) return null;
  if (Array.isArray(value)) return null;
  if (typeof value === "object") return null;
  if (typeof value === "string") {
    return value.length > 40 ? value.slice(0, 40) + "…" : value;
  }
  return String(value);
}

// Keep schemaType for legacy display_name (still used in aria-label and title attributes)
function schemaType(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (Array.isArray(value)) return `array[${value.length}]`;
  if (typeof value === "object") return "object";
  return typeof value;
}

/**
 * One row of the n8n-style schema view: a draggable field name + type, with a
 * twist control for nested objects/arrays. Leaves carry the same drag payload
 * as the JSON/table views (`{{ <prefix>.<path> }}`).
 */
function SchemaRow({
  label,
  value,
  path,
  dragPrefix,
}: {
  label: string;
  value: unknown;
  path: (string | number)[];
  dragPrefix?: string;
}): JSX.Element {
  const expandable =
    isPlainObject(value) || (Array.isArray(value) && value.length > 0);
  const [open, setOpen] = useState(path.length <= 1);
  const expr = dragPrefix ? buildExpression(dragPrefix, path) : undefined;
  const rawType = schemaRawType(value);
  const icon = schemaTypeIcon(value);
  const preview = schemaValuePreview(value);

  return (
    <div className="schema-node">
      <div
        className="schema-row"
        draggable={Boolean(expr)}
        onDragStart={expr ? (e) => startExpressionDrag(e, expr) : undefined}
        title={expr ? `Drag to insert ${expr} (${schemaType(value)})` : schemaType(value)}
      >
        {expandable ? (
          <button
            type="button"
            className="schema-twist"
            onClick={() => setOpen((o) => !o)}
            aria-label={open ? "Collapse" : "Expand"}
          >
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="schema-twist-spacer" aria-hidden />
        )}
        {expr && (
          <span className="schema-grip" aria-hidden>
            ⠿
          </span>
        )}
        <span
          className="schema-type-icon"
          data-type={rawType}
          aria-label={schemaType(value)}
        >
          {icon}
        </span>
        <span className="schema-key">{label}</span>
        {preview !== null && (
          <span className="schema-value" data-type={rawType}>
            {preview}
          </span>
        )}
        {expandable && (
          <span className="schema-type">{schemaType(value)}</span>
        )}
      </div>
      {expandable && open && (
        <div className="schema-children">
          {isPlainObject(value)
            ? Object.entries(value).map(([k, v]) => (
                <SchemaRow
                  key={k}
                  label={k}
                  value={v}
                  path={[...path, k]}
                  dragPrefix={dragPrefix}
                />
              ))
            : Array.isArray(value) && value.length > 0
              ? (
                <SchemaRow
                  label="0"
                  value={value[0]}
                  path={[...path, 0]}
                  dragPrefix={dragPrefix}
                />
              )
              : null}
        </div>
      )}
    </div>
  );
}

function SchemaTree({
  data,
  dragPrefix,
}: {
  data: unknown;
  dragPrefix?: string;
}) {
  if (isListOfRecords(data)) {
    const keys = Array.from(new Set(data.flatMap((row) => Object.keys(row))));
    const first = data[0] as Record<string, unknown>;
    return (
      <div className="schema-tree">
        <div className="schema-arrayhint">
          {data.length} items · fields of each item
        </div>
        {keys.map((k) => (
          <SchemaRow
            key={k}
            label={k}
            value={first[k]}
            path={[k]}
            dragPrefix={dragPrefix}
          />
        ))}
      </div>
    );
  }
  if (isPlainObject(data)) {
    return (
      <div className="schema-tree">
        {Object.entries(data).map(([k, v]) => (
          <SchemaRow
            key={k}
            label={k}
            value={v}
            path={[k]}
            dragPrefix={dragPrefix}
          />
        ))}
      </div>
    );
  }
  if (Array.isArray(data) && data.length > 0) {
    return (
      <div className="schema-tree">
        <div className="schema-arrayhint">{data.length} items</div>
        <SchemaRow label="0" value={data[0]} path={[0]} dragPrefix={dragPrefix} />
      </div>
    );
  }
  return (
    <div className="schema-tree">
      <SchemaRow label="value" value={data} path={[]} dragPrefix={dragPrefix} />
    </div>
  );
}

const PAGE_SIZE = 100;

function colStats(
  data: Record<string, unknown>[],
  col: string,
): { min: number; max: number; mean: number } | null {
  const nums = data
    .map((r) => Number(r[col]))
    .filter((n) => !isNaN(n));
  if (nums.length === 0) return null;
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const mean = nums.reduce((a, b) => a + b, 0) / nums.length;
  return { min, max, mean };
}

interface ColumnProfile {
  count: number;
  nulls: number;
  distinct: number;
  numeric: { min: number; max: number; mean: number } | null;
  top: { value: string; count: number }[];
}

function columnProfile(
  data: Record<string, unknown>[],
  col: string,
): ColumnProfile {
  let nulls = 0;
  const counts = new Map<string, number>();
  const nums: number[] = [];
  for (const row of data) {
    const v = row[col];
    if (v === null || v === undefined || v === "") {
      nulls += 1;
      continue;
    }
    const key = typeof v === "object" ? JSON.stringify(v) : String(v);
    counts.set(key, (counts.get(key) ?? 0) + 1);
    const n = Number(v);
    if (!isNaN(n) && typeof v !== "boolean") nums.push(n);
  }
  const top = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([value, count]) => ({ value, count }));
  const numeric =
    nums.length > 0 && nums.length >= data.length - nulls
      ? {
          min: Math.min(...nums),
          max: Math.max(...nums),
          mean: nums.reduce((a, b) => a + b, 0) / nums.length,
        }
      : null;
  return {
    count: data.length,
    nulls,
    distinct: counts.size,
    numeric,
    top,
  };
}

function ColumnProfileCard({
  data,
  col,
  onClose,
}: {
  data: Record<string, unknown>[];
  col: string;
  onClose: () => void;
}) {
  const p = columnProfile(data, col);
  const pct = (n: number) =>
    p.count > 0 ? `${Math.round((n / p.count) * 100)}%` : "0%";
  return (
    <div className="col-profile-pop" onClick={(e) => e.stopPropagation()}>
      <div className="col-profile-head">
        <strong>{col}</strong>
        <button className="btn btn-xs btn-ghost" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      <div className="col-profile-grid">
        <span>rows</span>
        <span>{p.count.toLocaleString()}</span>
        <span>nulls</span>
        <span>
          {p.nulls.toLocaleString()} ({pct(p.nulls)})
        </span>
        <span>distinct</span>
        <span>{p.distinct.toLocaleString()}</span>
        {p.numeric && (
          <>
            <span>min</span>
            <span>{p.numeric.min.toPrecision(5)}</span>
            <span>max</span>
            <span>{p.numeric.max.toPrecision(5)}</span>
            <span>mean</span>
            <span>{p.numeric.mean.toPrecision(5)}</span>
          </>
        )}
      </div>
      {p.top.length > 0 && (
        <div className="col-profile-top">
          <div className="col-profile-top-head">Top values</div>
          {p.top.map((t) => (
            <div key={t.value} className="col-profile-top-row">
              <span className="col-profile-top-val" title={t.value}>
                {t.value || "∅"}
              </span>
              <span className="col-profile-top-bar">
                <span
                  style={{
                    width: `${Math.round((t.count / p.count) * 100)}%`,
                  }}
                />
              </span>
              <span className="col-profile-top-count">{t.count}</span>
            </div>
          ))}
        </div>
      )}
      <div className="col-profile-foot muted">from preview sample</div>
    </div>
  );
}


function RecordTable({
  data,
  dragPrefix,
  dtypes,
}: {
  data: Record<string, unknown>[];
  dragPrefix?: string;
  dtypes?: Record<string, string>;
}) {
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [profileCol, setProfileCol] = useState<string | null>(null);

  const columns = Array.from(new Set(data.flatMap((row) => Object.keys(row))));

  // Filter
  const lower = search.toLowerCase();
  const filtered = search
    ? data.filter((row) =>
        columns.some((col) =>
          formatCell(row[col]).toLowerCase().includes(lower),
        ),
      )
    : data;

  // Sort
  const sorted = sortCol
    ? [...filtered].sort((a, b) => {
        const av = formatCell(a[sortCol]);
        const bv = formatCell(b[sortCol]);
        const cmp = av < bv ? -1 : av > bv ? 1 : 0;
        return sortDir === "asc" ? cmp : -cmp;
      })
    : filtered;

  // Pagination
  const totalRows = sorted.length;
  const pageCount = Math.max(1, Math.ceil(totalRows / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const startIdx = safePage * PAGE_SIZE;
  const endIdx = Math.min(startIdx + PAGE_SIZE, totalRows);
  const pageRows = sorted.slice(startIdx, endIdx);

  function toggleSort(col: string) {
    if (sortCol === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
    setPage(0);
  }

  // For a list, each row's iteration sees $json as that record.
  // Headers carry $json.col; cells carry $json.col too — both yield the
  // same per-item path, which is what the user almost always wants.
  return (
    <div className="data-table-wrap">
      <div className="data-table-filter">
        <input
          type="search"
          placeholder="Search rows…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(0); }}
        />
        {search && (
          <span className="df-truncated">
            {filtered.length} / {data.length} rows
          </span>
        )}
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th className="data-table-index">#</th>
            {columns.map((col) => {
              const expr = dragPrefix
                ? buildExpression(dragPrefix, [col])
                : undefined;
              const dtype = dtypes?.[col];
              const isNumeric = dtype
                ? /int|float/i.test(dtype)
                : false;
              const stats = isNumeric ? colStats(data, col) : null;
              const sortIndicator =
                sortCol === col ? (sortDir === "asc" ? " ▲" : " ▼") : "";
              const tooltipParts: string[] = [];
              if (expr) tooltipParts.push(`Drag to insert ${expr}`);
              if (stats) {
                tooltipParts.push(
                  `min: ${stats.min.toPrecision(4)}  max: ${stats.max.toPrecision(4)}  mean: ${stats.mean.toPrecision(4)}`,
                );
              }
              const title = tooltipParts.join("\n") || undefined;
              const classes = [
                expr ? "draggable" : "",
                "sortable",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <th
                  key={col}
                  className={classes || undefined}
                  draggable={Boolean(expr)}
                  onDragStart={
                    expr ? (e) => startExpressionDrag(e, expr) : undefined
                  }
                  title={title}
                  onClick={() => toggleSort(col)}
                >
                  {expr && <span className="drag-grip" aria-hidden>⠿</span>}
                  {col}{sortIndicator}
                  {dtype && (
                    <span className="col-dtype">{dtype}</span>
                  )}
                  <button
                    type="button"
                    className="col-profile-btn"
                    title="Column stats"
                    onClick={(e) => {
                      e.stopPropagation();
                      setProfileCol((c) => (c === col ? null : col));
                    }}
                  >
                    ▾
                  </button>
                  {profileCol === col && (
                    <ColumnProfileCard
                      data={data}
                      col={col}
                      onClose={() => setProfileCol(null)}
                    />
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {pageRows.map((row, i) => (
            <tr key={startIdx + i}>
              <td className="data-table-index">{startIdx + i}</td>
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
      {totalRows > PAGE_SIZE && (
        <div className="data-table-pagination">
          <button
            type="button"
            disabled={safePage === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            ← Prev
          </button>
          <span>
            Showing rows {startIdx + 1}–{endIdx} of {totalRows}
          </span>
          <button
            type="button"
            disabled={safePage >= pageCount - 1}
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function DatasetTableView({
  dataset,
  dragPrefix,
}: {
  dataset: DatasetRef;
  dragPrefix?: string;
}) {
  const [sqlOpen, setSqlOpen] = useState(false);
  const rows = (dataset.preview ?? []) as Record<string, unknown>[];
  const dtypes = Object.fromEntries(
    (dataset.schema ?? []).map((c) => [c.name, c.type]),
  );
  const cols = dataset.column_count ?? dataset.schema?.length ?? 0;
  const totalRows = dataset.row_count;
  return (
    <div className="dataset-view">
      <div className="dataset-view-strip">
        <span className="dataset-badge">Dataset</span>
        <span className="dataset-stat">
          <strong>{totalRows == null ? "?" : totalRows.toLocaleString()}</strong> rows
        </span>
        <span className="dataset-stat">
          <strong>{cols}</strong> cols
        </span>
        <span className="dataset-stat">
          {formatBytes(dataset.artifact.size_bytes)}
        </span>
        <span className="dataset-stat dataset-format">{dataset.format}</span>
        <button
          type="button"
          className="btn btn-xs dataset-sql-btn"
          onClick={() => setSqlOpen(true)}
        >
          Open in SQL
        </button>
        <a
          className="dataset-dl"
          href={datasetDownloadUrl(dataset)}
          title="Download Parquet"
        >
          Download
        </a>
      </div>
      {rows.length === 0 ? (
        <div className="muted">Dataset has no preview rows.</div>
      ) : (
        <>
          <RecordTable data={rows} dragPrefix={dragPrefix} dtypes={dtypes} />
          {dataset.preview_truncated && (
            <div className="dataset-preview-note muted">
              Preview is a sample of the first {rows.length} rows.
            </div>
          )}
        </>
      )}
      {sqlOpen && (
        <DatasetSqlModal dataset={dataset} onClose={() => setSqlOpen(false)} />
      )}
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
  const dataset = asDatasetRef(data);
  if (dataset) {
    return <DatasetTableView dataset={dataset} dragPrefix={dragPrefix} />;
  }
  const artifact = asArtifactRef(data);
  if (artifact) {
    return (
      <div className="artifact-card">
        <div className="artifact-card-head">
          <span className="artifact-badge">Artifact</span>
          <strong>{artifact.name}</strong>
          <span>{artifactSummary(artifact)}</span>
        </div>
        <div className="artifact-card-meta">
          <span>{artifact.content_type}</span>
          {artifact.node_id && <span>node {artifact.node_id}</span>}
          {artifact.run_id && <span>run {artifact.run_id}</span>}
        </div>
        <a className="artifact-download" href={artifactDownloadUrl(artifact)}>
          Download
        </a>
        <ArtifactMediaPreview refValue={artifact} />
        {artifact.preview !== undefined && (
          <div className="artifact-preview">
            {isTableable(artifact.preview) ? (
              <DataTable data={artifact.preview} />
            ) : (
              <JsonTree data={artifact.preview} />
            )}
          </div>
        )}
      </div>
    );
  }
  const envelope = asTypedEnvelope(data);
  if (envelope?.type === "dataframe") {
    const records = typedRecords(envelope) ?? [];
    const body = typedValueBody(envelope);
    const dtypes = isPlainObject(body.dtypes) ? body.dtypes : {};
    return (
      <div className="data-table-composite">
        <div className="data-table-summary">
          <span className="typed-badge">DataFrame</span>
          <span>{typedSummary(envelope)}</span>
          {Object.entries(dtypes)
            .slice(0, 4)
            .map(([column, dtype]) => (
              <span key={column}>
                {column}: <strong>{String(dtype)}</strong>
              </span>
            ))}
          {Object.keys(dtypes).length > 4 && (
            <span>+{Object.keys(dtypes).length - 4} dtypes</span>
          )}
        </div>
        {records.length > 0 ? (
          <RecordTable
            data={records}
            dragPrefix={dragPrefix ? `${dragPrefix}.value.records` : undefined}
            dtypes={Object.fromEntries(
              Object.entries(dtypes).map(([k, v]) => [k, String(v)]),
            )}
          />
        ) : (
          <p className="ndv-panel-empty muted">No preview rows captured.</p>
        )}
      </div>
    );
  }
  const display = typedDisplayValue(data);
  if (display !== data) {
    return <DataTable data={display} dragPrefix={dragPrefix} />;
  }
  if (isListOfRecords(display)) {
    return <RecordTable data={display} dragPrefix={dragPrefix} />;
  }
  const nested = findNestedRecordList(display);
  if (nested && isPlainObject(display)) {
    const summary = Object.entries(display).filter(
      ([key, value]) => key !== nested.key && !Array.isArray(value),
    );
    return (
      <div className="data-table-composite">
        <div className="data-table-summary">
          <span>
            Showing <strong>{nested.key}</strong>
          </span>
          <span>{nested.rows.length} rows</span>
          {summary.slice(0, 4).map(([key, value]) => (
            <span key={key}>
              {key}: <strong>{formatCell(value)}</strong>
            </span>
          ))}
        </div>
        <RecordTable data={nested.rows} />
      </div>
    );
  }
  if (isPlainObject(display)) {
    return (
      <div className="data-table-wrap">
        <table className="data-table data-table-kv">
          <tbody>
            {Object.entries(display).map(([key, value]) => {
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
  return <pre className="data-json">{pretty(display)}</pre>;
}

function HtmlPreview({ html }: { html: string }) {
  return (
    <div className="html-preview">
      <iframe
        title="HTML output preview"
        sandbox=""
        srcDoc={html}
      />
    </div>
  );
}

function VariableExplorer({
  variables,
}: {
  variables: NodeVariableInfo[];
}) {
  if (variables.length === 0) {
    return <p className="ndv-panel-empty muted">No variables captured.</p>;
  }

  return (
    <div className="variable-explorer">
      {variables.map((variable) => (
        <section className="variable-card" key={variable.name}>
          <header className="variable-card-head">
            <div>
              <strong>{variable.name}</strong>
              <span>{variable.type}</span>
            </div>
            {variable.summary && <p>{variable.summary}</p>}
          </header>
          {variable.columns && variable.columns.length > 0 && (
            <div className="variable-columns">
              {variable.columns.slice(0, 12).map((column) => (
                <span key={column}>{column}</span>
              ))}
              {variable.columns.length > 12 && (
                <span>+{variable.columns.length - 12} more</span>
              )}
            </div>
          )}
          {isListOfRecords(variable.preview) ? (
            <RecordTable data={variable.preview} />
          ) : (
            <pre className="data-json variable-preview">
              {pretty(variable.preview ?? variable.summary ?? null)}
            </pre>
          )}
        </section>
      ))}
    </div>
  );
}

function formatLogTime(timestampSeconds?: number | null): string | null {
  if (typeof timestampSeconds !== "number") return null;
  const date = new Date(timestampSeconds * 1000);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function buildExecutionLog({
  logs,
  status,
  error,
  durationMs,
  startedAt,
  finishedAt,
}: {
  logs?: string[];
  status?: string | null;
  error?: string | null;
  durationMs?: number | null;
  startedAt?: number | null;
  finishedAt?: number | null;
}): string {
  const lines: string[] = [];
  const started = formatLogTime(startedAt);
  const finished = formatLogTime(finishedAt);

  if (started) {
    lines.push(`[${started}] started`);
  }

  if (status || finished || typeof durationMs === "number") {
    const statusText = status ? `finished: ${status}` : "finished";
    const durationText =
      typeof durationMs === "number" ? ` in ${durationMs} ms` : "";
    lines.push(
      `${finished ? `[${finished}] ` : ""}${statusText}${durationText}`,
    );
  }

  if (error) {
    lines.push("", "error:", error);
  }

  if (logs && logs.length > 0) {
    lines.push("", "stdout/stderr:", ...logs);
  } else if (error) {
    lines.push("", "No stdout/stderr logs were captured before the node failed.");
  } else if (status === "skipped") {
    lines.push("", "This node was skipped, so no logs were produced.");
  } else if (status === "running") {
    lines.push("", "This node is still running.");
  } else if (lines.length > 0) {
    lines.push("", "The node ran, but it did not print anything.");
  }

  return lines.join("\n") || "No execution details were captured for this node.";
}

export function DataPanel({
  title,
  data,
  emptyMessage,
  footer,
  dragPrefix,
  logs,
  error,
  status,
  variables,
  durationMs,
  startedAt,
  finishedAt,
  tokenUsage,
}: {
  title: string;
  data: unknown;
  emptyMessage?: string;
  footer?: ReactNode;
  dragPrefix?: string;
  logs?: string[];
  error?: string | null;
  status?: string | null;
  variables?: NodeVariableInfo[];
  durationMs?: number | null;
  startedAt?: number | null;
  finishedAt?: number | null;
  tokenUsage?: TokenUsage | null;
}) {
  const [coerce, setCoerce] = useState<CoerceTarget | "">("");
  useEffect(() => { setCoerce(""); }, [data]);

  const rawDisplay = unwrapSingleOutput(data);
  const display = coerce !== "" ? coercePreview(data, coerce) : rawDisplay;
  const chart = asChartRef(display);
  const report = asReportRef(display);
  const canVisual = Boolean(chart || report);
  const canTable = isTableable(display);
  const htmlPreview = findHtmlPreview(display);
  const canHtml = Boolean(htmlPreview);
  const hasLogStream = logs !== undefined;
  const hasLogs = hasLogStream && logs.length > 0;
  const hasVariables = Boolean(variables?.length);
  const executionLog = hasLogStream
    ? buildExecutionLog({
        logs,
        status,
        error,
        durationMs,
        startedAt,
        finishedAt,
      })
    : "";
  const empty = data === undefined || data === null;
  const canSchema =
    !empty &&
    (isPlainObject(display) || (Array.isArray(display) && display.length > 0));
  const [view, setView] = useState<
    "json" | "raw" | "table" | "html" | "logs" | "variables" | "visual" | "schema"
  >(
    canVisual
      ? "visual"
      : canHtml
        ? "html"
        : dragPrefix && canSchema
          ? "schema"
          : canTable
            ? "table"
            : "json",
  );
  let effectiveView:
    | "json"
    | "raw"
    | "table"
    | "html"
    | "logs"
    | "variables"
    | "visual"
    | "schema" = view;
  if (view === "visual" && !canVisual) effectiveView = canTable ? "table" : "json";
  if (view === "schema" && !canSchema) effectiveView = canTable ? "table" : "json";
  if (view === "table" && !canTable) effectiveView = "json";
  if (view === "html" && !canHtml) effectiveView = canTable ? "table" : "json";
  if (view === "logs" && !hasLogStream) {
    effectiveView = canHtml ? "html" : canTable ? "table" : "json";
  }
  if (view === "variables" && !hasVariables) {
    effectiveView = canHtml ? "html" : canTable ? "table" : "json";
  }

  useEffect(() => {
    if (error && hasLogStream && empty) setView("logs");
  }, [empty, error, hasLogStream]);
  useEffect(() => {
    // Auto-focus the visual view when a chart/report flows in.
    if (canVisual) {
      setView((current) =>
        current === "logs" || current === "variables" ? current : "visual",
      );
    }
  }, [canVisual]);
  useEffect(() => {
    if (htmlPreview) {
      setView((current) =>
        current === "logs" || current === "variables" || current === "visual"
          ? current
          : "html",
      );
    } else {
      setView((current) =>
        current === "html" ? (canTable ? "table" : "json") : current,
      );
    }
  }, [canTable, htmlPreview]);

  const copyPayload =
    effectiveView === "logs"
      ? executionLog
      : effectiveView === "variables"
        ? pretty(variables ?? [])
        : effectiveView === "html"
          ? htmlPreview ?? ""
          : pretty(display);
  const panelClassName = `ndv-panel${empty ? " ndv-panel-empty-data" : ""}`;
  // Direction cue + item count for the panel header. Count is only shown when
  // it can be derived unambiguously (a list of items).
  const isInput = title.toLowerCase().startsWith("input");
  const DirIcon = isInput ? ArrowLineDown : ArrowLineUp;
  const itemCount = Array.isArray(display) ? display.length : null;

  return (
    <section className={panelClassName}>
      <header className="ndv-panel-head">
        <h3>
          <DirIcon
            className="ndv-panel-dir"
            size={13}
            weight="bold"
            aria-hidden="true"
          />
          {title}
          <span className="ndv-dir-badge">{isInput ? "IN" : "OUT"}</span>
          {!empty && itemCount !== null && (
            <span className="ndv-count-badge">
              {itemCount} {itemCount === 1 ? "item" : "items"}
            </span>
          )}
          {typeof durationMs === "number" && (
            <span className="ndv-duration" title="Execution time">
              {durationMs} ms
            </span>
          )}
          {tokenUsage && (
            <span
              className="ndv-token-usage"
              title={`${tokenUsage.prompt_tokens} prompt + ${tokenUsage.completion_tokens} completion tokens`}
            >
              {tokenUsage.total_tokens.toLocaleString()} tok
              {tokenUsage.model && ` · ${shortModelLabel(tokenUsage.model) ?? tokenUsage.model}`}
              {" · "}
              {formatCost(estimateCost(tokenUsage))}
            </span>
          )}
        </h3>
        {!dragPrefix && !empty && (
          <select
            className="data-coerce-select"
            aria-label="Coerce output type"
            value={coerce}
            onChange={(e) => setCoerce(e.target.value as CoerceTarget | "")}
            title="Preview data coerced to a different type"
          >
            <option value="">as-is</option>
            <option value="string">→ string</option>
            <option value="number">→ number</option>
            <option value="boolean">→ boolean</option>
            <option value="json">→ JSON parse</option>
          </select>
        )}
        <div className="data-view-toggle">
          {canVisual && (
            <button
              type="button"
              className={effectiveView === "visual" ? "active" : ""}
              onClick={() => setView("visual")}
              title={report ? "Render report" : "Render chart"}
            >
              {report ? "Report" : "Chart"}
            </button>
          )}
          {canSchema && (
            <button
              type="button"
              className={effectiveView === "schema" ? "active" : ""}
              onClick={() => setView("schema")}
              title="Field schema (drag fields into parameters)"
            >
              Schema
            </button>
          )}
          <button
            type="button"
            className={effectiveView === "json" ? "active" : ""}
            onClick={() => setView("json")}
          >
            JSON
          </button>
          <button
            type="button"
            className={effectiveView === "raw" ? "active" : ""}
            onClick={() => setView("raw")}
            title="Raw stringified JSON"
          >
            Raw
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
          {canHtml && (
            <button
              type="button"
              className={effectiveView === "html" ? "active" : ""}
              onClick={() => setView("html")}
              title="Render HTML output"
            >
              HTML
            </button>
          )}
          {logs !== undefined && (
            <button
              type="button"
              className={effectiveView === "logs" ? "active" : ""}
              onClick={() => setView("logs")}
              title={
                hasLogs
                  ? "Show captured logs"
                  : "Show execution details"
              }
            >
              Logs{hasLogs ? ` (${logs.length})` : ""}
            </button>
          )}
          {hasVariables && (
            <button
              type="button"
              className={effectiveView === "variables" ? "active" : ""}
              onClick={() => setView("variables")}
              title="Inspect Python variables"
            >
              Variables ({variables?.length})
            </button>
          )}
          <button
            type="button"
            onClick={() => copyText(copyPayload)}
            title="Copy current view"
          >
            Copy
          </button>
        </div>
      </header>
      <div className="ndv-panel-body">
        {error && (
          <div className="ndv-node-error" role="alert">
            <span>Node error</span>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => copyText(error)}
            >
              Copy
            </button>
            <pre>{error}</pre>
          </div>
        )}
        {effectiveView === "variables" && variables ? (
          <VariableExplorer variables={variables} />
        ) : effectiveView === "logs" ? (
          <pre className="data-json data-logs">{executionLog}</pre>
        ) : effectiveView === "visual" && chart ? (
          <ChartView chart={chart} />
        ) : effectiveView === "visual" && report ? (
          <ReportView report={report} />
        ) : effectiveView === "html" && htmlPreview ? (
          <HtmlPreview html={htmlPreview} />
        ) : empty ? (
          <p className="ndv-panel-empty muted">
            {emptyMessage ?? "No data yet."}
          </p>
        ) : effectiveView === "schema" ? (
          <SchemaTree data={display} dragPrefix={dragPrefix} />
        ) : effectiveView === "table" ? (
          <DataTable data={display} dragPrefix={dragPrefix} />
        ) : effectiveView === "raw" ? (
          <pre className="data-json">{pretty(display)}</pre>
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
