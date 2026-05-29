import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import { create } from "zustand";

import type {
  NodeManifest,
  NodeRunDebug,
  RunEvent,
  RunInfo,
  WorkflowGraph,
} from "../types";

export interface NoodleNodeData {
  manifest: NodeManifest;
  params: Record<string, unknown>;
  disabled: boolean;
  outputsOverride: string[] | null;
  onError: string;
  retryOnFail: boolean;
  retries: number;
  retryWaitSeconds: number;
  retryBackoff: boolean;
  alwaysOutputData: boolean;
  timeoutSeconds: number | null;
  [key: string]: unknown;
}

export interface NodeSettingsPatch {
  onError?: string;
  retryOnFail?: boolean;
  retries?: number;
  retryWaitSeconds?: number;
  retryBackoff?: boolean;
  alwaysOutputData?: boolean;
  timeoutSeconds?: number | null;
}

export interface NodeRunMeta {
  logs?: string[];
  error?: string | null;
  debug?: NodeRunDebug | null;
  durationMs?: number | null;
  startedAt?: number | null;
  finishedAt?: number | null;
}

export interface RunOptions {
  reuseUpstream?: boolean;
  triggerNodeId?: string;
}

export const TRIGGER_CATEGORY = "Triggers";

export function pickEditorRunTrigger(nodes: NoodleNode[]): NoodleNode | null {
  const triggers = nodes.filter((n) => n.data.manifest.category === TRIGGER_CATEGORY);
  if (triggers.length === 0) return null;
  const manual = triggers.find((n) => n.data.manifest.id === "manual_trigger");
  return manual ?? triggers[0];
}

export function isTriggerNode(node: NoodleNode | undefined): boolean {
  return Boolean(node && node.data.manifest.category === TRIGGER_CATEGORY);
}

function deriveSwitchOutputs(rules: unknown): string[] {
  if (rules && typeof rules === "object" && !Array.isArray(rules)) {
    const keys = Object.keys(rules as Record<string, unknown>);
    return [...keys, "fallback"];
  }
  return ["fallback"];
}

export type NoodleNode = Node<NoodleNodeData, "noodle">;

interface EditorStore {
  manifests: NodeManifest[];
  manifestsById: Record<string, NodeManifest>;
  nodes: NoodleNode[];
  edges: Edge[];
  selectedId: string | null;
  dirty: boolean;

  runId: string | null;
  running: boolean;
  runStatus: Record<string, string>;
  runOutputs: Record<string, unknown>;
  runMeta: Record<string, NodeRunMeta>;
  runError: string | null;

