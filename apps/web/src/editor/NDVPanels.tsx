import { Gear, Hexagon, Info, PushPin } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import { useToast } from "../ToastProvider";
import type { ArtifactInfo, ParamSpec } from "../types";
import { AgentTrace, extractAgentTrace } from "./AgentTrace";
import { missingFor } from "./missingPackages";
import { useServerPlatform } from "../hooks/useServerPlatform";
import { useTimeout } from "../hooks/useTimeout";
import { safeGetItem } from "../safeStorage";
import { DataPanel } from "./DataPanel";
import {
  ParamField,
  WebhookPanel,
  WEBHOOK_AUTH_TYPE_OPTIONS,
  formatParamLabel,
  groupActiveByValue,
  matchesDisplayWhen,
  paramGroup,
  ResourceOperationSelector,
  webhookCredentialSpec,
  webhookHiddenParam,
  webhookParamLabel,
  NodeCodePanel,
  ToolModeSection,
  FromAiParamControl,
} from "./NodeDetails";
import { isFromAiExpr } from "./toolParam";
import type { ExprContext } from "./node-details/expressions";
import { useEditor } from "./store";
import { VariablePickerPopover } from "./VariablePickerPopover";
import { asArtifactRef, artifactDownloadUrl, artifactSummary, formatBytes } from "./artifactValues";

/**
 * The three-column body of the NDV modal: Input | Parameters/Settings | Output.
 *
 * The user sees the data flowing in on the left, configures the node in
 * the middle, and inspects what came out on the right.
 */

