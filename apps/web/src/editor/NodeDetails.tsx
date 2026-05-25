import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { categoryColor } from "../categories";
import { NodeIcon } from "../NodeIcon";
import type { ParamSpec } from "../types";
import { useEditor } from "./store";

function JsonField({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const [text, setText] = useState(() =>
    value === null || value === undefined ? "" : JSON.stringify(value, null, 2),
  );
  const [invalid, setInvalid] = useState(false);

  return (
    <textarea
      className={`field-input field-json${invalid ? " field-invalid" : ""}`}
      value={text}
      spellCheck={false}
      rows={4}
      placeholder='{ "key": "value" }'
      onChange={(e) => {
        const next = e.target.value;
        setText(next);
        if (next.trim() === "") {
          setInvalid(false);
          onChange(null);
          return;
        }
        try {
          onChange(JSON.parse(next));
          setInvalid(false);
        } catch {
          setInvalid(true);
        }
      }}
    />
  );
}

interface KvRow {
  key: string;
  value: string;
}

function rowsFromValue(value: unknown): KvRow[] {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>).map(
      ([k, v]) => ({
        key: k,
        value: typeof v === "string" ? v : JSON.stringify(v),
      }),
    );
    return entries.length > 0 ? entries : [{ key: "", value: "" }];
  }
  return [{ key: "", value: "" }];
}

function rowsToDict(rows: KvRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (key) out[key] = row.value;
  }
  return out;
}

function KeyValueField({
  value,
  onChange,
  valueChoices,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  valueChoices?: unknown[] | null;
}) {
  const initialMode: "fields" | "json" =
    value && typeof value === "object" && !Array.isArray(value)
      ? "fields"
      : value === null || value === undefined
        ? "fields"
        : "json";
  const [mode, setMode] = useState<"fields" | "json">(initialMode);
  const [rows, setRows] = useState<KvRow[]>(() => rowsFromValue(value));
  const [jsonText, setJsonText] = useState(() =>
    value === null || value === undefined ? "" : JSON.stringify(value, null, 2),
  );
  const [invalid, setInvalid] = useState(false);

  function commitRows(next: KvRow[]): void {
    setRows(next);
    onChange(rowsToDict(next));
  }

  function switchToJson(): void {
    setJsonText(JSON.stringify(rowsToDict(rows), null, 2));
    setInvalid(false);
    setMode("json");
  }

  function switchToFields(): void {
    if (!jsonText.trim()) {
      setRows([{ key: "", value: "" }]);
      onChange({});
      setMode("fields");
      return;
    }
    try {
      const parsed: unknown = JSON.parse(jsonText);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const next = rowsFromValue(parsed);
        setRows(next);
        onChange(parsed);
        setInvalid(false);
        setMode("fields");
      }
    } catch {
      /* keep JSON mode if not parseable */
    }
  }

  return (
    <div className="kv-field">
      <div className="kv-toolbar">
        <button
          type="button"
          className={`kv-mode-btn${mode === "fields" ? " is-active" : ""}`}
          onClick={() => {
            if (mode !== "fields") switchToFields();
          }}
        >
          Fields
        </button>
        <button
          type="button"
          className={`kv-mode-btn${mode === "json" ? " is-active" : ""}`}
          onClick={() => {
            if (mode !== "json") switchToJson();
          }}
        >
          Raw JSON
        </button>
      </div>

      {mode === "fields" ? (
        <KvRowList
          rows={rows}
          commitRows={commitRows}
          valueChoices={valueChoices}
        />
      ) : (
        <textarea
          className={`field-input field-json${invalid ? " field-invalid" : ""}`}
          rows={5}
          spellCheck={false}
          placeholder='{ "key": "value" }'
          value={jsonText}
          onChange={(e) => {
            const next = e.target.value;
            setJsonText(next);
            if (next.trim() === "") {
              setInvalid(false);
              onChange(null);
              return;
            }
            try {
              onChange(JSON.parse(next));
              setInvalid(false);
            } catch {
              setInvalid(true);
            }
          }}
        />
      )}
    </div>
  );
}