  setManifests: (manifests: NodeManifest[]) => void;
  loadGraph: (graph: WorkflowGraph, opts?: { dirty?: boolean }) => void;
  toGraph: () => WorkflowGraph;
  onNodesChange: (changes: NodeChange<NoodleNode>[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => void;
  addNode: (manifestId: string, position: { x: number; y: number }) => void;
  addStickyNote: (position: { x: number; y: number }) => void;
  autoLayout: () => void;
  duplicateNode: (id: string) => void;
  updateParams: (id: string, params: Record<string, unknown>) => void;
  setSelected: (id: string | null) => void;
  markClean: () => void;

  ndvOpenId: string | null;
  openNdv: (id: string) => void;
  closeNdv: () => void;

  deleteNode: (id: string) => void;
  toggleDisabled: (id: string) => void;
  updateNodeSettings: (id: string, patch: NodeSettingsPatch) => void;

  runHandler: ((targets?: string[], options?: RunOptions) => Promise<void>) | null;
  setRunHandler: (
    fn: ((targets?: string[], options?: RunOptions) => Promise<void>) | null,
  ) => void;
  runFromNode: (id: string, options?: RunOptions) => void;
  runFromTrigger: (id: string) => void;

  startRun: (runId: string, targets?: string[]) => void;
  applyRunEvent: (event: RunEvent) => void;
  applyRunInfo: (run: RunInfo) => void;
  clearRun: () => void;
  setNodeOutput: (
    nodeId: string,
    outputs: unknown | undefined,
    status?: string,
  ) => void;

  workflowId: string | null;
  setWorkflowId: (id: string | null) => void;

  pinned: Record<string, unknown>;
  setPinned: (pinned: Record<string, unknown>) => void;
  setPinnedFor: (nodeId: string, payload: unknown | null) => void;

  // History (undo/redo) — snapshots of {nodes, edges} only. Reset whenever
  // `loadGraph` is called for a different workflow so undo never crosses
  // workflow boundaries.
  _past: Array<{ nodes: NoodleNode[]; edges: Edge[] }>;
  _future: Array<{ nodes: NoodleNode[]; edges: Edge[] }>;
  undo: () => void;
  redo: () => void;
}

let seq = 0;
function newNodeId(): string {
  seq += 1;
  return `n_${Date.now().toString(36)}_${seq}`;
}

function randomSlug(): string {
  return Math.random().toString(36).slice(2, 8);
}

function defaultParams(manifest: NodeManifest): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  for (const spec of manifest.params) {
    params[spec.name] = spec.default ?? null;
  }
  if (manifest.id === "webhook_trigger") {
    params.path = `webhook-${randomSlug()}`;
  }
  return params;
}

const STRUCTURAL = new Set(["position", "remove", "add", "replace"]);
const HISTORY_LIMIT = 50;

/** Detect when a node/edge change should commit a history entry.
 *  Position changes only commit on drop (`dragging===false`); everything
 *  structural always commits.
 */
function shouldCommitChanges(changes: Array<{ type: string; dragging?: boolean }>): boolean {
  return changes.some((c) => {
    if (c.type === "position") return c.dragging === false;
    return c.type === "remove" || c.type === "add" || c.type === "replace";
  });
}

function cloneParams(params: Record<string, unknown>): Record<string, unknown> {
  try {
    return structuredClone(params) as Record<string, unknown>;
  } catch {
    return JSON.parse(JSON.stringify(params)) as Record<string, unknown>;
  }
}

function layoutPositions(
  nodes: NoodleNode[],
  edges: Edge[],
): Record<string, { x: number; y: number }> {
  const incoming = new Map<string, number>();
  const outgoing = new Map<string, string[]>();
  const layer = new Map<string, number>();

  for (const node of nodes) {
    incoming.set(node.id, 0);
    outgoing.set(node.id, []);
    layer.set(node.id, 0);
  }
  for (const edge of edges) {
    if (!incoming.has(edge.target) || !outgoing.has(edge.source)) continue;
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
    outgoing.get(edge.source)?.push(edge.target);
  }

  const queue = nodes
    .filter((node) => (incoming.get(node.id) ?? 0) === 0)
    .sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x);
  const visited = new Set<string>();

  for (let i = 0; i < queue.length; i += 1) {
    const node = queue[i];
    visited.add(node.id);
    for (const target of outgoing.get(node.id) ?? []) {
      layer.set(target, Math.max(layer.get(target) ?? 0, (layer.get(node.id) ?? 0) + 1));
      const nextIncoming = (incoming.get(target) ?? 1) - 1;
      incoming.set(target, nextIncoming);
      if (nextIncoming === 0) {
        const targetNode = nodes.find((n) => n.id === target);
        if (targetNode) queue.push(targetNode);
      }
    }
  }

  for (const node of nodes) {
    if (visited.has(node.id)) continue;
    const upstreamLayers = edges
      .filter((edge) => edge.target === node.id)
      .map((edge) => (layer.get(edge.source) ?? 0) + 1);
    layer.set(node.id, upstreamLayers.length ? Math.max(...upstreamLayers) : 0);
  }

  const groups = new Map<number, NoodleNode[]>();
  for (const node of nodes) {
    const group = layer.get(node.id) ?? 0;
    groups.set(group, [...(groups.get(group) ?? []), node]);
  }

  const result: Record<string, { x: number; y: number }> = {};
  for (const [group, groupNodes] of groups.entries()) {
    const sorted = [...groupNodes].sort(
      (a, b) => a.position.y - b.position.y || a.position.x - b.position.x,
    );
    sorted.forEach((node, index) => {
      result[node.id] = {
        x: 48 + group * 300,
        y: 42 + index * 150,
      };
    });
  }
  return result;
}

