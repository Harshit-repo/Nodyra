import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, type RunStreamHandle, subscribeToRunEvents } from "./api";
import { AiDraftModal } from "./AiDraftModal";
import { Canvas } from "./editor/Canvas";
import { CommandPalette } from "./editor/CommandPalette";
import { FunctionsPanel } from "./editor/FunctionsPanel";
import { Inspector } from "./editor/Inspector";
import { NodeDetailModal } from "./editor/NodeDetailModal";
import { NodePalette } from "./editor/NodePalette";
import { PortDataViewer } from "./editor/PortDataViewer";
import { WorkflowHistory } from "./editor/WorkflowHistory";
import { pickEditorRunTrigger, type RunOptions, useEditor } from "./editor/store";
import { Logo } from "./Logo";
import { useCan } from "./permissions";
import { useToast } from "./ToastProvider";
import type {
  AiDraftMode,
  AiFixStrategy,
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

export function EditorPage() {
  const { id } = useParams<{ id: string }>();
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
  const [name, setName] = useState("");
  const [active, setActive] = useState(false);
  const [environmentId, setEnvironmentId] = useState<string | null>(null);
  const [runTimeout, setRunTimeout] = useState<string>("");
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
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [cmdOpen, setCmdOpen] = useState(false);
  const { notify } = useToast();
  const wsRef = useRef<RunStreamHandle | null>(null);
  const webhookTimerRef = useRef<number | null>(null);

  const setManifests = useEditor((s) => s.setManifests);
  const loadGraph = useEditor((s) => s.loadGraph);
  const toGraph = useEditor((s) => s.toGraph);
  const markClean = useEditor((s) => s.markClean);
  const dirty = useEditor((s) => s.dirty);
  const nodeCount = useEditor((s) => s.nodes.length);
  const hasTrigger = useEditor((s) => pickEditorRunTrigger(s.nodes) !== null);
  const selectedId = useEditor((s) => s.selectedId);
  const deleteNode = useEditor((s) => s.deleteNode);
  const duplicateNode = useEditor((s) => s.duplicateNode);
  const canRun = useCan("workflow:run");
  const canWrite = useCan("workflow:write");
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
        setRunTimeout(
          detail.run_timeout_seconds != null
            ? String(detail.run_timeout_seconds)
            : "",
        );
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
        setPinned(snap.upstream_cache);
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
        run_timeout_seconds: runTimeout === "" ? null : Math.max(0, parseFloat(runTimeout) || 0),
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

  async function publishDraft(updateDeployments = false): Promise<void> {
    if (!id || publishing) return;
    const saved = await save({ notifySuccess: false });
    if (!saved) return;
    setPublishing(true);
    setMessage("");
    try {
      const published = await api.publishWorkflow(id, {
        notes: "Published from editor",
        update_deployments: updateDeployments,
      });
      const detail = await api.getWorkflow(id);
      setWorkflow(detail);
      setPublishReviewOpen(false);
      const deployNote = published.updated_deployments
        ? ` Updated ${published.updated_deployments} deployment(s).`
        : " Deployments stay pinned until updated.";
      setMessage(`Published v${published.version}.${deployNote}`);
      notify(`Published v${published.version}.`, "success");
    } catch (err) {
      setMessage(String(err));
      notify("Could not publish workflow.", "error");
    } finally {
      setPublishing(false);
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
        run_timeout_seconds: runTimeout === "" ? null : Math.max(0, parseFloat(runTimeout) || 0),
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
    wsRef.current = subscribeToRunEvents(runId, {
      onMessage: (data) => {
        const payload = data as RunEvent;
        applyRunEvent(payload);
        if (payload.type === "run_finished") {
          notify(
            payload.status === "success"
              ? "Workflow run succeeded."
              : `Workflow run ${payload.status ?? "finished"}.`,
            payload.status === "success" ? "success" : "error",
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
      if ((e.key === "Delete" || e.key === "Backspace") && selectedId) {
        e.preventDefault();
        deleteNode(selectedId);
        return;
      }
      if (e.key.toLowerCase() === "d" && selectedId) {
        e.preventDefault();
        duplicateNode(selectedId);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [deleteNode, duplicateNode, environmentId, run, save, selectedId]);

  useEffect(() => {
    function onOpenShortcuts() { setShortcutsOpen(true); }
    window.addEventListener("noodle:open-shortcuts", onOpenShortcuts);
    return () => window.removeEventListener("noodle:open-shortcuts", onOpenShortcuts);
  }, []);

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
          <input
            className="toolbar-timeout"
            type="number"
            min={0}
            step={1}
            value={runTimeout}
            placeholder="No timeout"
            title="Run timeout (seconds). Blank or 0 means the run is never capped."
            onChange={(e) => setRunTimeout(e.target.value)}
          />
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
            className="btn btn-icon"
            onClick={() => setShortcutsOpen(true)}
            title="Keyboard shortcuts"
            aria-label="Keyboard shortcuts"
          >
            ?
          </button>
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
          {canRun && <button
            className="btn btn-run"
            onClick={() => void run()}
            disabled={running || Boolean(webhookListen) || !hasTrigger}
            title={
              !hasTrigger
                ? "Add a trigger node to run this workflow"
                : undefined
            }
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
          </button>}
          {running && (
            <button
              className="btn btn-danger"
              onClick={() => void cancelCurrentRun()}
              disabled={cancellingRun}
            >
              {cancellingRun ? "Stopping…" : "■ Stop"}
            </button>
          )}
          {canWrite && <button className="btn btn-primary" onClick={() => void save()} disabled={saving}>
            {dirty && <span className="dirty-dot" />}
            {saving ? "Saving…" : "Save draft"}
          </button>}
          {canWrite && <button
            className="btn"
            onClick={openPublishReview}
            disabled={saving || publishing}
            title="Publish the saved draft as a new production version"
          >
            {publishing ? "Publishing…" : "Publish"}
          </button>}
          <button
            className="btn"
            onClick={() => setShowHistory(true)}
            title="View version history"
          >
            History
          </button>
        </div>
      </header>

      {message && <div className="toolbar-error">{message}</div>}
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
          <Inspector />
        </div>
      </ReactFlowProvider>

      {ndvOpenId && <NodeDetailModal nodeId={ndvOpenId} />}

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
        <div className="modal-overlay" onClick={() => setShortcutsOpen(false)}>
          <div
            className="modal shortcuts-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="shortcuts-title"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="modal-head">
              <h2 id="shortcuts-title">Keyboard shortcuts</h2>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => setShortcutsOpen(false)}
              >
                ✕
              </button>
            </header>
            <div className="shortcut-grid">
              <span>Save draft</span>
              <kbd>Ctrl</kbd>
              <kbd>S</kbd>
              <span>Run workflow</span>
              <kbd>Ctrl</kbd>
              <kbd>Enter</kbd>
              <span>Search nodes</span>
              <kbd>/</kbd>
              <span />
              <span>Fit view</span>
              <kbd>Shift</kbd>
              <kbd>F</kbd>
              <span>Auto-layout</span>
              <kbd>Shift</kbd>
              <kbd>L</kbd>
              <span>Add group frame</span>
              <kbd>Shift</kbd>
              <kbd>G</kbd>
              <span>Delete selected node</span>
              <kbd>Delete</kbd>
              <span />
              <span>Duplicate selected node</span>
              <kbd>D</kbd>
              <span />
              <span>Open shortcuts</span>
              <kbd>?</kbd>
              <span />
            </div>
          </div>
        </div>
      )}

      {publishReviewOpen && publishSummary && (
        <div className="modal-overlay" onClick={() => setPublishReviewOpen(false)}>
          <div
            className="modal publish-review-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="publish-review-title"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="modal-head">
              <h2 id="publish-review-title">Review release</h2>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => setPublishReviewOpen(false)}
                disabled={publishing}
              >
                ✕
              </button>
            </header>
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

      {showHistory && id && (
        <WorkflowHistory
          workflowId={id}
          onClose={() => setShowHistory(false)}
          onRestore={(graph) => {
            loadGraph(graph, { dirty: true });
            setShowHistory(false);
          }}
        />
      )}

      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} />
    </div>
  );
}