const KV_DRAG_TYPE = "application/x-noodle-kv-row";

function KvRowList({
  rows,
  commitRows,
  valueChoices,
}: {
  rows: KvRow[];
  commitRows: (next: KvRow[]) => void;
  valueChoices?: unknown[] | null;
}) {
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const choices = valueChoices?.map((choice) => String(choice)) ?? [];

  function moveRow(from: number, to: number): void {
    if (from === to || to < 0 || to >= rows.length) return;
    const next = rows.slice();
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    commitRows(next);
  }

  return (
    <div className="kv-rows">
      {rows.map((row, i) => (
        <div
          className={`kv-row${dragOver === i ? " kv-row-drop" : ""}`}
          key={i}
          onDragOver={(e) => {
            // Only respond to our own row drags — text/expression drops on
            // the value input still pass through via exprDropHandlers.
            if (!e.dataTransfer.types.includes(KV_DRAG_TYPE)) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
            setDragOver(i);
          }}
          onDragLeave={() => {
            if (dragOver === i) setDragOver(null);
          }}
          onDrop={(e) => {
            if (!e.dataTransfer.types.includes(KV_DRAG_TYPE)) return;
            e.preventDefault();
            const from = parseInt(
              e.dataTransfer.getData(KV_DRAG_TYPE) || "-1",
              10,
            );
            if (Number.isFinite(from) && from >= 0) moveRow(from, i);
            setDragOver(null);
            setDragFrom(null);
          }}
        >
          <span
            className="kv-handle"
            title="Drag to reorder"
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(KV_DRAG_TYPE, String(i));
              e.dataTransfer.effectAllowed = "move";
              setDragFrom(i);
            }}
            onDragEnd={() => {
              setDragFrom(null);
              setDragOver(null);
            }}
          >
            ⠿
          </span>
          <input
            className="field-input"
            placeholder="key"
            value={row.key}
            onChange={(e) =>
              commitRows(
                rows.map((r, j) =>
                  j === i ? { ...r, key: e.target.value } : r,
                ),
              )
            }
          />
          {choices.length > 0 ? (
            <select
              className="field-input kv-value-select"
              value={row.value}
              onChange={(e) =>
                commitRows(
                  rows.map((r, j) =>
                    j === i ? { ...r, value: e.target.value } : r,
                  ),
                )
              }
            >
              <option value="" disabled>
                Select type
              </option>
              {row.value && !choices.includes(row.value) && (
                <option value={row.value}>{row.value}</option>
              )}
              {choices.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="field-input"
              placeholder="value"
              value={row.value}
              onChange={(e) =>
                commitRows(
                  rows.map((r, j) =>
                    j === i ? { ...r, value: e.target.value } : r,
                  ),
                )
              }
              {...exprDropHandlers(row.value, (next) =>
                commitRows(
                  rows.map((r, j) => (j === i ? { ...r, value: next } : r)),
                ),
              )}
            />
          )}
          <button
            type="button"
            className="kv-remove"
            aria-label="Remove"
            onClick={() => {
              const filtered = rows.filter((_, j) => j !== i);
              commitRows(
                filtered.length > 0 ? filtered : [{ key: "", value: "" }],
              );
            }}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        className="btn btn-sm btn-ghost"
        onClick={() => commitRows([...rows, { key: "", value: "" }])}
      >
        + Add field
      </button>
      {dragFrom !== null && (
        <p className="muted kv-drag-hint">Drop on a row to reorder.</p>
      )}
    </div>
  );
}

function exprDropHandlers(
  current: string,
  onChange: (v: string) => void,
): {
  onDragOver: React.DragEventHandler<HTMLInputElement | HTMLTextAreaElement>;
  onDrop: React.DragEventHandler<HTMLInputElement | HTMLTextAreaElement>;
} {
  return {
    onDragOver: (e) => {
      // Only accept text-like drags. Calling preventDefault flips the cursor
      // and lets the drop fire.
      const types = Array.from(e.dataTransfer.types);
      if (
        types.includes("application/x-noodle-expression") ||
        types.includes("text/plain")
      ) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }
    },
    onDrop: (e) => {
      const expr =
        e.dataTransfer.getData("application/x-noodle-expression") ||
        e.dataTransfer.getData("text/plain");
      if (!expr) return;
      e.preventDefault();
      const el = e.currentTarget;
      const start = el.selectionStart ?? current.length;
      const end = el.selectionEnd ?? current.length;
      const next = current.slice(0, start) + expr + current.slice(end);
      onChange(next);
      // Move caret after the inserted expression on the next tick (after
      // React has applied the controlled value).
      window.setTimeout(() => {
        const pos = start + expr.length;
        try {
          el.setSelectionRange(pos, pos);
        } catch {
          /* ignore */
        }
        el.focus();
      }, 0);
    },
  };
}

export function ParamField({
  spec,
  value,
  onChange,
}: {
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (spec.key_value) {
    return (
      <KeyValueField
        value={value}
        onChange={onChange}
        valueChoices={spec.choices}
      />
    );
  }
  if (spec.choices && spec.choices.length > 0) {
    return (
      <select
        className="field-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      >
        {spec.choices.map((choice) => (
          <option key={String(choice)} value={String(choice)}>
            {String(choice)}
          </option>
        ))}
      </select>
    );
  }

  if (spec.type === "boolean") {
    return (
      <label className="field-toggle">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="field-toggle-track" />
        <span className="field-toggle-text">{value ? "true" : "false"}</span>
      </label>
    );
  }

  if (spec.type === "integer" || spec.type === "number") {
    return (
      <input
        className="field-input"
        type="number"
        placeholder={spec.placeholder}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") return onChange(null);
          const parsed = spec.type === "integer" ? parseInt(raw, 10) : Number(raw);
          onChange(Number.isNaN(parsed) ? null : parsed);
        }}
      />
    );
  }

  if (spec.type === "string") {
    const current = String(value ?? "");
    const drop = exprDropHandlers(current, onChange);
    if (spec.multiline) {
      return (
        <textarea
          className="field-input field-code"
          rows={7}
          spellCheck={false}
          placeholder={spec.placeholder}
          value={current}
          onChange={(e) => onChange(e.target.value)}
          {...drop}
        />
      );
    }
    const isExpr = /\{\{[\s\S]+?\}\}/.test(current);
    const toggleFx = () => {
      if (isExpr) {
        // Strip the outermost {{ }} pair only — keeps inner braces intact.
        const stripped = current.replace(/^\s*\{\{\s*([\s\S]*?)\s*\}\}\s*$/, "$1");
        onChange(stripped === current ? "" : stripped);
      } else {
        onChange(`{{ ${current} }}`);
      }
    };
    return (
      <div className={`field-wrap${isExpr ? " field-wrap-expr" : ""}`}>
        <input
          className={`field-input${isExpr ? " field-input-expr" : ""}`}
          type="text"
          placeholder={spec.placeholder}
          value={current}
          onChange={(e) => onChange(e.target.value)}
          {...drop}
        />
        <button
          type="button"
          className={`fx-toggle${isExpr ? " fx-toggle-on" : ""}`}
          onClick={toggleFx}
          title={isExpr ? "Switch to fixed value" : "Switch to expression"}
          tabIndex={-1}
        >
          ƒx
        </button>
      </div>
    );
  }

  return <JsonField value={value} onChange={onChange} />;
}