const PACKAGE_INSTALL_TIMEOUT_MS = 10 * 60 * 1000;
const PACKAGE_INSTALL_POLL_MS = 2000;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function formatPinnedAt(value: string | null): string {
  if (!value) return "time unknown";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

type NdvTab = "parameters" | "settings" | "docs" | "credentials" | "logs" | "trace";

function AgentWiringBanner({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const edges = useEditor((s) => s.edges);
  const nodes = useEditor((s) => s.nodes);
  if (!node || node.data.manifest.id !== "ai_agent_v2") return null;

  const params = node.data.params;
  const strategy = String(params.strategy ?? "react");
  const strategyLabel: Record<string, string> = {
    react: "ReAct",
    plan_and_execute: "Plan & Execute",
    reflexion: "Reflexion",
  };

  const portLabels: Record<string, string> = {
    model: "Model",
    fast_model: "Fast model",
    tool: "Tool",
    subagent_1: "Sub-agent 1",
    subagent_2: "Sub-agent 2",
    subagent_3: "Sub-agent 3",
    retriever: "Retriever",
    memory: "Memory",
    guardrail: "Guardrail",
    parser: "Parser",
  };

  const connected: Array<{ port: string; label: string; nodeName: string }> = [];
  for (const edge of edges) {
    if (edge.target !== nodeId) continue;
    const handle = edge.targetHandle ?? "input";
    const portLabel = portLabels[handle];
    if (!portLabel) continue;
    const sourceNode = nodes.find((n) => n.id === edge.source);
    if (!sourceNode) continue;
    connected.push({
      port: handle,
      label: portLabel,
      nodeName:
        sourceNode.data.label ||
        String(sourceNode.data.params.model || sourceNode.data.params.deployment || "") ||
        sourceNode.data.manifest.name,
    });
  }

  const modelEntry = connected.find((c) => c.port === "model");
  const toolCount = connected.filter((c) => c.port === "tool").length;
  const otherConnected = connected.filter((c) => c.port !== "model" && c.port !== "tool");

  return (
    <div className="agent-ndv-banner">
      <div className="agent-ndv-banner-row">
        <span className="agent-ndv-chip agent-ndv-chip--strategy" title="Agent strategy">
          {strategyLabel[strategy] ?? strategy}
        </span>
        {modelEntry ? (
          <span className="agent-ndv-chip agent-ndv-chip--ok" title={`Model port: ${modelEntry.nodeName}`}>
            <Hexagon size={12} /> {modelEntry.nodeName}
          </span>
        ) : (
          <span className="agent-ndv-chip agent-ndv-chip--warn" title="No model connected to model port">
            <Hexagon size={12} /> No model
          </span>
        )}
        {toolCount > 0 ? (
          <span className="agent-ndv-chip agent-ndv-chip--ok" title={`${toolCount} tool node${toolCount === 1 ? "" : "s"} wired`}>
            <Gear size={12} /> {toolCount} tool{toolCount === 1 ? "" : "s"}
          </span>
        ) : null}
        {otherConnected.map((c) => (
          <span key={c.port} className="agent-ndv-chip agent-ndv-chip--ok" title={`${c.label}: ${c.nodeName}`}>
            {c.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function ParametersTab({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const updateParams = useEditor((s) => s.updateParams);
  const runOutputs = useEditor((s) => s.runOutputs);
  const edges = useEditor((s) => s.edges);
  // Inspector (form) vs Python (node source) view of the node.
  const [mode, setMode] = useState<"inspector" | "python">("inspector");
  // Which optional groups the user has explicitly opened/closed this session.
  // Reset when switching nodes so each node starts from its own value-derived
  // state. `undefined` for a group means "decide from saved values".
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const [pickerParam, setPickerParam] = useState<string | null>(null);
  useEffect(() => setOpenGroups({}), [nodeId]);
  if (!node) return null;
  const { manifest, params } = node.data;

  const setParam = (name: string, value: unknown) => {
    updateParams(node.id, { ...params, [name]: value });
  };

  // Optional-parameter grouping: split params into core (always shown) and
  // groups surfaced as "Add option" chips. Group order follows first appearance
  // in the manifest; value-based auto-expand uses the full group (incl. fields
  // currently hidden by a dependent toggle).
  const groupOrder: string[] = [];
  const allByGroup = new Map<string, ParamSpec[]>();
  for (const spec of manifest.params) {
    const g = paramGroup(spec);
    if (!g) continue;
    if (!allByGroup.has(g)) {
      allByGroup.set(g, []);
      groupOrder.push(g);
    }
    allByGroup.get(g)!.push(spec);
  }
  const groupIsOpen = (g: string): boolean =>
    openGroups[g] ?? groupActiveByValue(allByGroup.get(g) ?? [], params);
  const addGroup = (g: string) =>
    setOpenGroups((prev) => ({ ...prev, [g]: true }));
  const removeGroup = (g: string) => {
    // Clear the group's values back to default, then collapse it to a chip.
    const cleared: Record<string, unknown> = { ...params };
    for (const spec of allByGroup.get(g) ?? []) cleared[spec.name] = spec.default;
    updateParams(node.id, cleared);
    setOpenGroups((prev) => ({ ...prev, [g]: false }));
  };

  // Data flowing into this node from upstream outputs (for code drag-drop).
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

  // Live context so parameter fields can resolve {{ }} inline against real
  // upstream data (mirrors the expand-modal preview).
  const exprContext: ExprContext = {
    json: Object.values(incomingInputs)[0],
    inputs: incomingInputs,
    nodes: runOutputs,
  };

  return (
    <>
      <div className="ndv-mode-toggle" role="tablist" aria-label="Inspector or Python">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "inspector"}
          className={mode === "inspector" ? "active" : ""}
          onClick={() => setMode("inspector")}
        >
          Inspector
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "python"}
          className={mode === "python" ? "active" : ""}
          onClick={() => setMode("python")}
        >
          Python
        </button>
      </div>
      {mode === "python" ? (
        <NodeCodePanel
          nodeId={node.id}
          manifest={manifest}
          inputData={hasIncomingInputs ? incomingInputs : undefined}
          onClose={() => setMode("inspector")}
        />
      ) : (
      <>
      <p className="expr-hint field-desc">
        Use <code>{"{{ $json.field }}"}</code> or{" "}
        <code>{'{{ $node["nodeId"].main.field }}'}</code> in string fields to
        reference upstream data.
      </p>
      {manifest.params.length === 0 && (
        <p className="muted">This node has no parameters.</p>
      )}
      {(() => {
        const renderField = (spec: (typeof manifest.params)[number]) => {
          const value = params[spec.name];
          const displayLabel =
            webhookParamLabel(manifest.id, spec.name, params) ??
            (spec.display_name || formatParamLabel(spec.name));

          const isWebhookAuthType =
            manifest.id === "webhook_trigger" && spec.name === "auth_type";
          const isWebhookCreds =
            manifest.id === "webhook_trigger" && spec.name === "auth_credentials";

          let renderSpec = spec;
          if (isWebhookCreds) {
            const at = String(params.auth_type || "none").toLowerCase();
            renderSpec = webhookCredentialSpec(spec, at);
          }

          return (
            <div className="field" key={`${node.id}:${spec.name}`}>
              <div className="field-label">
                <span className="field-name">{displayLabel}</span>
                {spec.description && (
                  <span className="param-info-icon" aria-label={spec.description}>
                    <Info size={12} weight="bold" />
                    <span className="param-info-tooltip">{spec.description}</span>
                  </span>
                )}
                {spec.required && <span className="field-req">required</span>}
                {(spec.type === "string" || spec.type === "expression") &&
                  !spec.credential &&
                  spec.widget !== "hidden" && (
                    <button
                      type="button"
                      className="var-pick-inline-btn"
                      title="Pick a variable from upstream nodes"
                      onClick={() =>
                        setPickerParam((p) => (p === spec.name ? null : spec.name))
                      }
                    >
                      $
                    </button>
                  )}
              </div>
              <FromAiParamControl
                nodeId={node.id}
                spec={renderSpec}
                value={value}
                onSetParam={setParam}
              />
              {isFromAiExpr(value) ? (
                <p className="from-ai-note">↯ The model supplies this argument.</p>
              ) : isWebhookAuthType ? (
                <select
                  className="field-input"
                  value={String(value ?? "none")}
                  onChange={(e) => {
                    // Atomic update — two setParam calls would race on
                    // the same render-time params snapshot.
                    updateParams(node.id, {
                      ...params,
                      [spec.name]: e.target.value,
                      auth_credentials: "",
                    });
                  }}
                >
                  {WEBHOOK_AUTH_TYPE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              ) : (
                <ParamField
                  spec={renderSpec}
                  value={value}
                  onChange={(v) => setParam(spec.name, v)}
                  credentialContext={params}
                  exprContext={exprContext}
                  nodeId={node.id}
                />
              )}
              {pickerParam === spec.name && (
                <div className="var-pick-inline-wrap">
                  <VariablePickerPopover
                    nodeId={node.id}
                    onInsert={(expr) => {
                      const current = String(params[spec.name] ?? "");
                      const pos = current.length;
                      const before = current.slice(0, pos);
                      const opens = (before.match(/\{\{/g) ?? []).length;
                      const closes = (before.match(/\}\}/g) ?? []).length;
                      const insideExpr = opens > closes;
                      const toInsert = insideExpr
                        ? expr.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "")
                        : expr;
                      setParam(spec.name, current + toInsert);
                      setPickerParam(null);
                    }}
                    onClose={() => setPickerParam(null)}
                  />
                </div>
              )}
            </div>
          );
        };

        const visible = manifest.params.filter(
          (spec) =>
            spec.widget !== "hidden" &&
            !webhookHiddenParam(manifest.id, spec.name, params) &&
            matchesDisplayWhen(spec.display_when, params),
        );
        const core = visible.filter((spec) => !paramGroup(spec));
        const visibleByGroup = new Map<string, ParamSpec[]>();
        for (const spec of visible) {
          const g = paramGroup(spec);
          if (!g) continue;
          if (!visibleByGroup.has(g)) visibleByGroup.set(g, []);
          visibleByGroup.get(g)!.push(spec);
        }
        const closedGroups = groupOrder.filter((g) => !groupIsOpen(g));

        return (
          <>
            {manifest.integration && (
              <ResourceOperationSelector
                manifest={manifest}
                params={params}
                onChange={(next) => updateParams(node.id, next)}
              />
            )}
            {core.map(renderField)}
            {groupOrder
              .filter((g) => groupIsOpen(g))
              .map((g) => (
                <div className="ndv-group" key={`grp:${node.id}:${g}`}>
                  <div className="ndv-group-head">
                    <span>{g}</span>
                    <button
                      type="button"
                      className="ndv-group-remove"
                      title={`Remove ${g}`}
                      onClick={() => removeGroup(g)}
                    >
                      &times;
                    </button>
                  </div>
                  {(visibleByGroup.get(g) ?? []).map(renderField)}
                </div>
              ))}
            {closedGroups.length > 0 && (
              <div className="ndv-add-options">
                {closedGroups.map((g) => (
                  <button
                    type="button"
                    className="ndv-add-chip"
                    key={`chip:${node.id}:${g}`}
                    onClick={() => addGroup(g)}
                  >
                    + {g}
                  </button>
                ))}
              </div>
            )}
          </>
        );
      })()}
      </>
      )}
    </>
  );
}

function SettingsTab({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const updateNodeSettings = useEditor((s) => s.updateNodeSettings);
  if (!node) return null;
  const data = node.data;
  const defaultTimeout = data.manifest.id === "http_request" ? 45 : null;

  return (
    <>
      <ToolModeSection nodeId={nodeId} />
      <p className="field-desc">
        How this node behaves on failure and what flows downstream.
      </p>

      <div className="field">
        <div className="field-label">
          <span className="field-name">On error</span>
        </div>
        <p className="field-desc">What happens if this node throws.</p>
        <select
          className="field-input"
          value={data.onError ?? "stop"}
          onChange={(e) =>
            updateNodeSettings(nodeId, { onError: e.target.value })
          }
        >
          <option value="stop">Stop the workflow</option>
          <option value="continue">
            Continue (pass empty data downstream)
          </option>
        </select>
      </div>

      <div className="field">
        <div className="field-label">
          <span className="field-name">Retry on fail</span>
        </div>
        <p className="field-desc">
          Re-run the node a few times before giving up.
        </p>
        <label className="field-toggle">
          <input
            type="checkbox"
            checked={Boolean(data.retryOnFail)}
            onChange={(e) =>
              updateNodeSettings(nodeId, { retryOnFail: e.target.checked })
            }
          />
          <span className="field-toggle-track" />
          <span className="field-toggle-text">
            {data.retryOnFail ? "enabled" : "disabled"}
          </span>
        </label>
      </div>

      {data.retryOnFail && (
        <>
          <div className="field">
            <div className="field-label">
              <span className="field-name">Retries</span>
            </div>
            <input
              className="field-input"
              type="number"
              min={1}
              max={10}
              value={typeof data.retries === "number" ? data.retries : 1}
              onChange={(e) =>
                updateNodeSettings(nodeId, {
                  retries: Math.max(
                    1,
                    Math.min(10, parseInt(e.target.value || "1", 10) || 1),
                  ),
                })
              }
            />
          </div>

          <div className="field">
            <div className="field-label">
              <span className="field-name">Wait between retries (s)</span>
            </div>
            <p className="field-desc">
              Seconds to wait before each retry (0 = immediate).
            </p>
            <input
              className="field-input"
              type="number"
              min={0}
              step={0.5}
              value={
                typeof data.retryWaitSeconds === "number"
                  ? data.retryWaitSeconds
                  : 0
              }
              onChange={(e) =>
                updateNodeSettings(nodeId, {
                  retryWaitSeconds: Math.max(
                    0,
                    parseFloat(e.target.value || "0") || 0,
                  ),
                })
              }
            />
          </div>

          <div className="field">
            <div className="field-label">
              <span className="field-name">Exponential backoff</span>
            </div>
            <p className="field-desc">
              Double the wait after each failed attempt.
            </p>
            <label className="field-toggle">
              <input
                type="checkbox"
                checked={Boolean(data.retryBackoff)}
                onChange={(e) =>
                  updateNodeSettings(nodeId, { retryBackoff: e.target.checked })
                }
              />
              <span className="field-toggle-track" />
              <span className="field-toggle-text">
                {data.retryBackoff ? "enabled" : "disabled"}
              </span>
            </label>
          </div>
        </>
      )}

      <div className="field">
        <div className="field-label">
          <span className="field-name">Timeout (s)</span>
        </div>
        <p className="field-desc">
          {defaultTimeout
            ? `Blank uses the ${defaultTimeout}s default.`
            : "Blank = no timeout."}
        </p>
        <input
          className="field-input"
          type="number"
          min={0}
          step={1}
          placeholder={defaultTimeout ? `${defaultTimeout}` : "no timeout"}
          value={
            typeof data.timeoutSeconds === "number" ? data.timeoutSeconds : ""
          }
          onChange={(e) => {
            const raw = e.target.value;
            updateNodeSettings(nodeId, {
              timeoutSeconds:
                raw === "" ? null : Math.max(0, parseFloat(raw) || 0),
            });
          }}
        />
      </div>

      <div className="field">
        <div className="field-label">
          <span className="field-name">Always output data</span>
        </div>
        <p className="field-desc">
          Emit an empty output even if the node errors so downstream nodes
          still run.
        </p>
        <label className="field-toggle">
          <input
            type="checkbox"
            checked={Boolean(data.alwaysOutputData)}
            onChange={(e) =>
              updateNodeSettings(nodeId, {
                alwaysOutputData: e.target.checked,
              })
            }
          />
          <span className="field-toggle-track" />
          <span className="field-toggle-text">
            {data.alwaysOutputData ? "enabled" : "disabled"}
          </span>
        </label>
      </div>
    </>
  );
}

function DocsTab({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  if (!node) return null;
  const m = node.data.manifest;
  return (
    <>
      <p className="field-desc">Reference for this node.</p>
      <div className="field">
        <div className="field-label">
          <span className="field-name">{m.name}</span>
        </div>
        <p className="field-desc">{m.description || "No description provided."}</p>
        <p className="field-desc">
          <strong>Category:</strong> {m.category} · <strong>Version:</strong> {m.version} · <strong>Type:</strong> <code>{m.id}</code>
        </p>
      </div>
      {m.inputs.length > 0 && (
        <div className="field">
          <div className="field-label">
            <span className="field-name">Inputs</span>
          </div>
          <ul className="field-desc" style={{ margin: 0, paddingLeft: 18 }}>
            {m.inputs.map((p) => (
              <li key={p.name}>
                <code>{p.name}</code>
                {p.description ? ` — ${p.description}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {m.outputs.length > 0 && (
        <div className="field">
          <div className="field-label">
            <span className="field-name">Outputs</span>
          </div>
          <ul className="field-desc" style={{ margin: 0, paddingLeft: 18 }}>
            {m.outputs.map((p) => (
              <li key={p.name}>
                <code>{p.name}</code>
                {p.description ? ` — ${p.description}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {m.params.length > 0 && (
        <div className="field">
          <div className="field-label">
            <span className="field-name">Parameters</span>
          </div>
          <ul className="field-desc" style={{ margin: 0, paddingLeft: 18 }}>
            {m.params.map((p) => (
              <li key={p.name}>
                <code>{p.name}</code> ({p.type}
                {p.required ? ", required" : ""})
                {p.description ? ` — ${p.description}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

function CredentialsTab({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  if (!node) return null;
  const credSpecs = node.data.manifest.params.filter((p) => p.credential);
  if (credSpecs.length === 0) {
    return (
      <p className="field-desc">
        This node does not use stored credentials. Anything sensitive is set
        directly in its parameters.
      </p>
    );
  }
  return (
    <>
      <p className="field-desc">
        Credentials required by this node. Manage stored credentials from{" "}
        <a href="/credentials" target="_blank" rel="noreferrer">
          Settings → Credentials
        </a>
        .
      </p>
      {credSpecs.map((spec) => {
        const value = node.data.params[spec.name];
        const ref =
          value && typeof value === "object" && "credential_id" in (value as object)
            ? (value as { credential_id: string }).credential_id
            : null;
        return (
          <div className="field" key={spec.name}>
            <div className="field-label">
              <span className="field-name">{spec.credential?.label ?? spec.name}</span>
            </div>
            <p className="field-desc">
              Type: <code>{spec.credential?.type}</code>
            </p>
            <p className="field-desc">
              {ref ? (
                <>Linked to credential <code>{ref}</code></>
              ) : (
                "No credential selected. Pick one in the Parameters tab."
              )}
            </p>
          </div>
        );
      })}
    </>
  );
}

function LogsTab({ nodeId }: { nodeId: string }) {
  const runMeta = useEditor((s) => s.runMeta[nodeId]);
  const logs = runMeta?.logs ?? [];
  const error = runMeta?.error;
  if (logs.length === 0 && !error) {
    return (
      <p className="field-desc">
        No logs yet. Run this node (or the workflow) to capture stdout, stderr,
        and any error trace.
      </p>
    );
  }
  return (
    <>
      {error && (
        <div className="field">
          <div className="field-label">
            <span className="field-name">Error</span>
          </div>
          <pre className="field-desc" style={{ whiteSpace: "pre-wrap", color: "var(--error, #d97706)" }}>
            {String(error)}
          </pre>
        </div>
      )}
      {logs.length > 0 && (
        <div className="field">
          <div className="field-label">
            <span className="field-name">Logs</span>
          </div>
          <pre
            className="field-desc"
            style={{ whiteSpace: "pre-wrap", maxHeight: 400, overflow: "auto", margin: 0 }}
          >
            {logs.join("\n")}
          </pre>
        </div>
      )}
    </>
  );
}

function TraceTab({
  runMeta,
}: {
  runMeta: Record<string, unknown> | undefined;
}) {
  const trace = useMemo(() => extractAgentTrace(runMeta), [runMeta]);

  if (!trace) {
    return (
      <p className="field-desc">
        No agent trace available. Run the agent node to see its reasoning steps,
        tool calls, and outputs.
      </p>
    );
  }

  return <AgentTrace trace={trace} />;
}

function ArtifactBrowser({
  runId,
  nodeId,
  runOutput,
}: {
  runId: string;
  nodeId: string;
  runOutput: unknown;
}) {
  const [artifacts, setArtifacts] = useState<ArtifactInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setArtifacts(null);
    setError(null);
    api.listRunArtifacts(runId, nodeId).then(
      (list: ArtifactInfo[]) => { if (!cancelled) setArtifacts(list); },
      (err: unknown) => { if (!cancelled) setError(String(err)); },
    );
    return () => { cancelled = true; };
  }, [runId, nodeId]);

  // Scan runOutput for embedded refs — used as a fallback while the DB fetch
  // is in-flight (there is a brief window between the node finishing and the
  // artifact rows being committed).
  const embeddedRefs: Array<ReturnType<typeof asArtifactRef> & object> = [];
  if (runOutput && typeof runOutput === "object") {
    for (const val of Object.values(runOutput as Record<string, unknown>)) {
      const ref = asArtifactRef(val);
      if (ref) embeddedRefs.push(ref);
    }
  }

  // Once the DB list arrives, it is the source of truth. Only fall back to
  // embeddedRefs while still loading so we never show the same artifact twice.
  const apiArtifacts = artifacts ?? [];
  const apiIds = new Set(apiArtifacts.map((a) => a.id));
  const fallbackRefs = artifacts === null ? embeddedRefs : embeddedRefs.filter((r) => !apiIds.has(r.artifact_id));
  const hasContent = apiArtifacts.length > 0 || fallbackRefs.length > 0;

  if (error) {
    return (
      <div style={{ padding: "8px 12px", fontSize: "0.8rem", color: "var(--color-danger, red)" }}>
        Failed to load artifacts: {error}
      </div>
    );
  }
  if (!hasContent && artifacts !== null) return null;
  if (artifacts === null && embeddedRefs.length === 0) {
    return (
      <div style={{ padding: "8px 12px", fontSize: "0.8rem", opacity: 0.6 }}>
        Loading artifacts…
      </div>
    );
  }

  return (
    <div style={{ borderTop: "1px solid var(--color-border, #e0e0e0)", padding: "12px" }}>
      <h4 style={{ margin: "0 0 8px", fontSize: "0.8rem", textTransform: "uppercase", opacity: 0.6 }}>
        Artifacts
      </h4>
      {apiArtifacts.map((a) => {
        const token = safeGetItem("noodle_token");
        const qs = token ? `?token=${encodeURIComponent(token)}` : "";
        const url = `/api/artifacts/${encodeURIComponent(a.id)}/download${qs}`;
        return (
          <div key={a.id} style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <strong style={{ fontSize: "0.875rem" }}>{a.name}</strong>
              <span style={{ fontSize: "0.75rem", opacity: 0.6 }}>{formatBytes(a.size_bytes)}</span>
              <a href={url} download={a.name} style={{ fontSize: "0.75rem", marginLeft: "auto" }}>
                Download
              </a>
            </div>
            {a.content_type.startsWith("image/") && (
              <img src={url} alt={a.name} style={{ maxWidth: "100%", borderRadius: 4 }} />
            )}
            {a.content_type.startsWith("text/") && a.preview != null && (
              <pre style={{ fontSize: "0.75rem", maxHeight: 120, overflow: "auto", background: "var(--color-surface-alt, #1e1e1e)", padding: 8, borderRadius: 4 }}>
                {String(a.preview).slice(0, 500)}
              </pre>
            )}
            {a.content_type === "application/json" && a.preview != null && (
              <pre style={{ fontSize: "0.75rem", maxHeight: 120, overflow: "auto", background: "var(--color-surface-alt, #1e1e1e)", padding: 8, borderRadius: 4 }}>
                {JSON.stringify(a.preview, null, 2).slice(0, 500)}
              </pre>
            )}
          </div>
        );
      })}
      {fallbackRefs.map((ref) => {
        const url = artifactDownloadUrl(ref);
        return (
          <div key={ref.artifact_id} style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <strong style={{ fontSize: "0.875rem" }}>{ref.name}</strong>
              <span style={{ fontSize: "0.75rem", opacity: 0.6 }}>{artifactSummary(ref)}</span>
              <a href={url} download={ref.name} style={{ fontSize: "0.75rem", marginLeft: "auto" }}>
                Download
              </a>
            </div>
            {ref.content_type.startsWith("image/") && (
              <img src={url} alt={ref.name} style={{ maxWidth: "100%", borderRadius: 4 }} />
            )}
            {ref.content_type.startsWith("text/") && ref.preview != null && (
              <pre style={{ fontSize: "0.75rem", maxHeight: 120, overflow: "auto", background: "var(--color-surface-alt, #1e1e1e)", padding: 8, borderRadius: 4 }}>
                {String(ref.preview).slice(0, 500)}
              </pre>
            )}
            {ref.content_type === "application/json" && ref.preview != null && (
              <pre style={{ fontSize: "0.75rem", maxHeight: 120, overflow: "auto", background: "var(--color-surface-alt, #1e1e1e)", padding: 8, borderRadius: 4 }}>
                {JSON.stringify(ref.preview, null, 2).slice(0, 500)}
              </pre>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function NDVPanels({
  nodeId,
  onListeningChange,
}: {
  nodeId: string;
  onListeningChange?: (listening: boolean) => void;
}) {
  const [tab, setTab] = useState<NdvTab>("parameters");
  const middleBodyRef = useRef<HTMLDivElement | null>(null);
  const tabScroll = useRef<Record<NdvTab, number>>({
    parameters: 0,
    settings: 0,
    docs: 0,
    credentials: 0,
    logs: 0,
    trace: 0,
  });
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const manifest = node?.data.manifest;
  const isAgent = manifest?.id === "ai_agent_v2";
  const edges = useEditor((s) => s.edges);
  const runOutputs = useEditor((s) => s.runOutputs);
  const runOutput = useEditor((s) => s.runOutputs[nodeId]);
  const runStatus = useEditor((s) => s.runStatus[nodeId]);
  const runMeta = useEditor((s) => s.runMeta[nodeId]);
  const runId = useEditor((s) => s.runId);
  const workflowId = useEditor((s) => s.workflowId);
  const pinned = useEditor((s) => s.pinned[nodeId]);
  const setPinnedFor = useEditor((s) => s.setPinnedFor);
  const envId = useEditor((s) => s.envId);
  const envName = useEditor((s) => s.envName);
  const envPackages = useEditor((s) => s.envPackages);
  const environmentsList = useEditor((s) => s.environmentsList);
  const setEnvPackages = useEditor((s) => s.setEnvPackages);
  const applyEnvSwitch = useEditor((s) => s.applyEnvSwitch);
  const [pkgBusy, setPkgBusy] = useState(false);
  const [pkgElapsed, setPkgElapsed] = useState(0);
  const [pkgDone, setPkgDone] = useState(false);
  const scheduleTimeout = useTimeout();
  const installGenerationRef = useRef(0);
  const installTickerRef = useRef<number | null>(null);
  const { notify } = useToast();
  const platform = useServerPlatform();

  useEffect(() => {
    tabScroll.current = {
      parameters: 0,
      settings: 0,
      docs: 0,
      credentials: 0,
      logs: 0,
      trace: 0,
    };
    setTab("parameters");
    setPkgBusy(false);
    setPkgElapsed(0);
    setPkgDone(false);
  }, [nodeId]);

  useEffect(
    () => () => {
      installGenerationRef.current += 1;
      if (installTickerRef.current !== null) {
        window.clearInterval(installTickerRef.current);
        installTickerRef.current = null;
      }
    },
    [nodeId],
  );

  useEffect(() => {
    const body = middleBodyRef.current;
    if (!body) return;
    body.scrollTop = tabScroll.current[tab] ?? 0;
  }, [tab]);

  function selectTab(next: NdvTab): void {
    const body = middleBodyRef.current;
    if (body) tabScroll.current[tab] = body.scrollTop;
    setTab(next);
  }

  if (!node) {
    return (
      <div className="inspector-empty">
        <p>This node is no longer in the workflow.</p>
      </div>
    );
  }

  const missingPkgs = missingFor(node.data.manifest.requirements ?? [], envPackages, platform ?? undefined);
  const satisfyingEnvs = environmentsList.filter(
    (e) => e.id !== envId && missingFor(missingPkgs, e.packages, platform ?? undefined).length === 0,
  );

  async function addMissingToEnv(): Promise<void> {
    if (!envId || pkgBusy) return;
    const generation = ++installGenerationRef.current;
    const isCurrent = () => installGenerationRef.current === generation;
    setPkgBusy(true);
    setPkgElapsed(0);
    setPkgDone(false);
    const startedAt = Date.now();
    const ticker = window.setInterval(() => {
      if (isCurrent()) setPkgElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    installTickerRef.current = ticker;
    try {
      const updated = [...envPackages, ...missingPkgs];
      await api.setPackages(envId, updated);
      if (!isCurrent()) return;
      setEnvPackages(updated);

      while (Date.now() - startedAt < PACKAGE_INSTALL_TIMEOUT_MS) {
        await delay(PACKAGE_INSTALL_POLL_MS);
        if (!isCurrent()) return;
        const env = await api.getEnvironment(envId);
        if (!isCurrent()) return;
        if (env.status === "ready") {
          setPkgDone(true);
          scheduleTimeout(() => {
            if (isCurrent()) setPkgDone(false);
          }, 3000);
          notify(`Installed ${missingPkgs.join(", ")} in ${envName ?? "environment"}.`, "success");
          return;
        }
        if (env.status === "error") {
          notify("Package installation failed — check the environment logs.", "error");
          return;
        }
      }
      if (isCurrent()) {
        notify("Package installation is still building. Check the environment logs.", "error");
      }
    } catch {
      if (isCurrent()) notify("Failed to install packages — check the environment.", "error");
    } finally {
      window.clearInterval(ticker);
      if (installTickerRef.current === ticker) installTickerRef.current = null;
      if (isCurrent()) setPkgBusy(false);
    }
  }

  // Compute the data flowing into this node from upstream node outputs.
  const incomingInputs: Record<string, unknown> = {};
  for (const edge of edges) {
    if (edge.target !== nodeId) continue;
    const upstream = runOutputs[edge.source];
    if (!upstream || typeof upstream !== "object") continue;
    const sourceHandle = edge.sourceHandle ?? "main";
    const value = (upstream as Record<string, unknown>)[sourceHandle];
    if (value !== undefined) {
      incomingInputs[edge.targetHandle ?? "input"] = value;
    }
  }
  const inputData =
    Object.keys(incomingInputs).length > 0 ? incomingInputs : undefined;

  async function pin(): Promise<void> {
    if (!workflowId || runOutput === undefined) return;
    try {
      const saved = await api.pinNode(workflowId, nodeId, runOutput);
      setPinnedFor(nodeId, saved.payload, saved.updated_at);
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

  const outputData = pinned !== undefined ? pinned.payload : runOutput;
  const outputEmptyMessage = runMeta?.error
    ? "This node failed before producing output."
    : node.data.disabled
      ? "This node is disabled, so it will not produce output."
      : runStatus === "skipped"
        ? "This node was skipped in the last run."
        : runStatus
          ? "No output was captured for this node in the last run."
          : "This node has not run yet.";

  const outputFooter = (
    <div className="ndv-output-foot">
      {runStatus && (
        <span className={`run-pill status-run-${runStatus}`}>{runStatus}</span>
      )}
      {pinned !== undefined ? (
        <>
          <span className="pin-timestamp">Pinned {formatPinnedAt(pinned.updatedAt)}</span>
          <button className="btn btn-sm btn-ghost" onClick={() => void unpin()}>
            Unpin
          </button>
        </>
      ) : (
        runOutput !== undefined && (
          <button className="btn btn-sm" onClick={() => void pin()}>
            <PushPin size={13} /> Pin this output
          </button>
        )
      )}
    </div>
  );

  const isWebhookTrigger = node.data.manifest.id === "webhook_trigger";

  return (
    <div className="ndv-panels">
      {isWebhookTrigger ? (
        <section className="ndv-panel ndv-webhook-panel">
          <header className="ndv-panel-head">
            <h3>Trigger</h3>
            <span className="ndv-dir-badge">IN</span>
          </header>
          <div className="ndv-panel-body">
            <WebhookPanel
              path={String(node.data.params.path ?? "noodle")}
              nodeId={nodeId}
              onListeningChange={onListeningChange}
            />
          </div>
        </section>
      ) : (
        <DataPanel
          title="Input"
          data={inputData}
          emptyMessage="No upstream data yet. Run the workflow to see input here."
          dragPrefix="$json"
        />
      )}

      <section className="ndv-middle">
        <AgentWiringBanner nodeId={nodeId} />
        {missingPkgs.length > 0 && envId && (
          <div className="ndv-missing-pkgs warn-text">
            <p>
              This node needs <strong>{missingPkgs.join(", ")}</strong>, not
              installed in <strong>{envName ?? "this environment"}</strong>.
            </p>
            <div className="ndv-missing-actions">
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={pkgBusy}
                onClick={() => void addMissingToEnv()}
              >
                {pkgDone ? (
                  "Installed"
                ) : pkgBusy ? (
                  <span className="pkg-installing">
                    <span className="pkg-spinner" />
                    Installing… {pkgElapsed}s
                  </span>
                ) : (
                  `Add to ${envName ?? "env"}`
                )}
              </button>
              {satisfyingEnvs.length > 0 && applyEnvSwitch && (
                <select
                  className="field-input"
                  value=""
                  onChange={(e) => e.target.value && applyEnvSwitch(e.target.value)}
                >
                  <option value="">Switch environment…</option>
                  {satisfyingEnvs.map((env) => (
                    <option key={env.id} value={env.id}>
                      {env.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
        )}
        <div className="ndv-tabs" role="tablist" aria-label="Node configuration">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "parameters"}
            className={tab === "parameters" ? "active" : ""}
            onClick={() => selectTab("parameters")}
          >
            Parameters
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "settings"}
            className={tab === "settings" ? "active" : ""}
            onClick={() => selectTab("settings")}
          >
            Settings
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "docs"}
            className={tab === "docs" ? "active" : ""}
            onClick={() => selectTab("docs")}
          >
            Docs
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "credentials"}
            className={tab === "credentials" ? "active" : ""}
            onClick={() => selectTab("credentials")}
          >
            Credentials
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "logs"}
            className={tab === "logs" ? "active" : ""}
            onClick={() => selectTab("logs")}
          >
            Logs
          </button>
          {isAgent && (
            <button
              type="button"
              role="tab"
              aria-selected={tab === "trace"}
              className={tab === "trace" ? "active" : ""}
              onClick={() => selectTab("trace")}
            >
              Trace
            </button>
          )}
        </div>
        <div
          ref={middleBodyRef}
          className="ndv-middle-body"
          onScroll={(event) => {
            tabScroll.current[tab] = event.currentTarget.scrollTop;
          }}
        >
          {tab === "parameters" ? (
            <ParametersTab nodeId={nodeId} />
          ) : tab === "settings" ? (
            <SettingsTab nodeId={nodeId} />
          ) : tab === "docs" ? (
            <DocsTab nodeId={nodeId} />
          ) : tab === "credentials" ? (
            <CredentialsTab nodeId={nodeId} />
          ) : tab === "trace" ? (
            <TraceTab runMeta={runMeta as Record<string, unknown> | undefined} />
          ) : (
            <LogsTab nodeId={nodeId} />
          )}
        </div>
      </section>

      <DataPanel
        title={pinned !== undefined ? "Output (pinned)" : "Output"}
        data={outputData}
        emptyMessage={outputEmptyMessage}
        footer={outputFooter}
        logs={runMeta?.logs}
        error={runMeta?.error}
        status={runStatus}
        variables={runMeta?.debug?.variables}
        durationMs={runMeta?.durationMs}
        startedAt={runMeta?.startedAt}
        finishedAt={runMeta?.finishedAt}
        tokenUsage={runMeta?.tokenUsage}
      />
      {runId && runOutput !== undefined && (
        <ArtifactBrowser runId={runId} nodeId={nodeId} runOutput={runOutput} />
      )}
    </div>
  );
}
