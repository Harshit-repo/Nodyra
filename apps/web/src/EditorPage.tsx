import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, runEventsUrl } from "./api";
import { Canvas } from "./editor/Canvas";
import { Inspector } from "./editor/Inspector";
import { NodeDetailModal } from "./editor/NodeDetailModal";
import { NodePalette } from "./editor/NodePalette";
import { useEditor } from "./editor/store";
import { Logo } from "./Logo";
import type {
  Environment,
  RunEvent,
  RunInfo,
  WorkflowDetail,
} from "./types";

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
  const wsRef = useRef<WebSocket | null>(null);

  const setManifests = useEditor((s) => s.setManifests);
  const loadGraph = useEditor((s) => s.loadGraph);
  const toGraph = useEditor((s) => s.toGraph);
  const markClean = useEditor((s) => s.markClean);
  const dirty = useEditor((s) => s.dirty);
  const nodeCount = useEditor((s) => s.nodes.length);
  const running = useEditor((s) => s.running);
  const runError = useEditor((s) => s.runError);
  const startRun = useEditor((s) => s.startRun);
  const applyRunEvent = useEditor((s) => s.applyRunEvent);
  const applyRunInfo = useEditor((s) => s.applyRunInfo);
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
        const [manifests, detail, envs, pinnedList] = await Promise.all([
          api.nodes(),
          api.getWorkflow(id),
          api.listEnvironments(),
          api.listPinned(id),
        ]);
        if (cancelled) return;
        setManifests(manifests);
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

  useEffect(() => () => wsRef.current?.close(), []);

  async function save(): Promise<WorkflowDetail | null> {
    if (!id) return null;
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
      return updated;
    } catch (err) {
      setMessage(String(err));
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function run(targets?: string[]): Promise<void> {
    if (!id || running) return;
    const saved = await save();
    if (!saved) return;
    try {
      const { run_id } = await api.runWorkflow(
        id,
        targets && targets.length > 0 ? { targets } : {},
      );
      startRun(run_id);
      const ws = new WebSocket(runEventsUrl(run_id));
      wsRef.current = ws;
      ws.onmessage = (event) => {
        applyRunEvent(JSON.parse(event.data as string) as RunEvent);
      };
      ws.onclose = () => {
        wsRef.current = null;
      };
    } catch (err) {
      setMessage(String(err));
    }
  }

  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    setRunHandler((targets) => runRef.current(targets));
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
            v{workflow?.version} · {nodeCount} node{nodeCount === 1 ? "" : "s"}
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
              onChange={(e) => setActive(e.target.checked)}
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
            disabled={running}
          >
            {running ? "Running…" : "▶ Run"}
          </button>
          <button className="btn btn-primary" onClick={() => void save()} disabled={saving}>
            {dirty && <span className="dirty-dot" />}
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </header>

      {message && <div className="toolbar-error">{message}</div>}
      {runError && <div className="toolbar-error">Run failed: {runError}</div>}

      <ReactFlowProvider>
        <div className="editor-body">
          <NodePalette />
          <Canvas />
          <Inspector />
        </div>
      </ReactFlowProvider>

      {ndvOpenId && <NodeDetailModal nodeId={ndvOpenId} />}
    </div>
  );
}