function UrlRow({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="webhook-url">
      <code>{url}</code>
      <button
        className="btn btn-ghost btn-sm"
        onClick={() => {
          void navigator.clipboard.writeText(url);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function WebhookPanel({
  path,
  nodeId,
}: {
  path: string;
  nodeId?: string;
}) {
  const slug = path.trim() || "noodle";
  const origin = window.location.origin;
  const [received, setReceived] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const timerRef = useRef<number | null>(null);
  const setNodeOutput = useEditor((s) => s.setNodeOutput);

  function stop(): void {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setListening(false);
  }

  useEffect(() => stop, []);
  useEffect(() => {
    // Stop listening when the user switches to a different webhook node.
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  async function listen(): Promise<void> {
    if (listening) return;
    setError("");
    setReceived(false);
    if (nodeId) setNodeOutput(nodeId, undefined);
    try {
      await api.clearWebhook(slug);
    } catch (err) {
      setError(String(err));
      return;
    }
    setListening(true);
    timerRef.current = window.setInterval(async () => {
      try {
        const data = await api.lastWebhook(slug);
        if (data !== null && data !== undefined) {
          setReceived(true);
          if (nodeId) setNodeOutput(nodeId, { main: data }, "success");
          stop();
        }
      } catch (err) {
        setError(String(err));
        stop();
      }
    }, 1300);
  }

  return (
    <div className="inspector-section webhook-panel">
      <div className="inspector-section-head">Webhook URLs</div>
      <p className="field-desc">Test URL — captures requests while you build.</p>
      <UrlRow url={`${origin}/api/webhook-test/${slug}`} />
      <p className="field-desc">
        Production URL — runs this workflow when it is active.
      </p>
      <UrlRow url={`${origin}/api/webhook/${slug}`} />

      <div className="webhook-listen">
        {!listening ? (
          <button className="btn btn-sm" onClick={() => void listen()}>
            Listen for test event
          </button>
        ) : (
          <button className="btn btn-sm btn-listening" onClick={stop}>
            <span className="node-spinner" />
            Listening… (Stop)
          </button>
        )}
        {received && (
          <span className="muted">Request captured — see Output panel.</span>
        )}
      </div>

      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

export function NodeDetails({
  nodeId,
  showHeader = true,
}: {
  nodeId: string;
  showHeader?: boolean;
}) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const updateParams = useEditor((s) => s.updateParams);
  const runStatus = useEditor((s) => s.runStatus[nodeId]);
  const runOutput = useEditor((s) => s.runOutputs[nodeId]);
  const runMeta = useEditor((s) => s.runMeta[nodeId]);
  const runOutputs = useEditor((s) => s.runOutputs);
  const edges = useEditor((s) => s.edges);
  const workflowId = useEditor((s) => s.workflowId);
  const pinned = useEditor((s) => s.pinned[nodeId]);
  const setPinnedFor = useEditor((s) => s.setPinnedFor);

  async function pin(): Promise<void> {
    if (!workflowId || runOutput === undefined) return;
    try {
      await api.pinNode(workflowId, nodeId, runOutput);
      setPinnedFor(nodeId, runOutput);
    } catch {
      /* ignore */
    }
  }

  async function unpin(): Promise<void> {
    if (!workflowId) return;
    try {
      await api.unpinNode(workflowId, nodeId);
      setPinnedFor(nodeId, null);
    } catch {
      /* ignore */
    }
  }

  if (!node) {
    return (
      <div className="inspector-empty">
        <p>This node is no longer in the workflow.</p>
      </div>
    );
  }

  const { manifest, params, disabled } = node.data;
  const color = categoryColor(manifest.category);

  const setParam = (name: string, value: unknown) => {
    updateParams(node.id, { ...params, [name]: value });
  };

  // Compute the data flowing into this node from upstream node outputs.
  const incomingInputs: Record<string, unknown> = {};
  for (const edge of edges) {
    if (edge.target !== node.id) continue;
    const upstream = runOutputs[edge.source];
    if (!upstream || typeof upstream !== "object") continue;
    const sourceHandle = edge.sourceHandle ?? "main";
    const value = (upstream as Record<string, unknown>)[sourceHandle];
    if (value !== undefined) {
      incomingInputs[edge.targetHandle ?? "input"] = value;
    }
  }
  const hasIncomingInputs = Object.keys(incomingInputs).length > 0;

  return (
    <>
      {showHeader && (
        <div className="inspector-node">
          <div className="inspector-node-head">
            <span className="inspector-glyph" style={{ color }}>
              <NodeIcon name={manifest.icon} size={20} />
            </span>
            <span className="inspector-node-name">{manifest.name}</span>
            {disabled && <span className="cred-type">disabled</span>}
          </div>
          <div className="inspector-meta">
            <span className="mono-tag" style={{ color }}>
              {manifest.category}
            </span>
            <span className="mono-tag">{manifest.id}</span>
          </div>
          {manifest.description && (
            <p className="inspector-desc">{manifest.description}</p>
          )}
        </div>
      )}

      <div className="inspector-section">
        <div className="inspector-section-head">Parameters</div>
        <p className="field-desc expr-hint">
          Use <code>{"{{ $json.field }}"}</code> or{" "}
          <code>{'{{ $node["nodeId"].main.field }}'}</code> in string fields to
          reference upstream data.
        </p>
        {manifest.params.length === 0 && (
          <p className="muted">This node has no parameters.</p>
        )}
        {manifest.params.map((spec) => {
          const value = params[spec.name];
          const fx = typeof value === "string" && /\{\{.+?\}\}/s.test(value);
          return (
            <div className="field" key={`${node.id}:${spec.name}`}>
              <div className="field-label">
                <span className="field-name">{spec.name}</span>
                <span className="field-type">{spec.type}</span>
                {fx && <span className="fx-badge" title="Contains expression">fx</span>}
                {spec.required && <span className="field-req">required</span>}
              </div>
              {spec.description && (
                <p className="field-desc">{spec.description}</p>
              )}
              <ParamField
                spec={spec}
                value={value}
                onChange={(v) => setParam(spec.name, v)}
              />
            </div>
          );
        })}
      </div>

      {hasIncomingInputs && (
        <div className="inspector-section input-section">
          <div className="inspector-section-head">Input</div>
          <p className="field-desc">From upstream nodes' most recent run.</p>
          <pre className="run-output">
            {JSON.stringify(incomingInputs, null, 2)}
          </pre>
        </div>
      )}

      {runStatus && (
        <div className="inspector-section run-section">
          <div className="inspector-section-head">
            Last run
            <span className={`run-pill status-run-${runStatus}`}>
              {runStatus}
            </span>
          </div>
          {runMeta?.error && (
            <div className="ndv-node-error" role="alert">
              <span>Node error</span>
              <pre>{runMeta.error}</pre>
            </div>
          )}
          {runOutput !== undefined ? (
            <pre className="run-output">
              {JSON.stringify(runOutput, null, 2)}
            </pre>
          ) : (
            <p className="muted">No output captured.</p>
          )}
          {runOutput !== undefined && !pinned && (
            <button
              className="btn btn-sm"
              style={{ marginTop: 8 }}
              onClick={() => void pin()}
            >
              📌 Pin this output
            </button>
          )}
        </div>
      )}

      {pinned !== undefined && (
        <div className="inspector-section pin-section">
          <div className="inspector-section-head">
            Pinned
            <span className="run-pill status-run-success">pinned</span>
          </div>
          <p className="field-desc">
            Runs use this value instead of executing the node.
          </p>
          <pre className="run-output">{JSON.stringify(pinned, null, 2)}</pre>
          <button
            className="btn btn-sm btn-ghost"
            style={{ marginTop: 8 }}
            onClick={() => void unpin()}
          >
            Unpin
          </button>
        </div>
      )}

      {manifest.id === "webhook_trigger" && (
        <WebhookPanel
          path={String(params.path ?? "noodle")}
          nodeId={node.id}
        />
      )}
    </>
  );
}
