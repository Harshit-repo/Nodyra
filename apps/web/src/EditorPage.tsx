import { ReactFlowProvider } from "@xyflow/react";
import { Keyboard } from "@phosphor-icons/react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker, useParams } from "react-router-dom";

import { api, errorMessage, getOrgId, getToken, type RunStreamHandle, subscribeToRunEvents } from "./api";
import { AiDraftModal } from "./AiDraftModal";
import { ConfirmDialog } from "./ConfirmDialog";
import { RunnerPoolSelect } from "./RunnerPoolSelect";
import { Canvas } from "./editor/Canvas";
import { ChatPanel } from "./editor/ChatPanel";
import { CommandPalette } from "./editor/CommandPalette";
import { FunctionsPanel } from "./editor/FunctionsPanel";
import { Inspector } from "./editor/Inspector";
import { NodePalette } from "./editor/NodePalette";
import { PortDataViewer } from "./editor/PortDataViewer";
import { WorkflowHistory } from "./editor/WorkflowHistory";
import {
  childToGraph,
  pickEditorRunTrigger,
  type EditorStore,
  type PinnedOutput,
  type RunOptions,
  useEditor,
} from "./editor/store";
import { Logo } from "./Logo";
import { useCan } from "./permissions";
import {
  useEnvironments,
  useNodes,
  usePinned,
  useRunnerPools,
  useTriggerManualPullMutation,
  useWorkflow,
  useWorkflowCustomNodeManifests,
} from "./queries";
import { GitHubSyncBadge } from "./editor/GitHubSyncBadge";
import { GitHubConflictModal } from "./editor/GitHubConflictModal";
import { RunApprovalsPanel } from "./RunApprovalsPanel";
import { useConfirm } from "./ConfirmProvider";
import { useToast } from "./ToastProvider";
import { A11yModal } from "./editor/A11yModal";
import { deriveBarStatus } from "./editor/barStatus";
import { useAutosave } from "./editor/useAutosave";
import { SaveIndicator, type SaveState } from "./editor/SaveIndicator";
import { PublishPill } from "./editor/PublishPill";
import { OverflowMenu, type OverflowItem } from "./editor/OverflowMenu";
import { WorkflowSettingsModal } from "./editor/WorkflowSettingsModal";
import { RunSidecar } from "./editor/RunSidecar";
import type {
  AiDraftMode,
  AiFixStrategy,
  AiWorkflowDraftResponse,
  Environment,
  GraphNode,
  RunEvent,
  RunnerPoolInfo,
  WorkflowDetail,
  WorkflowGraph,
  WorkflowVersionInfo,
} from "./types";

const NodeDetailModal = lazy(() =>
  import("./editor/NodeDetailModal").then((module) => ({
    default: module.NodeDetailModal,
  })),
);

interface WebhookListenState {
  nodeId: string;
  path: string;
  url: string;
  targets?: string[];
}

interface PublishSummary {
  addedNodes: number;
  removedNodes: number;
  edgeDelta: number;
  credentialRefs: number;
  triggerSummary: string;
  triggerChanged: boolean;
  environmentChanged: boolean;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return (
    tag === "input" ||
    tag === "textarea" ||
    tag === "select" ||
    target.isContentEditable
  );
}

function isCredentialRef(value: unknown): boolean {
  return (
    Boolean(value) &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).__noodle_credential__ === true
  );
}

function countCredentialRefs(value: unknown): number {
  if (isCredentialRef(value)) return 1;
  if (Array.isArray(value)) {
    return value.reduce<number>(
      (total, item) => total + countCredentialRefs(item),
      0,
    );
  }
  if (value && typeof value === "object") {
    return Object.values(value as Record<string, unknown>).reduce<number>(
      (total, item) => total + countCredentialRefs(item),
      0,
    );
  }
  return 0;
}

function triggerTypes(graph: WorkflowGraph | null | undefined): string[] {
  if (!graph) return [];
  return graph.nodes
    .filter((node) => node.type.endsWith("_trigger"))
    .map((node) => node.type)
    .sort();
}

function pinnedPayload(pin: PinnedOutput | undefined): unknown {
  return pin?.payload;
}

function buildPublishSummary(
  baseGraph: WorkflowGraph | null | undefined,
  currentGraph: WorkflowGraph,
  baseEnvironmentId: string | null | undefined,
  currentEnvironmentId: string | null,
): PublishSummary {
  const baseNodeIds = new Set(baseGraph?.nodes.map((node) => node.id) ?? []);
  const currentNodeIds = new Set(currentGraph.nodes.map((node) => node.id));
  const baseTriggers = triggerTypes(baseGraph);
  const currentTriggers = triggerTypes(currentGraph);
  return {
    addedNodes: currentGraph.nodes.filter((node) => !baseNodeIds.has(node.id))
      .length,
    removedNodes: [...baseNodeIds].filter((id) => !currentNodeIds.has(id)).length,
    edgeDelta: currentGraph.edges.length - (baseGraph?.edges.length ?? 0),
    credentialRefs: countCredentialRefs(currentGraph.nodes),
    triggerSummary: currentTriggers.length ? currentTriggers.join(", ") : "none",
    triggerChanged: baseTriggers.join("|") !== currentTriggers.join("|"),
    environmentChanged: (baseEnvironmentId ?? "") !== (currentEnvironmentId ?? ""),
  };
}

// ---- RunSettingsChip: stacked env+runner chip that opens a popover ----

