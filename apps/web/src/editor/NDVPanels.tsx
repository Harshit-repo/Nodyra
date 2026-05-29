import { useState } from "react";

import { api } from "../api";
import { DataPanel } from "./DataPanel";
import {
  ParamField,
  WebhookPanel,
  WEBHOOK_AUTH_TYPE_OPTIONS,
  formatParamLabel,
  webhookCredentialSpec,
  webhookHiddenParam,
  webhookParamLabel,
} from "./NodeDetails";
import { useEditor } from "./store";

/**
 * The three-column body of the NDV modal: Input | Parameters/Settings | Output.
 *
 * The user sees the data flowing in on the left, configures the node in
 * the middle, and inspects what came out on the right.
 */

function ParametersTab({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const updateParams = useEditor((s) => s.updateParams);
  if (!node) return null;
  const { manifest, params } = node.data;

  const setParam = (name: string, value: unknown) => {
    updateParams(node.id, { ...params, [name]: value });
  };

  return (
    <>
      <p className="expr-hint field-desc">
        Use <code>{"{{ $json.field }}"}</code> or{" "}
        <code>{'{{ $node["nodeId"].main.field }}'}</code> in string fields to
        reference upstream data.
      </p>
      {manifest.params.length === 0 && (
        <p className="muted">This node has no parameters.</p>
      )}
      {manifest.params
        .filter((spec) => !webhookHiddenParam(manifest.id, spec.name, params))
        .map((spec) => {
          const value = params[spec.name];
          const displayLabel =
            webhookParamLabel(manifest.id, spec.name, params) ??
            formatParamLabel(spec.name);

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
              {spec.description && (
                <p className="field-desc">{spec.description}</p>
              )}
              {isWebhookAuthType ? (
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
        })}
      {manifest.id === "webhook_trigger" && (
        <WebhookPanel
          path={String(params.path ?? "noodle")}
          nodeId={node.id}
        />
      )}
    </>
  );
}

function SettingsTab({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const updateNodeSettings = useEditor((s) => s.updateNodeSettings);
  if (!node) return null;
  const data = node.data;
  const defaultTimeout =
    data.manifest.id === "code"
      ? 60
      : data.manifest.id === "http_request"
        ? 45
        : null;

  return (
    <>
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

export function NDVPanels({ nodeId }: { nodeId: string }) {
  const [tab, setTab] = useState<"parameters" | "settings" | "docs" | "credentials" | "logs">("parameters");
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const edges = useEditor((s) => s.edges);
  const runOutputs = useEditor((s) => s.runOutputs);
  const runOutput = useEditor((s) => s.runOutputs[nodeId]);
  const runStatus = useEditor((s) => s.runStatus[nodeId]);
  const runMeta = useEditor((s) => s.runMeta[nodeId]);
  const workflowId = useEditor((s) => s.workflowId);
  const pinned = useEditor((s) => s.pinned[nodeId]);
  const setPinnedFor = useEditor((s) => s.setPinnedFor);

  if (!node) {
    return (
      <div className="inspector-empty">
        <p>This node is no longer in the workflow.</p>
      </div>
    );
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

  return (
    <div className="ndv-panels">
      <DataPanel
        title="Input"
        data={inputData}
        emptyMessage="No upstream data yet. Run the workflow to see input here."
        dragPrefix="$json"
      />

      <section className="ndv-middle">
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
    </div>
  );
}
