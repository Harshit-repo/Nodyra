import { useEffect, useState } from "react";

import { api } from "../api";
import { useToast } from "../ToastProvider";
import type { ArtifactInfo, ParamSpec } from "../types";
import { missingFor } from "./missingPackages";
import { useServerPlatform } from "../hooks/useServerPlatform";
import { DataPanel } from "./DataPanel";
import {
  ParamField,
  WebhookPanel,
  WEBHOOK_AUTH_TYPE_OPTIONS,
  formatParamLabel,
  groupActiveByValue,
  paramGroup,
  webhookCredentialSpec,
  webhookHiddenParam,
  webhookParamLabel,
  NodeCodePanel,
  ToolModeSection,
  FromAiParamControl,
} from "./NodeDetails";
import { isFromAiExpr } from "./toolParam";
import { useEditor } from "./store";
import { asArtifactRef, artifactDownloadUrl, artifactSummary, formatBytes } from "./artifactValues";

/**
 * The three-column body of the NDV modal: Input | Parameters/Settings | Output.
 *
 * The user sees the data flowing in on the left, configures the node in
 * the middle, and inspects what came out on the right.
 */

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
      <ToolModeSection nodeId={node.id} />
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
                {spec.required && <span className="field-req">required</span>}
              </div>
              <FromAiParamControl
                nodeId={node.id}
                spec={renderSpec}
                value={value}
                onSetParam={setParam}
              />
              {spec.description && (
                <p className="field-desc">{spec.description}</p>
              )}
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
                />
              )}
            </div>
          );
        };

        const visible = manifest.params.filter(
          (spec) => !webhookHiddenParam(manifest.id, spec.name, params),
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

function ArtifactBrowser({ runId, runOutput }: { runId: string; runOutput: unknown }) {
  const [artifacts, setArtifacts] = useState<ArtifactInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.listRunArtifacts(runId).then(
      (list: ArtifactInfo[]) => { if (!cancelled) setArtifacts(list); },
      (err: unknown) => { if (!cancelled) setError(String(err)); },
    );
    return () => { cancelled = true; };
  }, [runId]);

  // Also scan runOutput for embedded ArtifactRef objects
  const embeddedRefs: Array<ReturnType<typeof asArtifactRef> & object> = [];
  if (runOutput && typeof runOutput === "object") {
    for (const val of Object.values(runOutput as Record<string, unknown>)) {
      const ref = asArtifactRef(val);
      if (ref) embeddedRefs.push(ref);
    }
  }

  const apiArtifacts = artifacts ?? [];
  const hasContent = apiArtifacts.length > 0 || embeddedRefs.length > 0;

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
        const token = localStorage.getItem("noodle_token");
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
      {embeddedRefs.map((ref) => {
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

export function NDVPanels({ nodeId }: { nodeId: string }) {
  const [tab, setTab] = useState<"parameters" | "settings" | "docs" | "credentials" | "logs">("parameters");
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
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
  const { notify } = useToast();
  const platform = useServerPlatform();

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
    setPkgBusy(true);
    try {
      const updated = [...envPackages, ...missingPkgs];
      await api.setPackages(envId, updated);
      setEnvPackages(updated);
      notify(`Added ${missingPkgs.join(", ")} to ${envName ?? "environment"}.`, "success");
    } catch {
      notify("Failed to install packages — check the environment.", "error");
    } finally {
      setPkgBusy(false);
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

  const outputData = pinned !== undefined ? pinned : runOutput;
  const outputEmptyMessage = runMeta?.error
    ? "This node failed before producing output."
    : runStatus === "skipped"
      ? "This node was skipped in the last run."
      : "No output yet. Click Run to execute the workflow.";

  const outputFooter = (
    <div className="ndv-output-foot">
      {runStatus && (
        <span className={`run-pill status-run-${runStatus}`}>{runStatus}</span>
      )}
      {pinned !== undefined ? (
        <button className="btn btn-sm btn-ghost" onClick={() => void unpin()}>
          Unpin
        </button>
      ) : (
        runOutput !== undefined && (
          <button className="btn btn-sm" onClick={() => void pin()}>
            📌 Pin this output
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
          </header>
          <div className="ndv-panel-body">
            <WebhookPanel
              path={String(node.data.params.path ?? "noodle")}
              nodeId={nodeId}
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
                {pkgBusy ? "Adding…" : `Add to ${envName ?? "env"}`}
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
        <div className="ndv-tabs">
          <button
            type="button"
            className={tab === "parameters" ? "active" : ""}
            onClick={() => setTab("parameters")}
          >
            Parameters
          </button>
          <button
            type="button"
            className={tab === "settings" ? "active" : ""}
            onClick={() => setTab("settings")}
          >
            Settings
          </button>
          <button
            type="button"
            className={tab === "docs" ? "active" : ""}
            onClick={() => setTab("docs")}
          >
            Docs
          </button>
          <button
            type="button"
            className={tab === "credentials" ? "active" : ""}
            onClick={() => setTab("credentials")}
          >
            Credentials
          </button>
          <button
            type="button"
            className={tab === "logs" ? "active" : ""}
            onClick={() => setTab("logs")}
          >
            Logs
          </button>
        </div>
        <div className="ndv-middle-body">
          {tab === "parameters" ? (
            <ParametersTab nodeId={nodeId} />
          ) : tab === "settings" ? (
            <SettingsTab nodeId={nodeId} />
          ) : tab === "docs" ? (
            <DocsTab nodeId={nodeId} />
          ) : tab === "credentials" ? (
            <CredentialsTab nodeId={nodeId} />
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
      />
      {runId && runOutput !== undefined && (
        <ArtifactBrowser runId={runId} runOutput={runOutput} />
      )}
    </div>
  );
}
