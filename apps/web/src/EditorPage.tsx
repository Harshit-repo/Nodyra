import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, runEventsUrl } from "./api";
import { Canvas } from "./editor/Canvas";
import { FunctionsPanel } from "./editor/FunctionsPanel";
import { Inspector } from "./editor/Inspector";
import { NodeDetailModal } from "./editor/NodeDetailModal";
import { NodePalette } from "./editor/NodePalette";
import { PortDataViewer } from "./editor/PortDataViewer";
import { type RunOptions, useEditor } from "./editor/store";
import { Logo } from "./Logo";
import { useToast } from "./ToastProvider";
import type {
  AiWorkflowDraftResponse,
  Environment,
  GraphNode,
  RunEvent,
  RunInfo,
  WorkflowDetail,
  WorkflowGraph,
} from "./types";

interface WebhookListenState {
  nodeId: string;
  path: string;
  url: string;
  targets?: string[];
}

export function EditorPage() {
  const { id } = useParams<{ id: string }>();
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
  const [name, setName] = useState("");
  const [active, setActive] = useState(false);
  const [environmentId, setEnvironmentId] = useState<string | null>(null);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [runsOpen, setRunsOpen] = useState(false);
  const [runsList, setRunsList] = useState<RunInfo[]>([]);
  const [cancellingRun, setCancellingRun] = useState(false);
  const [webhookListen, setWebhookListen] = useState<WebhookListenState | null>(
    null,
  );
  const [functionsOpen, setFunctionsOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiPreview, setAiPreview] = useState<AiWorkflowDraftResponse | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const { notify } = useToast();
  const wsRef = useRef<WebSocket | null>(null);
  const webhookTimerRef = useRef<number | null>(null);

  const setManifests = useEditor((s) => s.setManifests);
  const loadGraph = useEditor((s) => s.loadGraph);
  const toGraph = useEditor((s) => s.toGraph);
  const markClean = useEditor((s) => s.markClean);
  const dirty = useEditor((s) => s.dirty);
  const nodeCount = useEditor((s) => s.nodes.length);
  const runId = useEditor((s) => s.runId);
  const running = useEditor((s) => s.running);
  const runError = useEditor((s) => s.runError);
  const runStatusMap = useEditor((s) => s.runStatus);
  const runOutputsMap = useEditor((s) => s.runOutputs);
  const runMetaMap = useEditor((s) => s.runMeta);
  const pinnedMap = useEditor((s) => s.pinned);
  const startRun = useEditor((s) => s.startRun);
  const applyRunEvent = useEditor((s) => s.applyRunEvent);
  const applyRunInfo = useEditor((s) => s.applyRunInfo);
  const setNodeOutput = useEditor((s) => s.setNodeOutput);
  const clearRun = useEditor((s) => s.clearRun);
  const ndvOpenId = useEditor((s) => s.ndvOpenId);
  const closeNdv = useEditor((s) => s.closeNdv);
  const setRunHandler = useEditor((s) => s.setRunHandler);
  const setWorkflowId = useEditor((s) => s.setWorkflowId);
  const setPinned = useEditor((s) => s.setPinned);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setStatus("loading");
    clearRun();
    closeNdv();
    (async () => {
      try {
        const [manifests, custom, detail, envs, pinnedList] = await Promise.all([
          api.nodes(),
          api.workflowCustomNodeManifests(id),
          api.getWorkflow(id),
          api.listEnvironments(),
          api.listPinned(id),
        ]);
        if (cancelled) return;
        setManifests([...manifests, ...custom]);
        loadGraph(detail.graph);
        setWorkflow(detail);
        setName(detail.name);
        setActive(detail.active);
        setEnvironmentId(detail.environment_id);
        setEnvironments(envs);
        setWorkflowId(id);
        const pinnedMap: Record<string, unknown> = {};
        for (const p of pinnedList) pinnedMap[p.node_id] = p.payload;
        setPinned(pinnedMap);
        setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        setMessage(String(err));
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, setManifests, loadGraph, clearRun, closeNdv, setWorkflowId, setPinned]);

  function stopWebhookListen(): void {
    if (webhookTimerRef.current !== null) {
      window.clearInterval(webhookTimerRef.current);
      webhookTimerRef.current = null;
    }
    setWebhookListen(null);
  }

  useEffect(
    () => () => {
      wsRef.current?.close();
      stopWebhookListen();
    },
    [],
  );

  async function save(
    options: { notifySuccess?: boolean } = {},
  ): Promise<WorkflowDetail | null> {
    if (!id) return null;
    const notifySuccess = options.notifySuccess ?? true;
    setSaving(true);
    setMessage("");
    try {
      const updated = await api.updateWorkflow(id, {
        name: name.trim() || "Untitled workflow",
        active,
        environment_id: environmentId ?? undefined,
        graph: toGraph(),
      });
      setWorkflow(updated);
      markClean();
      if (notifySuccess) notify("Draft saved.", "success");
      return updated;
    } catch (err) {
      setMessage(String(err));
      notify("Could not save draft.", "error");
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function publishDraft(): Promise<void> {
    if (!id || publishing) return;
    const saved = await save({ notifySuccess: false });
    if (!saved) return;
    setPublishing(true);
    setMessage("");
    try {
      const published = await api.publishWorkflow(id, {
        notes: "Published from editor",
      });
      const detail = await api.getWorkflow(id);
      setWorkflow(detail);
      setMessage(`Published v${published.version}. Deployments stay pinned until updated.`);
      notify(`Published v${published.version}.`, "success");
    } catch (err) {
      setMessage(String(err));
      notify("Could not publish workflow.", "error");
    } finally {
      setPublishing(false);
    }
  }

  async function previewAiDraft(): Promise<void> {
    if (!id || aiBusy || !aiPrompt.trim()) return;
    setAiBusy(true);
    setMessage("");
    try {
      const draft = await api.aiWorkflowDraft(id, {
        prompt: aiPrompt.trim(),
        apply: false,
      });
      setAiPreview(draft);
      notify("AI draft preview ready.", "success");
    } catch (err) {
      setMessage(String(err));
      notify("Could not build AI draft.", "error");
    } finally {
      setAiBusy(false);
    }
  }

  async function applyAiDraft(): Promise<void> {
    if (!id || !aiPreview) return;
    setAiBusy(true);
    setMessage("");
    try {
      await api.updateWorkflow(id, { graph: aiPreview.graph });
      loadGraph(aiPreview.graph);
      markClean();
      const detail = await api.getWorkflow(id);
      setWorkflow(detail);
      setAiOpen(false);
      setAiPreview(null);
      const missing = aiPreview.missing_credentials.length
        ? ` Missing credentials: ${aiPreview.missing_credentials.join(", ")}.`
        : "";
      setMessage(`${aiPreview.explanation}${missing}`);
      notify("AI draft applied.", "success");
    } catch (err) {
      setMessage(String(err));
      notify("Could not apply AI draft.", "error");
    } finally {
      setAiBusy(false);
    }
  }

  function openAiFixFailedRun(): void {
    const failedNodeId = Object.entries(runStatusMap).find(
      ([, status]) => status === "error",
    )?.[0];
    const failedError = failedNodeId ? runMetaMap[failedNodeId]?.error : runError;
    setAiPrompt(
      [
        "Fix this failed workflow run.",
        failedNodeId ? `Failed node id: ${failedNodeId}.` : "",
        failedError ? `Error: ${failedError}` : "",
        "Return an editable Noodle workflow draft that avoids the failure.",
      ]
        .filter(Boolean)
        .join("\n"),
    );
    setAiPreview(null);
    setAiOpen(true);
  }

  async function toggleActive(next: boolean): Promise<void> {
    // Save eagerly when the user flips Active so the change persists
    // without a separate Save click. Optimistically update the UI and
    // roll back if the request fails.
    if (!id) return;
    setActive(next);
    setSaving(true);
    setMessage("");
    try {
      const updated = await api.updateWorkflow(id, {
        name: name.trim() || "Untitled workflow",
        active: next,
        environment_id: environmentId ?? undefined,
        graph: toGraph(),
      });
      setWorkflow(updated);
      markClean();
      notify(next ? "Workflow activated." : "Workflow deactivated.", "success");
    } catch (err) {
      setActive(!next);
      setMessage(String(err));
      notify("Could not update workflow state.", "error");
    } finally {
      setSaving(false);
    }
  }

  type RunCache = Record<string, Record<string, unknown>>;

  function isPortOutput(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function upstreamNodeIds(graph: WorkflowGraph, targets?: string[]): Set<string> {
    const targetSet = targets && targets.length > 0 ? new Set(targets) : null;
    const upstream = new Set<string>();
    const seen = new Set<string>();
    if (!targetSet) return upstream;

    const visit = (nodeId: string) => {
      if (seen.has(nodeId)) return;
      seen.add(nodeId);
      for (const edge of graph.edges) {
        if (edge.target !== nodeId) continue;
        if (!targetSet.has(edge.source)) upstream.add(edge.source);
        visit(edge.source);
      }
    };
    for (const target of targetSet) visit(target);
    return upstream;
  }

  function reusableUpstreamCache(
    graph: WorkflowGraph,
    targets?: string[],
  ): RunCache | undefined {
    const cache: RunCache = {};
    for (const nodeId of upstreamNodeIds(graph, targets)) {
      const pinned = pinnedMap[nodeId];
      const output =
        pinned !== undefined
          ? pinned
          : runStatusMap[nodeId] === "success"
            ? runOutputsMap[nodeId]
            : undefined;
      if (isPortOutput(output)) cache[nodeId] = output;
    }
    return Object.keys(cache).length > 0 ? cache : undefined;
  }

  function plannedNodeIds(
    graph: WorkflowGraph,
    targets?: string[],
    cache?: RunCache,
  ): Set<string> {
    const planned = new Set<string>();
    const targetSet = targets && targets.length > 0 ? new Set(targets) : null;
    if (!targetSet) {
      for (const node of graph.nodes) planned.add(node.id);
      return planned;
    }

    const visit = (nodeId: string) => {
      if (planned.has(nodeId)) return;
      planned.add(nodeId);
      if (cache?.[nodeId]) return;
      for (const edge of graph.edges) {
        if (edge.target === nodeId) visit(edge.source);
      }
    };
    for (const target of targetSet) visit(target);
    return planned;
  }

  function webhookForRun(
    graph: WorkflowGraph,
    targets?: string[],
    cache?: RunCache,
  ): GraphNode | null {
    const planned = plannedNodeIds(graph, targets, cache);
    return (
      graph.nodes.find(
        (node) =>
          planned.has(node.id) &&
          node.type === "webhook_trigger" &&
          !cache?.[node.id],
      ) ?? null
    );
  }

  function connectRunStream(runId: string, targets?: string[]): void {
    startRun(runId, targets);
    const ws = new WebSocket(runEventsUrl(runId));
    wsRef.current = ws;
    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data as string) as RunEvent;
      applyRunEvent(payload);
      if (payload.type === "run_finished") {
        notify(
          payload.status === "success"
            ? "Workflow run succeeded."
            : `Workflow run ${payload.status ?? "finished"}.`,
          payload.status === "success" ? "success" : "error",
        );
      } else if (payload.type === "run_error") {
        notify(payload.error ? `Run failed: ${payload.error}` : "Run failed.", "error");
      }
    };
    ws.onclose = () => {
      wsRef.current = null;
    };
  }

  async function startWebhookTestRun(
    node: GraphNode,
    targets?: string[],
    cache?: RunCache,
  ): Promise<void> {
    if (!id || webhookListen) return;
    const path = String(node.params.path ?? "").trim() || "noodle";
    const url = `${window.location.origin}/api/webhook-test/${path}`;
    const runTargets =
      targets?.length === 1 && targets[0] === node.id ? undefined : targets;

    setMessage("");
    setNodeOutput(node.id, undefined);
    try {
      await api.clearWebhook(path);
    } catch (err) {
      setMessage(String(err));
      return;
    }

    setWebhookListen({ nodeId: node.id, path, url, targets: runTargets });
    notify("Listening for test webhook.", "info");
    webhookTimerRef.current = window.setInterval(async () => {
      try {
        const data = await api.lastWebhook(path);
        if (data === null || data === undefined) return;

        stopWebhookListen();
        setNodeOutput(node.id, { main: data }, "success");
        notify("Webhook test event captured.", "success");
        const runCache = {
          ...(cache ?? {}),
          [node.id]: { main: data },
        };
        const { run_id } = await api.runWorkflow(id, {
          mode: "manual",
          targets: runTargets,
          cache: runCache,
        });
        connectRunStream(run_id, runTargets);
      } catch (err) {
        stopWebhookListen();
        setMessage(String(err));
      }
    }, 1000);
  }

  async function run(
    targets?: string[],
    options: RunOptions = {},
  ): Promise<void> {
    if (!id || running || webhookListen) return;
    if (nodeCount === 0) {
      notify("Add a node before running.", "error");
      return;
    }
    const saved = await save({ notifySuccess: false });
    if (!saved) return;
    const cache = options.reuseUpstream
      ? reusableUpstreamCache(saved.graph, targets)
      : undefined;
    const webhookNode = webhookForRun(saved.graph, targets, cache);
    if (webhookNode) {
      await startWebhookTestRun(webhookNode, targets, cache);
      return;
    }
    try {
      const body: {
        targets?: string[];
        cache?: Record<string, Record<string, unknown>>;
      } = {};
      if (targets && targets.length > 0) body.targets = targets;
      if (cache) body.cache = cache;
      const { run_id } = await api.runWorkflow(id, body);
      if (cache && Object.keys(cache).length > 0) {
        notify(`Reused ${Object.keys(cache).length} upstream output(s).`, "info");
      }
      connectRunStream(run_id, targets);
    } catch (err) {
      setMessage(String(err));
      notify("Could not start workflow run.", "error");
    }
  }

  async function cancelCurrentRun(): Promise<void> {
    if (!runId || cancellingRun) return;
    setCancellingRun(true);
    try {
      const result = await api.cancelRun(runId);
      if (result.status === "cancelled") {
        applyRunEvent({
          type: "run_cancelled",
          run_id: runId,
          error: "Run cancelled",
        });
      } else if (result.status !== "cancelling") {
        applyRunEvent({
          type: "run_finished",
          run_id: runId,
          status: result.status,
        });
      }
    } catch (err) {
      setMessage(String(err));
      notify("Could not cancel run.", "error");
    } finally {
      setCancellingRun(false);
    }
  }

  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    setRunHandler((targets, options) => runRef.current(targets, options));
    return () => setRunHandler(null);
  }, [setRunHandler]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  async function openRuns(): Promise<void> {
    if (!id) return;
    if (runsOpen) {
      setRunsOpen(false);
      return;
    }
    try {
      const list = await api.listRuns(id);
      setRunsList(list);
      setRunsOpen(true);
    } catch (err) {
      setMessage(String(err));
    }
  }

  async function viewRun(runId: string): Promise<void> {
    setRunsOpen(false);
    try {
      const run = await api.getRun(runId);
      applyRunInfo(run);
    } catch (err) {
      setMessage(String(err));
    }
  }

  if (status === "loading") {
    return <div className="screen-center muted">Loading workflow…</div>;
  }
  if (status === "error") {
    return (
      <div className="screen-center">
        <p className="error-text">{message}</p>
        <Link className="btn" to="/">
          Back to workflows
        </Link>
      </div>
    );
  }

  return (
    <div className="editor">
      <header className="toolbar">
        <div className="toolbar-left">
          <Link to="/" className="toolbar-home" title="All workflows">
            <Logo size={22} />
          </Link>
          <span className="toolbar-sep" />
          <input
            className="toolbar-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            spellCheck={false}
          />
          <span className="toolbar-meta">
            published v{workflow?.published_version ?? workflow?.version}
            {workflow?.has_unpublished_changes ? " · unpublished draft" : ""}
            {" · "}
            {nodeCount} node{nodeCount === 1 ? "" : "s"}
          </span>
        </div>
        <div className="toolbar-right">
          <select
            className="toolbar-env"
            title="Run environment"
            value={environmentId ?? ""}
            onChange={(e) => setEnvironmentId(e.target.value || null)}
          >
            {environments.length === 0 && <option value="">No environment</option>}
            {environments.map((env) => (
              <option key={env.id} value={env.id}>
                {env.name}
              </option>
            ))}
          </select>
          <label className="active-toggle">
            <input
              type="checkbox"
              checked={active}
              onChange={(e) => void toggleActive(e.target.checked)}
              disabled={saving}
            />
            <span className="active-track" />
            <span>{active ? "Active" : "Inactive"}</span>
          </label>
          <div className="runs-menu">
            <button className="btn" onClick={() => void openRuns()}>
              Runs ▾
            </button>
            {runsOpen && (
              <div
                className="runs-dropdown"
                onMouseLeave={() => setRunsOpen(false)}
              >
                {runsList.length === 0 && (
                  <p className="runs-empty muted">No runs yet.</p>
                )}
                {runsList.map((r) => (
                  <button
                    key={r.id}
                    className="runs-item"
                    onClick={() => void viewRun(r.id)}
                  >
                    <span className={`run-pill status-run-${r.status}`}>
                      {r.status}
                    </span>
                    <span className="runs-item-meta">
                      {new Date(r.started_at).toLocaleString()} · {r.trigger_type}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            className="btn"
            onClick={() => setFunctionsOpen(true)}
            title="Upload Python files; their functions appear in the palette"
          >
            ƒ Functions
          </button>
          <button
            className="btn"
            onClick={() => {
              setAiPreview(null);
              setAiOpen(true);
            }}
            title="Generate an editable draft from a natural-language prompt"
          >
            AI Draft
          </button>
          <div className="export-menu">
            <button className="btn" onClick={() => setExportOpen((o) => !o)}>
              Export ▾
            </button>
            {exportOpen && (
              <div
                className="export-dropdown"
                onMouseLeave={() => setExportOpen(false)}
              >
                <a
                  href={`/api/workflows/${id}/export.py`}
                  onClick={() => setExportOpen(false)}
                >
                  Python script (.py)
                </a>
                <a
                  href={`/api/workflows/${id}/export/docker`}
                  onClick={() => setExportOpen(false)}
                >
                  Docker bundle (.zip)
                </a>
              </div>
            )}
          </div>
          <button
            className="btn btn-run"
            onClick={() => void run()}
            disabled={running || Boolean(webhookListen)}
          >
            {webhookListen ? (
              <>
                <span className="node-spinner" />
                Listening…
              </>
            ) : running ? (
              "Running…"
            ) : (
              "▶ Run"
            )}
          </button>
          {running && (
            <button
              className="btn btn-danger"
              onClick={() => void cancelCurrentRun()}
              disabled={cancellingRun}
            >
              {cancellingRun ? "Stopping…" : "■ Stop"}
            </button>
          )}
          <button className="btn btn-primary" onClick={() => void save()} disabled={saving}>
            {dirty && <span className="dirty-dot" />}
            {saving ? "Saving…" : "Save draft"}
          </button>
          <button
            className="btn"
            onClick={() => void publishDraft()}
            disabled={saving || publishing}
            title="Publish the saved draft as a new production version"
          >
            {publishing ? "Publishing…" : "Publish"}
          </button>
        </div>
      </header>

      {message && <div className="toolbar-error">{message}</div>}
      {webhookListen && (
        <div className="webhook-test-banner">
          <div className="webhook-test-copy">
            <span className="webhook-test-pulse" aria-hidden />
            <strong>Listening for test webhook</strong>
            <span>{webhookListen.path}</span>
            <code>{webhookListen.url}</code>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={stopWebhookListen}>
            Stop
          </button>
        </div>
      )}
      {runError && (
        <div className="toolbar-error run-error-summary">
          <span>
            {runError === "Run cancelled" || runError === "Run failed"
              ? runError
              : `Run failed: ${runError}`}
          </span>
          <button className="btn btn-sm btn-ghost" onClick={openAiFixFailedRun}>
            Fix with AI
          </button>
        </div>
      )}

      <ReactFlowProvider>
        <div className="editor-body">
          <NodePalette />
          <div className="editor-stage">
            <Canvas />
            <div className="canvas-fab">
              {running ? (
                <button
                  type="button"
                  className="canvas-run-btn is-running"
                  onClick={() => void cancelCurrentRun()}
                  disabled={cancellingRun}
                >
                  {cancellingRun ? "Stopping…" : "■ Stop"}
                </button>
              ) : (
                <button
                  type="button"
                  className="canvas-run-btn"
                  onClick={() => void run()}
                  disabled={Boolean(webhookListen) || saving}
                  title="Execute the whole workflow"
                >
                  ▶ Execute Workflow
                </button>
              )}
            </div>
            <PortDataViewer />
          </div>
          <Inspector />
        </div>
      </ReactFlowProvider>

      {ndvOpenId && <NodeDetailModal nodeId={ndvOpenId} />}

      {aiOpen && (
        <div
          className="modal-overlay"
          onClick={() => {
            setAiOpen(false);
            setAiPreview(null);
          }}
        >
          <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <h2>AI workflow draft</h2>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => {
                  setAiOpen(false);
                  setAiPreview(null);
                }}
              >
                ✕
              </button>
            </header>
            <div className="modal-body">
              <p className="field-desc">
                Creates a normal editable draft on the canvas. Review credentials
                and parameters before publishing.
              </p>
              <textarea
                className="field-input field-code"
                rows={6}
                value={aiPrompt}
                onChange={(e) => setAiPrompt(e.target.value)}
                placeholder="When a GitHub issue is opened, summarize it with OpenAI and post to Slack."
                spellCheck={false}
              />
              {aiPreview && (
                <div className="ai-preview">
                  <div className="ai-preview-head">
                    <strong>{aiPreview.graph.nodes.length} nodes</strong>
                    <span>{aiPreview.graph.edges.length} connections</span>
                  </div>
                  <p>{aiPreview.explanation}</p>
                  {aiPreview.missing_credentials.length > 0 && (
                    <p className="error-text">
                      Missing credentials: {aiPreview.missing_credentials.join(", ")}
                    </p>
                  )}
                  {aiPreview.required_packages.length > 0 && (
                    <p className="field-desc">
                      Packages: {aiPreview.required_packages.join(", ")}
                    </p>
                  )}
                  {aiPreview.assumptions.length > 0 && (
                    <ul className="ai-preview-list">
                      {aiPreview.assumptions.map((assumption) => (
                        <li key={assumption}>{assumption}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
            <footer className="modal-foot">
              <button
                className="btn btn-ghost"
                onClick={() => {
                  setAiOpen(false);
                  setAiPreview(null);
                }}
                disabled={aiBusy}
              >
                Cancel
              </button>
              <button
                className="btn"
                onClick={() => void previewAiDraft()}
                disabled={aiBusy || !aiPrompt.trim()}
              >
                {aiBusy ? "Building..." : "Preview draft"}
              </button>
              {aiPreview && (
                <button
                  className="btn btn-primary"
                  onClick={() => void applyAiDraft()}
                  disabled={aiBusy}
                >
                  Apply draft
                </button>
              )}
            </footer>
          </div>
        </div>
      )}

      {functionsOpen && id && (
        <FunctionsPanel
          workflowId={id}
          onClose={() => setFunctionsOpen(false)}
          onChanged={async () => {
            const [builtins, custom] = await Promise.all([
              api.nodes(),
              api.workflowCustomNodeManifests(id),
            ]);
            setManifests([...builtins, ...custom]);
          }}
          onApplyStarterGraph={(graph) => {
            // Apply locally and mark the workflow dirty; the user reviews on
            // the canvas and clicks Save to persist.
            loadGraph(graph, { dirty: true });
          }}
        />
      )}
    </div>
  );
}