export const useEditor = create<EditorStore>((set, get) => ({
  manifests: [],
  manifestsById: {},
  nodes: [],
  edges: [],
  selectedId: null,
  dirty: false,

  ndvOpenId: null,
  runHandler: null,

  runId: null,
  running: false,
  runStatus: {},
  runOutputs: {},
  runMeta: {},
  runError: null,

  workflowId: null,
  pinned: {},

  setManifests: (manifests) =>
    set({
      manifests,
      manifestsById: Object.fromEntries(manifests.map((m) => [m.id, m])),
    }),

  loadGraph: (graph, opts) => {
    const byId = get().manifestsById;
    const nodes: NoodleNode[] = [];
    for (const n of graph.nodes) {
      const manifest = byId[n.type];
      if (!manifest) continue;
      const params = n.params ?? {};
      let outputsOverride: string[] | null = n.outputs_override ?? null;
      if (!outputsOverride && manifest.id === "switch") {
        outputsOverride = deriveSwitchOutputs(params.rules);
      }
      nodes.push({
        id: n.id,
        type: "noodle",
        position: n.position,
        data: {
          manifest,
          params,
          disabled: Boolean(n.disabled),
          outputsOverride,
          onError: typeof n.on_error === "string" ? n.on_error : "stop",
          retryOnFail: Boolean(n.retry_on_fail),
          retries: typeof n.retries === "number" ? n.retries : 1,
          retryWaitSeconds:
            typeof n.retry_wait_seconds === "number" ? n.retry_wait_seconds : 0,
          retryBackoff: Boolean(n.retry_backoff),
          alwaysOutputData: Boolean(n.always_output_data),
          timeoutSeconds:
            typeof n.timeout_seconds === "number" ? n.timeout_seconds : null,
        },
      });
    }
    const edges: Edge[] = graph.edges.map((e, i) => ({
      id: e.id || `e_${i}`,
      source: e.source,
      sourceHandle: e.source_output,
      target: e.target,
      targetHandle: e.target_input,
    }));
    set({
      nodes,
      edges,
      selectedId: null,
      dirty: Boolean(opts?.dirty),
      _past: [],
      _future: [],
    });
  },

  toGraph: () => {
    const { nodes, edges } = get();
    return {
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.data.manifest.id,
        params: n.data.params,
        position: { x: n.position.x, y: n.position.y },
        disabled: Boolean(n.data.disabled),
        outputs_override: n.data.outputsOverride,
        on_error: n.data.onError ?? "stop",
        retry_on_fail: Boolean(n.data.retryOnFail),
        retries: typeof n.data.retries === "number" ? n.data.retries : 1,
        retry_wait_seconds:
          typeof n.data.retryWaitSeconds === "number"
            ? n.data.retryWaitSeconds
            : 0,
        retry_backoff: Boolean(n.data.retryBackoff),
        always_output_data: Boolean(n.data.alwaysOutputData),
        timeout_seconds:
          typeof n.data.timeoutSeconds === "number"
            ? n.data.timeoutSeconds
            : null,
      })),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        source_output: e.sourceHandle ?? "main",
        target: e.target,
        target_input: e.targetHandle ?? "input",
      })),
    };
  },

  onNodesChange: (changes) => {
    const structural = changes.some((c) => STRUCTURAL.has(c.type));
    const commit = shouldCommitChanges(changes as Array<{ type: string; dragging?: boolean }>);
    const state = get();
    set({
      nodes: applyNodeChanges(changes, state.nodes),
      dirty: state.dirty || structural,
      ...(commit
        ? {
            _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
            _future: [],
          }
        : {}),
    });
  },

  onEdgesChange: (changes) => {
    const structural = changes.some((c) => STRUCTURAL.has(c.type));
    const commit = shouldCommitChanges(changes as Array<{ type: string; dragging?: boolean }>);
    const state = get();
    set({
      edges: applyEdgeChanges(changes, state.edges),
      dirty: state.dirty || structural,
      ...(commit
        ? {
            _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
            _future: [],
          }
        : {}),
    });
  },

  onConnect: (connection) => {
    const state = get();
    const kept = state.edges.filter(
      (e) =>
        !(
          e.target === connection.target &&
          e.targetHandle === connection.targetHandle
        ),
    );
    set({
      edges: addEdge(connection, kept),
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  addNode: (manifestId, position) => {
    const state = get();
    const manifest = state.manifestsById[manifestId];
    if (!manifest) return;
    const node: NoodleNode = {
      id: newNodeId(),
      type: "noodle",
      position,
      data: {
        manifest,
        params: defaultParams(manifest),
        disabled: false,
        outputsOverride:
          manifest.id === "switch" ? ["fallback"] : null,
        onError: "stop",
        retryOnFail: false,
        retries: 1,
        retryWaitSeconds: 0,
        retryBackoff: false,
        alwaysOutputData: false,
        timeoutSeconds: null,
      },
    };
    set({
      nodes: [...state.nodes, node],
      selectedId: node.id,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  addStickyNote: (position) => {
    const state = get();
    const node = {
      id: newNodeId(),
      type: "sticky",
      position,
      data: { content: "", color: "yellow" },
    } as unknown as NoodleNode;
    set({
      nodes: [...state.nodes, node],
      selectedId: node.id,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  autoLayout: () => {
    const state = get();
    const { nodes, edges } = state;
    if (nodes.length === 0) return;
    const positions = layoutPositions(nodes, edges);
    set({
      nodes: nodes.map((node) => ({
        ...node,
        position: positions[node.id] ?? node.position,
      })),
      dirty: true,
      _past: [...state._past, { nodes, edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  duplicateNode: (id) => {
    const state = get();
    const source = state.nodes.find((node) => node.id === id);
    if (!source) return;
    const node: NoodleNode = {
      ...source,
      id: newNodeId(),
      selected: false,
      position: {
        x: source.position.x + 48,
        y: source.position.y + 48,
      },
      data: {
        ...source.data,
        params: cloneParams(source.data.params),
      },
    };
    set({
      nodes: [...state.nodes, node],
      selectedId: node.id,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  updateParams: (id, params) => {
    const state = get();
    const node = state.nodes.find((n) => n.id === id);
    let outputsOverride = node?.data.outputsOverride ?? null;
    let edges = state.edges;
    if (node && node.data.manifest.id === "switch") {
      outputsOverride = deriveSwitchOutputs(params.rules);
      const valid = new Set(outputsOverride);
      edges = state.edges.filter(
        (e) => e.source !== id || valid.has(e.sourceHandle ?? "main"),
      );
    }
    set({
      nodes: state.nodes.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, params, outputsOverride } }
          : n,
      ),
      edges,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  setSelected: (id) => set({ selectedId: id }),
  markClean: () => set({ dirty: false }),

  openNdv: (id) => set({ ndvOpenId: id, selectedId: id }),
  closeNdv: () => set({ ndvOpenId: null }),

  deleteNode: (id) => {
    const state = get();
    set({
      nodes: state.nodes.filter((n) => n.id !== id),
      edges: state.edges.filter((e) => e.source !== id && e.target !== id),
      selectedId: state.selectedId === id ? null : state.selectedId,
      ndvOpenId: state.ndvOpenId === id ? null : state.ndvOpenId,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  toggleDisabled: (id) => {
    const state = get();
    set({
      nodes: state.nodes.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, disabled: !n.data.disabled } }
          : n,
      ),
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  updateNodeSettings: (id, patch) => {
    const state = get();
    set({
      nodes: state.nodes.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, ...patch } } : n,
      ),
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  setRunHandler: (fn) => set({ runHandler: fn }),
  runFromNode: (id, options = { reuseUpstream: true }) => {
    const handler = get().runHandler;
    if (handler) void handler([id], options);
  },
  runFromTrigger: (id) => {
    // Triggers have no ancestors — the engine's ancestor-walk would
    // collapse `targets=[id]` to "just the trigger". Use the new
    // `trigger_node_id` gating instead so the trigger AND its descendants
    // execute.
    const handler = get().runHandler;
    if (handler) void handler(undefined, { triggerNodeId: id });
  },

  startRun: (runId, targets) => {
    const { nodes, edges, runStatus } = get();
    const planned = new Set<string>();
    const targetSet = targets && targets.length > 0 ? new Set(targets) : null;
    if (targetSet) {
      const visit = (id: string) => {
        if (planned.has(id)) return;
        planned.add(id);
        for (const edge of edges) {
          if (edge.target === id) visit(edge.source);
        }
      };
      for (const target of targetSet) visit(target);
    } else {
      for (const node of nodes) planned.add(node.id);
    }

    // Keep prior runOutputs / runStatus visible — each node's own
    // node_started event will clear its slot when execution actually
    // begins. That way the Input panel (which reads upstream outputs)
    // and the Output panel stay populated with the previous run until
    // the new run replaces them, avoiding the full-blank flicker. Planned
    // nodes are marked running immediately so the canvas shows a spinner for
    // every node involved in this execution, not only the current node.
    const nextStatus = { ...runStatus };
    for (const id of planned) nextStatus[id] = "running";
    set({
      runId,
      running: true,
      runStatus: nextStatus,
      runError: null,
    });
  },

  applyRunEvent: (event) => {
    if (event.type === "node_started" && event.node_id) {
      const nid = event.node_id;
      set((state) => {
        const nextOutputs = { ...state.runOutputs };
        delete nextOutputs[nid];
        const nextMeta = { ...state.runMeta };
        delete nextMeta[nid];
        return {
          runStatus: { ...state.runStatus, [nid]: "running" },
          runOutputs: nextOutputs,
          runMeta: nextMeta,
        };
      });
    } else if (event.type === "node_finished" && event.node_id) {
      const nid = event.node_id;
      set((state) => ({
        runStatus: { ...state.runStatus, [nid]: event.status ?? "success" },
        runOutputs: { ...state.runOutputs, [nid]: event.outputs },
        runMeta: {
          ...state.runMeta,
          [nid]: {
            logs: event.logs ?? [],
            error: event.error ?? null,
            debug: event.debug ?? null,
            durationMs: event.duration_ms ?? null,
            startedAt: event.started_at ?? null,
            finishedAt: event.finished_at ?? null,
          },
        },
      }));
    } else if (event.type === "run_error") {
      set((state) => ({
        running: false,
        runError: event.error ?? "Run failed",
        runStatus: Object.fromEntries(
          Object.entries(state.runStatus).map(([id, status]) => [
            id,
            status === "running" ? "error" : status,
          ]),
        ),
      }));
    } else if (event.type === "run_cancelled") {
      set((state) => ({
        running: false,
        runError: event.error ?? "Run cancelled",
        runStatus: Object.fromEntries(
          Object.entries(state.runStatus).map(([id, status]) => [
            id,
            status === "running" ? "cancelled" : status,
          ]),
        ),
      }));
    } else if (event.type === "run_finished") {
      const fallback =
        event.status === "cancelled"
          ? "cancelled"
          : event.status === "error"
            ? "error"
            : "skipped";
      set((state) => ({
        running: false,
        runError:
          event.status === "error"
            ? (state.runError ?? "Run failed")
            : event.status === "cancelled"
              ? (state.runError ?? "Run cancelled")
              : state.runError,
        runStatus: Object.fromEntries(
          Object.entries(state.runStatus).map(([id, status]) => [
            id,
            status === "running" ? fallback : status,
          ]),
        ),
      }));
    }
  },

  clearRun: () =>
    set({
      runId: null,
      running: false,
      runStatus: {},
      runOutputs: {},
      runMeta: {},
      runError: null,
    }),

  setNodeOutput: (nodeId, outputs, status) =>
    set((state) => {
      const nextOutputs = { ...state.runOutputs };
      const nextStatus = { ...state.runStatus };
      if (outputs === undefined) {
        delete nextOutputs[nodeId];
        delete nextStatus[nodeId];
      } else {
        nextOutputs[nodeId] = outputs;
        if (status !== undefined) nextStatus[nodeId] = status;
      }
      return { runOutputs: nextOutputs, runStatus: nextStatus };
    }),

  applyRunInfo: (run) => {
    const status: Record<string, string> = {};
    const outputs: Record<string, unknown> = {};
    const meta: Record<string, NodeRunMeta> = {};
    for (const nr of run.node_runs) {
      status[nr.node_id] = nr.status;
      outputs[nr.node_id] = nr.output;
      meta[nr.node_id] = {
        logs: nr.logs ?? [],
        error: nr.error ?? null,
        debug: nr.debug ?? null,
        durationMs: nr.duration_ms ?? null,
        startedAt: nr.started_at ?? null,
        finishedAt: nr.finished_at ?? null,
      };
    }
    set({
      runId: run.id,
      runStatus: status,
      runOutputs: outputs,
      runMeta: meta,
      running: false,
      runError: null,
    });
  },

  setWorkflowId: (id) => set({ workflowId: id }),

  setPinned: (pinned) => set({ pinned }),
  setPinnedFor: (nodeId, payload) =>
    set((state) => {
      const next = { ...state.pinned };
      if (payload === null || payload === undefined) {
        delete next[nodeId];
      } else {
        next[nodeId] = payload;
      }
      return { pinned: next };
    }),

  _past: [],
  _future: [],
  undo: () => {
    const { _past, _future, nodes, edges } = get();
    if (_past.length === 0) return;
    const prev = _past[_past.length - 1];
    set({
      _past: _past.slice(0, -1),
      _future: [..._future, { nodes, edges }].slice(-HISTORY_LIMIT),
      nodes: prev.nodes,
      edges: prev.edges,
      dirty: true,
    });
  },
  redo: () => {
    const { _past, _future, nodes, edges } = get();
    if (_future.length === 0) return;
    const next = _future[_future.length - 1];
    set({
      _future: _future.slice(0, -1),
      _past: [..._past, { nodes, edges }].slice(-HISTORY_LIMIT),
      nodes: next.nodes,
      edges: next.edges,
      dirty: true,
    });
  },
}));