function RunSettingsChip({
  environments,
  environmentId,
  onEnvChange,
  pools,
  defaultRunnerPoolId,
  onRunnerChange,
  saving,
}: {
  environments: Environment[];
  environmentId: string | null;
  onEnvChange: (id: string | null) => void;
  pools: RunnerPoolInfo[];
  defaultRunnerPoolId: string | null;
  onRunnerChange: (id: string | null) => void;
  saving: boolean;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const envName = environments.find((e) => e.id === environmentId)?.name ?? "No env";
  const poolName = pools.find((p) => p.id === defaultRunnerPoolId)?.name ?? null;

  return (
    <div className="run-settings-wrap" ref={wrapRef}>
      <button
        type="button"
        className={`run-settings-chip${open ? " is-open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Run settings"
      >
        {saving ? (
          <span className="chip-saving">saving…</span>
        ) : (
          <>
            <span className="chip-env">
              {envName}
              <span className="chip-caret">▾</span>
            </span>
            {poolName && (
              <span className="chip-runner">
                <span style={{ color: "#22c55e", lineHeight: 1 }}>●</span>
                {poolName}
              </span>
            )}
          </>
        )}
      </button>
      {open && (
        <div className="run-settings-popover">
          <div className="rsp-title">Run settings</div>
          <div className="rsp-section">
            <div className="rsp-label">Environment</div>
            <select
              className="rsp-env-select"
              value={environmentId ?? ""}
              onChange={(e) => onEnvChange(e.target.value || null)}
            >
              {environments.map((env) => (
                <option key={env.id} value={env.id}>
                  {env.name}
                </option>
              ))}
            </select>
          </div>
          <div className="rsp-divider" />
          <div className="rsp-section">
            <div className="rsp-label">Runner override</div>
            <RunnerPoolSelect
              pools={pools}
              value={defaultRunnerPoolId}
              onChange={onRunnerChange}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// Stable selectors defined outside the component so their references never
// change between renders, preventing needless Zustand re-subscriptions.
const selectHasTrigger = (s: EditorStore) =>
  // While drilled into a metanode the live nodes are the interior (no trigger);
  // runs fold up to the root, so check the root graph for a trigger.
  pickEditorRunTrigger(s.drillStack.length > 0 ? s.drillStack[0]!.nodes : s.nodes) !== null;
const selectHasChatTrigger = (s: EditorStore) =>
  s.nodes.some((n) => n.data.manifest?.id === "chat_trigger");
const selectChatTriggerParams = (s: EditorStore) => {
  const node = s.nodes.find((n) => n.data.manifest?.id === "chat_trigger");
  if (!node) return null;
  return node.data.params as Record<string, string>;
};


export function EditorPage() {
  const { id } = useParams<{ id: string }>();
  const workflowQuery = useWorkflow(id ?? null);
  const nodesQuery = useNodes();
  const customNodesQuery = useWorkflowCustomNodeManifests(id ?? null);
  const environmentsQuery = useEnvironments();
  const runnerPoolsQuery = useRunnerPools();
  const pinnedQuery = usePinned(id ?? null);
const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
  const [name, setName] = useState("");
  const [active, setActive] = useState(false);
  const [environmentId, setEnvironmentId] = useState<string | null>(null);
  const [defaultRunnerPoolId, setDefaultRunnerPoolId] = useState<string | null>(null);
  const [chipSaving, setChipSaving] = useState(false);
  const [runTimeout, setRunTimeout] = useState<string>("");
  const [mcpEnabled, setMcpEnabled] = useState(false);
  const [mcpToolName, setMcpToolName] = useState("");
  const [mcpDescription, setMcpDescription] = useState("");
  const environments = environmentsQuery.data ?? [];
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [sidecarOpen, setSidecarOpen] = useState(false);
  const [cancellingRun, setCancellingRun] = useState(false);
  const [webhookListen, setWebhookListen] = useState<WebhookListenState | null>(
    null,
  );
  const [functionsOpen, setFunctionsOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiMode, setAiMode] = useState<AiDraftMode>("draft");
  const [aiFixStrategy, setAiFixStrategy] = useState<AiFixStrategy>("minimal");
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiPreview, setAiPreview] = useState<AiWorkflowDraftResponse | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiFailedNodeId, setAiFailedNodeId] = useState<string | null>(null);
  const [aiFailedError, setAiFailedError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [publishReviewOpen, setPublishReviewOpen] = useState(false);
  const [publishUpdateDeployments, setPublishUpdateDeployments] = useState(false);
  const [publishSummary, setPublishSummary] = useState<PublishSummary | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [restoreVersion, setRestoreVersion] = useState<WorkflowVersionInfo | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [publishNotes, setPublishNotes] = useState("");
  const [saveError, setSaveError] = useState(false);
  const [togglingActive, setTogglingActive] = useState(false);
  // Run id of a run paused awaiting tool approval (UX-6). Drives a persistent
  // banner with inline approve/reject instead of relying on a transient toast.
  const [waitingRunId, setWaitingRunId] = useState<string | null>(null);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const triggerPull = useTriggerManualPullMutation();
  const chatOpen = useEditor((s) => s.chatOpen);
  const openChat = useEditor((s) => s.openChat);
  const closeChat = useEditor((s) => s.closeChat);
  const { notify } = useToast();
  const confirm = useConfirm();
  const wsRef = useRef<RunStreamHandle | null>(null);
  const webhookTimerRef = useRef<number | null>(null);
  const listenPathRef = useRef<string | null>(null);
const aiAbortRef = useRef<AbortController | null>(null);
  const saveInProgressRef = useRef(false);
  const loadedWorkflowIdRef = useRef<string | null>(null);
  const creatingChildIdsRef = useRef<Set<string>>(new Set());

  const setManifests = useEditor((s) => s.setManifests);
  const setEnvContext = useEditor((s) => s.setEnvContext);
  const setApplyEnvSwitch = useEditor((s) => s.setApplyEnvSwitch);
  const loadGraph = useEditor((s) => s.loadGraph);
  const toGraph = useEditor((s) => s.toGraph);
  const markClean = useEditor((s) => s.markClean);
  const dirty = useEditor((s) => s.dirty);
  const childDirty = useEditor((s) =>
    Object.values(s.childWorkflows).some((cw) => cw.dirty),
  );
  const nodeCount = useEditor((s) => s.nodes.length);
  const hasTrigger = useEditor(selectHasTrigger);
  const hasChatTrigger = useEditor(selectHasChatTrigger);
  const selectedId = useEditor((s) => s.selectedId);
  const deleteSelection = useEditor((s) => s.deleteSelection);
  const duplicateNode = useEditor((s) => s.duplicateNode);
  const canRun = useCan("workflow:run");
  const canWrite = useCan("workflow:write");

  const barStatus = deriveBarStatus({
    publishedVersion: workflow?.published_version ?? null,
    active,
    hasUnpublishedChanges: Boolean(workflow?.has_unpublished_changes),
    dirty: dirty || childDirty,
  });

  const saveState: SaveState = saveError
    ? "error"
    : saving
      ? "saving"
      : dirty || childDirty
        ? "unsaved"
        : "saved";

  useAutosave({
    enabled: Boolean(canWrite && id),
    dirty: dirty || childDirty,
    delayMs: 1500,
    onSave: () => { void save({ notifySuccess: false }); },
  });

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
  const runHasError = useEditor((s) => Object.values(s.runStatus).some((st) => st === "error"));
  const ndvOpenId = useEditor((s) => s.ndvOpenId);
  const closeNdv = useEditor((s) => s.closeNdv);
  const setRunHandler = useEditor((s) => s.setRunHandler);
  const setWorkflowId = useEditor((s) => s.setWorkflowId);
  const setPinned = useEditor((s) => s.setPinned);
  const loadChildGraph = useEditor((s) => s.loadChildGraph);
  const setChildWorkflowLoading = useEditor((s) => s.setChildWorkflowLoading);
  const markChildClean = useEditor((s) => s.markChildClean);
  const updateParams = useEditor((s) => s.updateParams);
  const manifestsById = useEditor((s) => s.manifestsById);
  const editorNodes = useEditor((s) => s.nodes);
  const chatTriggerParams = useEditor(selectChatTriggerParams);
  // Map Group nodes that need a child workflow created.
  const mapGroupsNeedingChild = useMemo(
    () =>
      editorNodes.filter(
      (n) => n.type === "mapGroup" && !(n.data.params.child_workflow_id as string),
    ),
    [editorNodes],
  );

  useEffect(() => {
    if (!id) return;
    loadedWorkflowIdRef.current = null;
    setStatus("loading");
    setMessage("");
    clearRun();
    closeNdv();
    setWorkflowId(id);
  }, [clearRun, closeNdv, id, setWorkflowId]);

  useEffect(() => {
    if (!id || loadedWorkflowIdRef.current === id) return;
    if (
      workflowQuery.isLoading ||
      nodesQuery.isLoading ||
      customNodesQuery.isLoading ||
      environmentsQuery.isLoading ||
      pinnedQuery.isLoading
    ) {
      return;
    }
    const loadError =
      workflowQuery.error ??
      nodesQuery.error ??
      customNodesQuery.error ??
      environmentsQuery.error ??
      pinnedQuery.error;
    if (loadError) {
      setMessage(errorMessage(loadError));
      setStatus("error");
      return;
    }
    const detail = workflowQuery.data;
    if (!detail || !nodesQuery.data || !customNodesQuery.data || !pinnedQuery.data) {
      return;
    }

    let cancelled = false;
    setManifests([...nodesQuery.data, ...customNodesQuery.data]);
    loadGraph(detail.graph);
    setWorkflow(detail);
    setName(detail.name);
    setActive(detail.active);
    setEnvironmentId(detail.environment_id);
    setDefaultRunnerPoolId(detail.default_runner_pool_id ?? null);
    setRunTimeout(
      detail.run_timeout_seconds != null ? String(detail.run_timeout_seconds) : "",
    );
    setMcpEnabled(detail.mcp_enabled ?? false);
    setMcpToolName(detail.mcp_tool_name ?? "");
    setMcpDescription(detail.mcp_description ?? "");
    const pinnedMap: Record<string, PinnedOutput> = {};
    for (const p of pinnedQuery.data) {
      pinnedMap[p.node_id] = { payload: p.payload, updatedAt: p.updated_at };
    }
    setPinned(pinnedMap);
    loadedWorkflowIdRef.current = id;
    setStatus("ready");
    window.setTimeout(() => window.dispatchEvent(new Event("noodle:fit-view")), 60);

    // Load child workflows for any map_group nodes.
    const mapGroupNodes = useEditor.getState().nodes.filter(
      (n) => n.type === "mapGroup" && Boolean(n.data.params.child_workflow_id as string),
    );
    if (mapGroupNodes.length > 0) {
      void Promise.all(
        mapGroupNodes.map(async (mg) => {
          if (cancelled) return;
          const childId = mg.data.params.child_workflow_id as string;
          setChildWorkflowLoading(mg.id, true);
          try {
            const child = await api.getWorkflow(childId);
            if (!cancelled) loadChildGraph(mg.id, childId, child.graph);
          } catch (err) {
            if (!cancelled) setChildWorkflowLoading(mg.id, false, String(err));
          }
        }),
      );
    }

    return () => {
      cancelled = true;
    };
  }, [
    customNodesQuery.data,
    customNodesQuery.error,
    customNodesQuery.isLoading,
    environmentsQuery.error,
    environmentsQuery.isLoading,
    id,
    loadChildGraph,
    loadGraph,
    nodesQuery.data,
    nodesQuery.error,
    nodesQuery.isLoading,
    pinnedQuery.data,
    pinnedQuery.error,
    pinnedQuery.isLoading,
    setChildWorkflowLoading,
    setManifests,
    setPinned,
    workflowQuery.data,
    workflowQuery.error,
    workflowQuery.isLoading,
  ]);

  // Mirror the current run-environment context into the editor store so the
  // NDV can flag nodes whose packages the env lacks (and offer fix actions).
  useEffect(() => {
    const list = environments.map((e) => ({
      id: e.id,
      name: e.name,
      packages: e.packages,
      backend: e.backend,
    }));
    const current = environments.find((e) => e.id === environmentId) ?? null;
    setEnvContext({
      envId: current?.id ?? null,
      envName: current?.name ?? null,
      envPackages: current?.packages ?? [],
      environmentsList: list,
    });
  }, [environmentId, environments, setEnvContext]);

  useEffect(() => {
    setApplyEnvSwitch((envId: string) => setEnvironmentId(envId));
    return () => setApplyEnvSwitch(null);
  }, [setApplyEnvSwitch]);

  // Auto-create child workflows for newly dropped Map Group nodes.
  useEffect(() => {
    if (!id || mapGroupsNeedingChild.length === 0) return;
    const manualTriggerManifest = manifestsById["manual_trigger"];
    for (const mg of mapGroupsNeedingChild) {
      if (creatingChildIdsRef.current.has(mg.id)) continue;
      creatingChildIdsRef.current.add(mg.id);
      setChildWorkflowLoading(mg.id, true, null);
      void (async () => {
        try {
          const childWf = await api.createWorkflow(`${name} — map body`);
          const initialGraph: WorkflowGraph = {
            nodes: manualTriggerManifest
              ? [
                  {
                    id: `${mg.id}_t`,
                    type: "manual_trigger",
                    params: {},
                    position: { x: 100, y: 80 },
                    disabled: false,
                    outputs_override: null,
                    on_error: "stop",
                    retry_on_fail: false,
                    retries: 1,
                    retry_wait_seconds: 0,
                    retry_backoff: false,
                    always_output_data: false,
                    timeout_seconds: null,
                  },
                ]
              : [],
            edges: [],
          };
          await api.updateWorkflow(childWf.id, { graph: initialGraph });
          if (loadedWorkflowIdRef.current !== id) return;
          updateParams(mg.id, { ...mg.data.params, child_workflow_id: childWf.id });
          loadChildGraph(mg.id, childWf.id, initialGraph);
        } catch (err) {
          if (loadedWorkflowIdRef.current === id) {
            setChildWorkflowLoading(mg.id, false, String(err));
            notify(`Could not create map body workflow: ${String(err)}`, "error");
          }
        } finally {
          creatingChildIdsRef.current.delete(mg.id);
        }
      })();
    }
  }, [
    id,
    loadChildGraph,
    manifestsById,
    mapGroupsNeedingChild,
    name,
    notify,
    setChildWorkflowLoading,
    updateParams,
  ]);

  // Task 20: Debug in editor. When ExecutionsPage links to
  // /workflows/<id>?debug_run=<run_id>, load that run's snapshot once the
  // workflow is ready: rehydrate the graph as it ran, pin every successful
  // upstream output, and open the failed node so the user can iterate.
  const openNdv = useEditor((s) => s.openNdv);
  useEffect(() => {
    if (!id || status !== "ready") return;
    const search = new URLSearchParams(window.location.search);
    const debugRunId = search.get("debug_run");
    if (!debugRunId) return;
    let cancelled = false;
    (async () => {
      try {
        const snap = await api.runDebugSnapshot(debugRunId);
        if (cancelled) return;
        loadGraph(snap.graph);
        setPinned(
          Object.fromEntries(
            Object.entries(snap.upstream_cache).map(([nodeId, payload]) => [
              nodeId,
              { payload, updatedAt: null },
            ]),
          ),
        );
        if (snap.failed_node_id) openNdv(snap.failed_node_id);
        setMessage(
          snap.failed_node_id
            ? `Loaded debug snapshot from run ${debugRunId}. ` +
                `Failed node "${snap.failed_node_id}" is open; upstream outputs are pinned.`
            : `Loaded debug snapshot from run ${debugRunId}.`,
        );
      } catch (err) {
        if (cancelled) return;
        setMessage(`Failed to load debug snapshot: ${String(err)}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, status, loadGraph, setPinned, openNdv]);

  async function saveRunSetting(patch: { environment_id?: string | null; default_runner_pool_id?: string | null }) {
    if (!id) return;
    setChipSaving(true);
    try {
      await api.updateWorkflow(id, patch);
    } catch {
      // swallow — next explicit save will sync
    } finally {
      setChipSaving(false);
    }
  }

  function stopWebhookListen(): void {
    if (webhookTimerRef.current !== null) {
      window.clearInterval(webhookTimerRef.current);
      webhookTimerRef.current = null;
    }
    if (listenPathRef.current) {
      void api.stopListen(listenPathRef.current).catch(() => undefined);
      listenPathRef.current = null;
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

  // Warn before a tab close / refresh drops unsaved graph edits — saving is
  // manual (Cmd/Ctrl+S, Save, name blur), so without this guard a refresh
  // silently loses work. Covers the parent graph and any dirty child (map-body)
  // workflows.
  useEffect(() => {
    if (!dirty && !childDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty, childDirty]);

  // In-app navigation guard (UX-8): block route changes away from a dirty
  // editor (Logo link, browser back, programmatic nav) and confirm via dialog.
  // `beforeunload` above only covers real tab-close/refresh, not SPA nav.
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      (dirty || childDirty) &&
      currentLocation.pathname !== nextLocation.pathname,
  );

  async function triggerExport(path: string, filename: string): Promise<void> {
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    // Send the active org so the export resolves to the right tenant. This GET
    // bypasses the shared request() wrapper (it streams a blob), so the org
    // header must be added explicitly or the export 404s for non-default orgs (R-10).
    const orgId = getOrgId();
    if (orgId) headers["X-Org-Id"] = orgId;
    try {
      const resp = await fetch(path, { headers, credentials: "include" });
      if (!resp.ok) {
        const body = await resp.text().catch(() => resp.statusText);
        notify(`Export failed: ${body}`, "error");
        return;
      }
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    } catch (err) {
      notify(`Export failed: ${errorMessage(err)}`, "error");
    }
  }

  async function save(
    options: { notifySuccess?: boolean } = {},
  ): Promise<WorkflowDetail | null> {
    if (!id) return null;
    if (saveInProgressRef.current) return null;
    saveInProgressRef.current = true;
    const notifySuccess = options.notifySuccess ?? true;
    setSaving(true);
    setMessage("");
    try {
      setSaveError(false);
      const updated = await api.updateWorkflow(id, {
        name: name.trim() || "Untitled workflow",
        active,
        environment_id: environmentId ?? undefined,
        default_runner_pool_id: defaultRunnerPoolId,
        run_timeout_seconds: runTimeout === "" ? null : Math.max(0, parseFloat(runTimeout) || 0),
        mcp_enabled: mcpEnabled,
        mcp_tool_name: mcpToolName || null,
        mcp_description: mcpDescription || null,
        graph: toGraph(),
      });
      setWorkflow(updated);
      markClean();

      // Save dirty child workflows concurrently.
      const { childWorkflows } = useEditor.getState();
      const dirtyChildren = Object.entries(childWorkflows).filter(([, cw]) => cw.dirty);
      if (dirtyChildren.length > 0) {
        await Promise.all(
          dirtyChildren.map(async ([mgId, cw]) => {
            try {
              await api.updateWorkflow(cw.workflowId, { graph: childToGraph(cw) });
              markChildClean(mgId);
            } catch (err) {
              notify(`Could not save map body workflow: ${String(err)}`, "error");
            }
          }),
        );
      }

      if (notifySuccess) notify("Draft saved.", "success");
      return updated;
    } catch (err) {
      setSaveError(true);
      notify(`Could not save draft. ${errorMessage(err)}`, "error");
      return null;
    } finally {
      setSaving(false);
      saveInProgressRef.current = false;
    }
  }

  async function publishDraft(updateDeployments = false): Promise<void> {
    if (!id || publishing) return;
    const saved = await save({ notifySuccess: false });
    if (!saved) return;
    setPublishing(true);
    setMessage("");
    try {
      const published = await api.publishWorkflow(id, {
        notes: publishNotes.trim() || undefined,
        update_deployments: updateDeployments,
      });
      const detail = await api.getWorkflow(id);
      setWorkflow(detail);
      // Publishing takes the workflow live (backend sets active=true), so the
      // pill flips to the green "Published" state and the Active toggle reads on.
      setActive(detail.active);
      setPublishReviewOpen(false);
      setPublishNotes("");
      const deployNote = published.updated_deployments
        ? ` Updated ${published.updated_deployments} deployment(s).`
        : " Deployments stay pinned until updated.";
      setMessage(`Published v${published.version}.${deployNote}`);
      notify(`Published v${published.version}.`, "success");
    } catch (err) {
      notify(`Could not publish workflow. ${errorMessage(err)}`, "error");
    } finally {
      setPublishing(false);
    }
  }

  async function toggleActive(next: boolean): Promise<void> {
    // Flip whether the published version runs live in production. Persist
    // eagerly (optimistic) so there's no separate save step; roll back the
    // switch if the request fails. Pausing keeps version history intact.
    if (!id || togglingActive) return;
    if (
      !next &&
      !(await confirm({
        title: "Pause this workflow?",
        body: "It stops running in production until you switch it back on. Version history is kept.",
        confirmLabel: "Pause",
      }))
    ) return;
    setActive(next);
    setTogglingActive(true);
    try {
      const updated = await api.updateWorkflow(id, { active: next });
      setWorkflow(updated);
      notify(next ? "Workflow is live." : "Workflow paused.", "success");
    } catch (err) {
      setActive(!next);
      notify(`Could not update workflow state. ${errorMessage(err)}`, "error");
    } finally {
      setTogglingActive(false);
    }
  }

  function openPublishReview(): void {
    setPublishSummary(
      buildPublishSummary(workflow?.graph, toGraph(), workflow?.environment_id, environmentId),
    );
    setPublishUpdateDeployments(false);
    setPublishReviewOpen(true);
  }

  async function previewAiDraft(): Promise<void> {
    if (!id || aiBusy || !aiPrompt.trim()) return;
    aiAbortRef.current?.abort();
    const controller = new AbortController();
    aiAbortRef.current = controller;
    const timeoutId = window.setTimeout(() => controller.abort(), 60_000);
    setAiBusy(true);
    setMessage("");
    try {
      const draft = await api.aiWorkflowDraft(id, {
        prompt: aiPrompt.trim(),
        apply: false,
        mode: aiMode,
        current_graph: aiMode === "fix" ? toGraph() : undefined,
        failed_run_id: aiMode === "fix" ? runId : undefined,
        failed_node_id: aiMode === "fix" ? aiFailedNodeId : undefined,
        error: aiMode === "fix" ? aiFailedError : undefined,
        fix_strategy: aiFixStrategy,
      }, controller.signal);
      setAiPreview(draft);
      notify("AI draft preview ready.", "success");
    } catch (err) {
      if (controller.signal.aborted) {
        notify("AI draft request timed out. Please try again.", "error");
      } else {
        notify(`Could not build AI draft. ${errorMessage(err)}`, "error");
      }
    } finally {
      window.clearTimeout(timeoutId);
      setAiBusy(false);
      aiAbortRef.current = null;
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
      notify(`Could not apply AI draft. ${errorMessage(err)}`, "error");
    } finally {
      setAiBusy(false);
    }
  }

  function openAiFixFailedRun(): void {
    const failedNodeId = Object.entries(runStatusMap).find(
      ([, status]) => status === "error",
    )?.[0];
    const failedError = failedNodeId ? runMetaMap[failedNodeId]?.error : runError;
    setAiMode("fix");
    setAiFixStrategy("minimal");
    setAiFailedNodeId(failedNodeId ?? null);
    setAiFailedError(failedError ?? null);
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
          ? pinnedPayload(pinned)
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

  function connectRunStream(runId: string, targets?: string[], cache?: RunCache): void {
    // Tear down any prior run's stream before opening a new one — otherwise a
    // rapid re-run (or starting a second run) leaks the old socket and lets its
    // events keep mutating editor state for the wrong run.
    wsRef.current?.close();
    wsRef.current = null;
    setWaitingRunId(null);
    startRun(runId, targets, cache);
    wsRef.current = subscribeToRunEvents(runId, {
      onMessage: (data) => {
        const payload = data as RunEvent;
        applyRunEvent(payload);
        if (payload.type === "run_finished") {
          // A "waiting" finish means the run paused for a tool approval. Surface
          // it as a persistent banner (UX-6) — a transient toast is too easy to
          // miss for a run that's genuinely blocked.
          setWaitingRunId(payload.status === "waiting" ? (payload.run_id ?? runId) : null);
          notify(
            payload.status === "success"
              ? "Workflow run succeeded."
              : payload.status === "waiting"
                ? "Workflow run waiting for approval."
                : `Workflow run ${payload.status ?? "finished"}.`,
            payload.status === "success"
              ? "success"
              : payload.status === "waiting"
                ? "info"
                : "error",
          );
        } else if (payload.type === "run_error") {
          notify(
            payload.error ? `Run failed: ${payload.error}` : "Run failed.",
            "error",
          );
        }
      },
      onReconnecting: (attempt) => {
        // Don't spam toasts on the first attempt — most drops are 1s blips.
        if (attempt >= 2) {
          notify(`Reconnecting to run stream (attempt ${attempt})…`, "info");
        }
      },
      onClosed: () => {
        wsRef.current = null;
      },
    });
  }

  function startChatCanvasRun(runId: string): void {
    // The chat panel owns the live run socket for chat turns. Reuse its events
    // to animate the canvas instead of opening a second editor stream.
    wsRef.current?.close();
    wsRef.current = null;
    setWaitingRunId(null);
    startRun(runId);
  }

  function applyChatRunEvent(event: RunEvent): void {
    applyRunEvent(event);
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
      await api.startListen(path);
    } catch (err) {
      setMessage(String(err));
      return;
    }

    listenPathRef.current = path;
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
          // Gate to this webhook trigger's branch — otherwise sibling
          // triggers in the same graph would also fire on the test run.
          ...(runTargets && runTargets.length > 0
            ? {}
            : { trigger_node_id: node.id }),
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

    // Default the run to the workflow's trigger when no explicit
    // targets / triggerNodeId was supplied. A workflow with no trigger
    // can't be run — bail with a toast instead of letting the API 400.
    let triggerNodeId = options.triggerNodeId;
    if (!triggerNodeId && (!targets || targets.length === 0)) {
      const trigger = pickEditorRunTrigger(useEditor.getState().nodes);
      if (!trigger) {
        notify("Add a trigger node to run this workflow.", "error");
        return;
      }
      triggerNodeId = trigger.id;
    }

    const cache = options.reuseUpstream
      ? reusableUpstreamCache(saved.graph, targets)
      : undefined;
    // Test-listen mode only kicks in when a webhook_trigger is what we
    // actually intend to fire — i.e. the chosen entry trigger is a webhook,
    // or the user explicitly targets a webhook downstream.
    const webhookNode =
      triggerNodeId && (!targets || targets.length === 0)
        ? saved.graph.nodes.find(
            (n) => n.id === triggerNodeId && n.type === "webhook_trigger",
          ) ?? null
        : webhookForRun(saved.graph, targets, cache);
    if (webhookNode) {
      await startWebhookTestRun(webhookNode, targets, cache);
      return;
    }
    try {
      const body: {
        targets?: string[];
        cache?: Record<string, Record<string, unknown>>;
        trigger_node_id?: string;
      } = {};
      if (targets && targets.length > 0) body.targets = targets;
      if (cache) body.cache = cache;
      if (triggerNodeId && (!targets || targets.length === 0)) {
        body.trigger_node_id = triggerNodeId;
      }
      const { run_id } = await api.runWorkflow(id, body);
      if (cache && Object.keys(cache).length > 0) {
        notify(`Reused ${Object.keys(cache).length} upstream output(s).`, "info");
      }
      connectRunStream(run_id, targets, cache);
    } catch (err) {
      notify(`Could not start workflow run. ${errorMessage(err)}`, "error");
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
      notify(`Could not cancel run. ${errorMessage(err)}`, "error");
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
      if (e.defaultPrevented) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen(true);
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        void run();
        return;
      }
      if (isEditableTarget(e.target)) return;
      if (e.key === "?") {
        e.preventDefault();
        setShortcutsOpen(true);
        return;
      }
      if (e.key === "/") {
        e.preventDefault();
        window.dispatchEvent(new Event("noodle:focus-node-search"));
        return;
      }
      if (e.shiftKey && e.key.toLowerCase() === "f") {
        e.preventDefault();
        window.dispatchEvent(new Event("noodle:fit-view"));
        return;
      }
      if (e.shiftKey && e.key.toLowerCase() === "l") {
        e.preventDefault();
        window.dispatchEvent(new Event("noodle:auto-layout"));
        return;
      }
      if (e.shiftKey && e.key.toLowerCase() === "p") {
        e.preventDefault();
        window.dispatchEvent(new Event("noodle:toggle-node-palette"));
        return;
      }
      if (e.shiftKey && e.key.toLowerCase() === "i") {
        e.preventDefault();
        window.dispatchEvent(new Event("noodle:toggle-inspector"));
        return;
      }
      if (e.shiftKey && e.key.toLowerCase() === "n") {
        e.preventDefault();
        useEditor.getState().addStickyNote({ x: 200 + Math.random() * 200, y: 200 + Math.random() * 100 });
        return;
      }
      if (e.shiftKey && e.key.toLowerCase() === "g") {
        e.preventDefault();
        useEditor.getState().addGroupNode({ x: 200 + Math.random() * 200, y: 200 + Math.random() * 100 });
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        const deleted = deleteSelection();
        if (deleted === 0) return;
        e.preventDefault();
        return;
      }
      if (e.key.toLowerCase() === "d" && selectedId) {
        e.preventDefault();
        duplicateNode(selectedId);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [deleteSelection, duplicateNode, environmentId, run, save, selectedId]);

  useEffect(() => {
    function onOpenShortcuts() { setShortcutsOpen(true); }
    window.addEventListener("noodle:open-shortcuts", onOpenShortcuts);
    return () => window.removeEventListener("noodle:open-shortcuts", onOpenShortcuts);
  }, []);

  async function viewRun(runId: string): Promise<void> {
    try {
      const run = await api.getRun(runId);
      applyRunInfo(run);
      setSidecarOpen(true);
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
            aria-label="Workflow name"
            onChange={(e) => setName(e.target.value)}
            onBlur={() => { if (dirty || name.trim() !== (workflow?.name ?? "")) void save({ notifySuccess: false }); }}
            spellCheck={false}
          />
          <span className="toolbar-meta">
            {barStatus.versionLabel}
            {" · "}
            {nodeCount} node{nodeCount === 1 ? "" : "s"}
          </span>
        </div>
        <div className="toolbar-right">
          <SaveIndicator state={saveState} onRetry={() => { void save({ notifySuccess: false }); }} />
          {workflow?.github_sync_status && (
            <GitHubSyncBadge status={workflow.github_sync_status} />
          )}

          <RunSettingsChip
            environments={environments}
            environmentId={environmentId}
            onEnvChange={(envId) => {
              setEnvironmentId(envId);
              void saveRunSetting({ environment_id: envId });
            }}
            pools={runnerPoolsQuery.data ?? []}
            defaultRunnerPoolId={defaultRunnerPoolId}
            onRunnerChange={(poolId) => {
              setDefaultRunnerPoolId(poolId);
              void saveRunSetting({ default_runner_pool_id: poolId });
            }}
            saving={chipSaving}
          />

          {hasChatTrigger ? (
            <button type="button" className="btn" onClick={openChat} title="Open chat panel">
              Chat
            </button>
          ) : null}

          <button
            className="btn"
            onClick={() => {
              setAiMode("draft");
              setAiFixStrategy("minimal");
              setAiFailedNodeId(null);
              setAiFailedError(null);
              setAiPreview(null);
              setAiOpen(true);
            }}
            title="Generate an editable draft from a natural-language prompt"
          >
            ✨ AI Draft
          </button>

          <button
            className={`btn${sidecarOpen ? " active" : ""}`}
            onClick={() => setSidecarOpen((v) => !v)}
            title="Run history"
          >
            Runs
          </button>

          {canWrite && (
            <PublishPill
              status={barStatus}
              onClick={openPublishReview}
              disabled={saving || publishing}
            />
          )}

          {canWrite && barStatus.kind !== "unpublished" && (
            <label
              className="active-toggle"
              title={active
                ? "Live — triggers run in production. Switch off to pause."
                : "Paused — switch on to run the published version live."}
            >
              <input
                type="checkbox"
                checked={active}
                disabled={togglingActive}
                aria-label={active ? "Pause workflow" : "Activate workflow"}
                onChange={(e) => void toggleActive(e.target.checked)}
              />
              <span className="active-track" />
              <span>{active ? "Live" : "Paused"}</span>
            </label>
          )}

          <button
            type="button"
            className="btn btn-icon"
            title="Keyboard shortcuts"
            aria-label="Keyboard shortcuts"
            onClick={() => setShortcutsOpen(true)}
          >
            <Keyboard size={14} weight="bold" />
          </button>

          <OverflowMenu
            items={[
              { id: "history", label: "History & versions", onSelect: () => setShowHistory(true) },
              { id: "functions", label: "Functions", onSelect: () => setFunctionsOpen(true) },
              { id: "export-py", label: "Export · Python script (.py)", onSelect: () => void triggerExport(`/api/workflows/${id}/export.py`, `${name || "workflow"}.py`) },
              { id: "export-docker", label: "Export · Docker bundle (.zip)", onSelect: () => void triggerExport(`/api/workflows/${id}/export/docker`, `${name || "workflow"}-docker.zip`) },
              { id: "export-module", label: "Export · Python module (.py)", onSelect: () => void triggerExport(`/api/workflows/${id}/export.module.py`, `${name || "workflow"}_module.py`) },
              { id: "settings", label: "Workflow settings", dividerBefore: true, onSelect: () => setSettingsOpen(true) },
              { id: "shortcuts", label: "Keyboard shortcuts", onSelect: () => setShortcutsOpen(true) },
              { id: "github-pull", label: "Pull from GitHub", dividerBefore: true, onSelect: () => { if (id) void triggerPull.mutateAsync(id); } },
              workflow?.github_sync_status === "conflict" && {
                id: "github-conflict",
                label: "Resolve GitHub conflict",
                onSelect: () => setConflictOpen(true),
              },
            ] as (OverflowItem | null | false)[]}
          />
        </div>
      </header>

      {conflictOpen && workflow && (
        <GitHubConflictModal
          workflowId={workflow.id}
          workflowName={workflow.name}
          onClose={() => setConflictOpen(false)}
        />
      )}

      {message && <div className="toolbar-message">{message}</div>}
      {active && (dirty || workflow?.has_unpublished_changes) && (
        <div className="production-warning">
          Draft changes won't affect active production runs until you publish.
        </div>
      )}
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

      {waitingRunId && (
        <div className="toolbar-approval run-approval-banner">
          <div className="run-approval-head">
            <span>⏸ This run is paused for tool approval.</span>
            <div className="run-approval-actions">
              <Link
                className="btn btn-sm btn-ghost"
                to={`/executions?run=${waitingRunId}`}
              >
                View run →
              </Link>
              <button
                className="btn btn-sm btn-ghost"
                aria-label="Dismiss approval banner"
                onClick={() => setWaitingRunId(null)}
              >
                ✕
              </button>
            </div>
          </div>
          <RunApprovalsPanel
            runId={waitingRunId}
            runStatus="waiting"
            onChanged={() => {
              // A decision was made — resume streaming so the editor reflects
              // the run continuing (or finishing). If it pauses again, the next
              // run_finished:"waiting" re-arms this banner.
              connectRunStream(waitingRunId);
            }}
          />
        </div>
      )}

      <ReactFlowProvider>
        <div className="editor-body">
          <NodePalette />
          <div className="editor-stage">
            <Canvas />
            {sidecarOpen && runId && !running && (
              <div className="run-exec-banner">
                <div className="run-exec-banner-dot" />
                <span className="run-exec-banner-label">Viewing</span>
                <span className="run-exec-banner-id">#{runId.slice(0, 6)}</span>
                <span className="run-exec-banner-meta">
                  {runHasError ? " · error" : " · success"}
                </span>
                <div className="run-exec-banner-end">
                  <button className="run-exec-banner-exit" onClick={clearRun}>
                    Exit run view
                  </button>
                </div>
              </div>
            )}
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
              ) : canRun ? (
                <button
                  type="button"
                  className="canvas-run-btn"
                  onClick={() => void run()}
                  disabled={Boolean(webhookListen) || saving || !hasTrigger}
                  title={
                    !hasTrigger
                      ? "Add a trigger node to run this workflow"
                      : "Execute the whole workflow"
                  }
                >
                  ▶ Execute Workflow
                </button>
              ) : null}
            </div>
            <PortDataViewer />
          </div>
          {sidecarOpen && id && <RunSidecar workflowId={id} />}
          <Inspector />
        </div>
      </ReactFlowProvider>

      {ndvOpenId && (
        <Suspense fallback={null}>
          <NodeDetailModal nodeId={ndvOpenId} />
        </Suspense>
      )}

      {aiOpen && (
        <AiDraftModal
          mode={aiMode}
          prompt={aiPrompt}
          preview={aiPreview}
          busy={aiBusy}
          fixStrategy={aiFixStrategy}
          onPromptChange={(value) => {
            setAiPrompt(value);
            setAiPreview(null);
          }}
          onFixStrategyChange={(value) => {
            setAiFixStrategy(value);
            setAiPreview(null);
          }}
          onPreview={() => void previewAiDraft()}
          onApply={() => void applyAiDraft()}
          onClose={() => {
            setAiOpen(false);
            setAiPreview(null);
          }}
        />
      )}

      {shortcutsOpen && (
        <A11yModal
          className="shortcuts-modal"
          titleId="shortcuts-title"
          title="Keyboard shortcuts"
          onClose={() => setShortcutsOpen(false)}
        >
          <div className="shortcut-grid">
              <span>Save draft</span>
              <kbd>Ctrl</kbd>
              <kbd>S</kbd>
              <span>Run workflow</span>
              <kbd>Ctrl</kbd>
              <kbd>Enter</kbd>
              <span>Copy selected nodes</span>
              <kbd>Ctrl</kbd>
              <kbd>C</kbd>
              <span>Cut selected nodes</span>
              <kbd>Ctrl</kbd>
              <kbd>X</kbd>
              <span>Paste copied nodes</span>
              <kbd>Ctrl</kbd>
              <kbd>V</kbd>
              <span>Search nodes</span>
              <kbd>/</kbd>
              <span />
              <span>Fit view</span>
              <kbd>Shift</kbd>
              <kbd>F</kbd>
              <span>Auto-layout</span>
              <kbd>Shift</kbd>
              <kbd>L</kbd>
              <span>Toggle node picker</span>
              <kbd>Shift</kbd>
              <kbd>P</kbd>
              <span>Toggle inspector</span>
              <kbd>Shift</kbd>
              <kbd>I</kbd>
              <span>Add group frame</span>
              <kbd>Shift</kbd>
              <kbd>G</kbd>
              <span>Add sticky note</span>
              <kbd>Shift</kbd>
              <kbd>N</kbd>
              <span>Delete selected nodes</span>
              <kbd>Delete</kbd>
              <span />
              <span>Duplicate selected node</span>
              <kbd>D</kbd>
              <span />
              <span>Open shortcuts</span>
              <kbd>?</kbd>
              <span />
          </div>
        </A11yModal>
      )}

      {publishReviewOpen && publishSummary && (
        <A11yModal
          className="publish-review-modal"
          titleId="publish-review-title"
          title="Review release"
          onClose={() => setPublishReviewOpen(false)}
          closeDisabled={publishing}
        >
            <div className="publish-review-grid">
              <div>
                <strong>{publishSummary.addedNodes}</strong>
                <span>nodes added</span>
              </div>
              <div>
                <strong>{publishSummary.removedNodes}</strong>
                <span>nodes removed</span>
              </div>
              <div>
                <strong>
                  {publishSummary.edgeDelta > 0 ? "+" : ""}
                  {publishSummary.edgeDelta}
                </strong>
                <span>connection delta</span>
              </div>
              <div>
                <strong>{publishSummary.credentialRefs}</strong>
                <span>credential refs</span>
              </div>
            </div>
            <div className="publish-review-notes">
              <p>
                <strong>Triggers:</strong> {publishSummary.triggerSummary}
                {publishSummary.triggerChanged ? " (changed)" : ""}
              </p>
              {publishSummary.environmentChanged && (
                <p>
                  <strong>Environment:</strong> this release changes the run
                  environment.
                </p>
              )}
              {workflow?.active && (
                <p>
                  <strong>Production:</strong> publishing creates a new version.
                  Existing deployments remain pinned unless you update them.
                </p>
              )}
            </div>
            <label className="field-toggle publish-deploy-toggle">
              <input
                type="checkbox"
                checked={publishUpdateDeployments}
                onChange={(e) => setPublishUpdateDeployments(e.target.checked)}
              />
              <span className="field-toggle-track" />
              <span className="field-toggle-text">
                Update deployments to this version
              </span>
            </label>
            <label className="field publish-notes-field">
              <span>Version notes (optional)</span>
              <textarea
                value={publishNotes}
                placeholder="What changed in this version?"
                onChange={(e) => setPublishNotes(e.target.value)}
                rows={3}
              />
            </label>
            <div className="modal-actions">
              <button
                className="btn btn-ghost"
                onClick={() => setPublishReviewOpen(false)}
                disabled={publishing}
              >
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={() => void publishDraft(publishUpdateDeployments)}
                disabled={saving || publishing}
              >
                {publishing ? "Publishing..." : "Publish release"}
              </button>
            </div>
        </A11yModal>
      )}

      {settingsOpen && (
        <WorkflowSettingsModal
          runTimeout={runTimeout}
          onRunTimeoutChange={setRunTimeout}
          mcpEnabled={mcpEnabled}
          onMcpEnabledChange={setMcpEnabled}
          mcpToolName={mcpToolName}
          onMcpToolNameChange={setMcpToolName}
          mcpDescription={mcpDescription}
          onMcpDescriptionChange={setMcpDescription}
          onClose={() => { setSettingsOpen(false); void save({ notifySuccess: false }); }}
        />
      )}

      {chatOpen && workflow ? (
        <ChatPanel
          workflowId={workflow.id}
          title={chatTriggerParams?.title ?? "Chat"}
          placeholder={chatTriggerParams?.input_placeholder ?? "Type a message…"}
          initialMessage={chatTriggerParams?.initial_message ?? ""}
          onRun={startChatCanvasRun}
          onRunEvent={applyChatRunEvent}
          onClose={closeChat}
          onViewRun={(runId) => viewRun(runId)}
          live
        />
      ) : null}

      {functionsOpen && id && (
        <FunctionsPanel
          workflowId={id}
          onClose={() => setFunctionsOpen(false)}
          onChanged={async () => {
            const [builtins, custom] = await Promise.all([
              nodesQuery.refetch(),
              customNodesQuery.refetch(),
            ]);
            setManifests([...(builtins.data ?? []), ...(custom.data ?? [])]);
          }}
          onApplyStarterGraph={(graph) => {
            // Apply locally and mark the workflow dirty; the user reviews on
            // the canvas and clicks Save to persist.
            loadGraph(graph, { dirty: true });
          }}
        />
      )}

      {showHistory && id && (
        <WorkflowHistory
          workflowId={id}
          onClose={() => setShowHistory(false)}
          onRestore={(version) => setRestoreVersion(version)}
          onCompare={(_version) => {
            /* wired in Task 7 */
          }}
        />
      )}
      {blocker.state === "blocked" && (
        <ConfirmDialog
          title="Leave with unsaved changes?"
          body="This workflow has unsaved edits. Leaving now will discard them."
          confirmLabel="Leave"
          onCancel={() => blocker.reset()}
          onConfirm={() => blocker.proceed()}
        />
      )}
      {restoreVersion && id && (
        <ConfirmDialog
          title="Restore this version?"
          body="This will replace your current draft. Any unsaved changes will be lost."
          confirmLabel="Restore"
          onCancel={() => setRestoreVersion(null)}
          onConfirm={() => {
            api
              .getVersionGraph(id, restoreVersion.id)
              .then(({ graph }) => loadGraph(graph, { dirty: true }));
            setRestoreVersion(null);
            setShowHistory(false);
          }}
        />
      )}

      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} />
    </div>
  );
}
