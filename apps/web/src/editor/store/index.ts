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

import {
  findInputPort,
  findOutputPort,
  validateConnection,
  type ConnectionCheck,
} from "../connectionValidation";
import { fromAiExpr, isFromAiExpr, paramArgType } from "../toolParam";
import type {
  NodeManifest,
  ParamSpec,
  NodeRunDebug,
  PortSpec,
  RunEvent,
  RunInfo,
  WorkflowGraph,
} from "../../types";
import { childWorkflowInitialState } from "./childWorkflowSlice";
import { clipboardInitialState } from "./clipboardSlice";
import {
  drillInitialState,
  type DrillFrame,
  type GraphNodeShape,
  type MetaPorts,
  isMetaBar,
  drillPrefix,
  maxPortSuffix,
  META_BAR_INPUT_ID,
  META_BAR_OUTPUT_ID,
} from "./drillSlice";
import { graphInitialState } from "./graphSlice";
import { runInitialState } from "./runSlice";

export type { ChildWorkflowSlice } from "./childWorkflowSlice";
export type { ClipboardSlice } from "./clipboardSlice";
export type { DrillSliceState } from "./drillSlice";
export type { GraphSlice } from "./graphSlice";
export type { RunSlice } from "./runSlice";

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
  toolMode?: boolean;
  toolName?: string | null;
  toolDescription?: string;
  label?: string;
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
  toolMode?: boolean;
  toolName?: string | null;
  toolDescription?: string;
  label?: string;
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

export interface ClipboardResult {
  nodeCount: number;
  edgeCount: number;
}

export interface PinnedOutput {
  payload: unknown;
  updatedAt: string | null;
}

export interface ChildWorkflowState {
  workflowId: string;
  nodes: NoodleNode[];
  edges: Edge[];
  dirty: boolean;
  loading: boolean;
  error: string | null;
}

export function childToGraph(cw: ChildWorkflowState): WorkflowGraph {
  return {
    nodes: cw.nodes.map((n) => ({
      id: n.id,
      type: n.data.manifest.id,
      params: n.data.params,
      position: { x: n.position.x, y: n.position.y },
      disabled: Boolean(n.data.disabled),
      outputs_override: n.data.outputsOverride,
      on_error: n.data.onError ?? "stop",
      retry_on_fail: Boolean(n.data.retryOnFail),
      retries: typeof n.data.retries === "number" ? n.data.retries : 1,
      retry_wait_seconds: typeof n.data.retryWaitSeconds === "number" ? n.data.retryWaitSeconds : 0,
      retry_backoff: Boolean(n.data.retryBackoff),
      always_output_data: Boolean(n.data.alwaysOutputData),
      timeout_seconds: typeof n.data.timeoutSeconds === "number" ? n.data.timeoutSeconds : null,
      tool_mode: Boolean(n.data.toolMode),
      tool_name: n.data.toolName ?? null,
      tool_description: n.data.toolDescription ?? "",
      label: n.data.label || undefined,
    })),
    edges: cw.edges.map((e) => ({
      id: e.id,
      source: e.source,
      source_output: e.sourceHandle ?? "main",
      target: e.target,
      target_input: e.targetHandle ?? "input",
    })),
  };
}

/**
 * When a loop_start is removed, any surviving loop_end that pointed at it would
 * dangle (the engine rejects a Loop End whose pair is missing). Clear those
 * back-references so the orphaned Loop End is simply unpaired, not broken.
 */
function unpairOrphanedLoopEnds(nodes: NoodleNode[], deletedIds: Set<string>): NoodleNode[] {
  let unpaired = 0;
  const next = nodes.map((n) => {
    if (
      n.data.manifest?.id === "loop_end" &&
      typeof n.data.params.loop_start_id === "string" &&
      deletedIds.has(n.data.params.loop_start_id)
    ) {
      unpaired += 1;
      return { ...n, data: { ...n.data, params: { ...n.data.params, loop_start_id: "" } } };
    }
    return n;
  });
  if (unpaired > 0) {
    console.warn(
      `Unpaired ${unpaired} Loop End node(s) whose Loop Start was deleted. ` +
        "Re-pair or delete them before running.",
    );
  }
  return next;
}

// ---- Metanode helpers ---------------------------------------------------

interface GraphNodeLike {
  id: string;
  type: string;
  params: Record<string, unknown>;
  position: { x: number; y: number };
  disabled?: boolean;
  outputs_override?: string[] | null;
  on_error?: string;
  retry_on_fail?: boolean;
  retries?: number;
  retry_wait_seconds?: number;
  retry_backoff?: boolean;
  always_output_data?: boolean;
  timeout_seconds?: number | null;
  tool_mode?: boolean;
  tool_name?: string | null;
  tool_description?: string;
  label?: string;
}

interface GraphEdgeLike {
  id: string;
  source: string;
  source_output: string;
  target: string;
  target_input: string;
}

function nodeToGraphNode(n: NoodleNode): GraphNodeLike {
  return {
    id: n.id,
    type: n.data.manifest.id,
    params: n.data.params,
    position: { x: n.position.x, y: n.position.y },
    disabled: Boolean(n.data.disabled),
    outputs_override: n.data.outputsOverride,
    on_error: n.data.onError ?? "stop",
    retry_on_fail: Boolean(n.data.retryOnFail),
    retries: typeof n.data.retries === "number" ? n.data.retries : 1,
    retry_wait_seconds: typeof n.data.retryWaitSeconds === "number" ? n.data.retryWaitSeconds : 0,
    retry_backoff: Boolean(n.data.retryBackoff),
    always_output_data: Boolean(n.data.alwaysOutputData),
    timeout_seconds: typeof n.data.timeoutSeconds === "number" ? n.data.timeoutSeconds : null,
    tool_mode: Boolean(n.data.toolMode),
    tool_name: n.data.toolName ?? null,
    tool_description: n.data.toolDescription ?? "",
    label: n.data.label || undefined,
  };
}

function placeholderManifest(typeId: string): NodeManifest {
  return {
    id: typeId,
    name: typeId,
    category: "Unavailable",
    version: "0",
    description: "This node type isn't available in this editor build.",
    icon: "warning",
    inputs: [{ name: "input", description: "", data_kind: "any" }],
    outputs: [{ name: "main", description: "", data_kind: "any" }],
    params: [],
  } as NodeManifest;
}

function graphNodeToNode(
  gn: GraphNodeLike,
  byId: Record<string, NodeManifest>,
): NoodleNode | null {
  let manifest = byId[gn.type];
  let unavailableType: string | undefined;
  if (!manifest && gn.type === "meta_node") {
    const ports = ((gn.params ?? {}).ports ?? {}) as {
      inputs?: { port: string }[];
      outputs?: { port: string }[];
    };
    manifest = buildMetanodeManifest(
      (ports.inputs ?? []).map((p) => p.port),
      (ports.outputs ?? []).map((p) => p.port),
    );
  }
  if (!manifest) {
    manifest = placeholderManifest(gn.type);
    unavailableType = gn.type;
  }
  return {
    id: gn.id,
    type: gn.type === "map_group" ? "mapGroup" : "noodle",
    position: gn.position,
    data: {
      manifest,
      params: gn.params ?? {},
      disabled: Boolean(gn.disabled),
      outputsOverride: gn.outputs_override ?? null,
      onError: typeof gn.on_error === "string" ? gn.on_error : "stop",
      retryOnFail: Boolean(gn.retry_on_fail),
      retries: typeof gn.retries === "number" ? gn.retries : 1,
      retryWaitSeconds: typeof gn.retry_wait_seconds === "number" ? gn.retry_wait_seconds : 0,
      retryBackoff: Boolean(gn.retry_backoff),
      alwaysOutputData: Boolean(gn.always_output_data),
      timeoutSeconds: typeof gn.timeout_seconds === "number" ? gn.timeout_seconds : null,
      toolMode: Boolean(gn.tool_mode),
      toolName: gn.tool_name ?? null,
      toolDescription: typeof gn.tool_description === "string" ? gn.tool_description : "",
      label: gn.label || undefined,
      ...(unavailableType ? { unavailableType } : {}),
    },
  } as NoodleNode;
}

function edgeToGraphEdge(e: Edge): GraphEdgeLike {
  return {
    id: e.id,
    source: e.source,
    source_output: e.sourceHandle ?? "main",
    target: e.target,
    target_input: e.targetHandle ?? "input",
  };
}

function graphEdgeToEdge(ge: GraphEdgeLike): Edge {
  return {
    id: ge.id,
    source: ge.source,
    sourceHandle: ge.source_output,
    target: ge.target,
    targetHandle: ge.target_input,
  } as Edge;
}

function buildMetanodeManifest(inputs: string[], outputs: string[]): NodeManifest {
  const toPort = (name: string): PortSpec => ({ name, description: "", data_kind: "any" });
  const spec = (overrides: Partial<ParamSpec> & { name: string }): ParamSpec => ({
    type: "string", required: false, default: "", description: "",
    placeholder: "", choices: null, multiline: false, key_value: false,
    ...overrides,
  });
  return {
    id: "meta_node",
    name: "Metanode",
    category: "Logic",
    version: "1.0.0",
    description: "A group of nodes collapsed into one.",
    icon: "stack",
    inputs: inputs.map(toPort),
    outputs: outputs.length > 0 ? outputs.map(toPort) : [toPort("main")],
    params: [
      spec({ name: "name", default: "Metanode", description: "Display name for this group." }),
      spec({
        name: "execution",
        default: "transparent",
        choices: ["transparent", "isolated"],
        description:
          "transparent = inlined into the parent at run time (pure grouping); " +
          "isolated = runs as its own nested execution with its own scope.",
        display_name: "Execution",
      }),
    ],
  } as NodeManifest;
}

// ---- Drill-in helpers ------------------------------------------------------

function makeBar(
  side: "input" | "output",
  ports: { id: string; label: string }[],
  position: { x: number; y: number },
): NoodleNode {
  return {
    id: side === "input" ? META_BAR_INPUT_ID : META_BAR_OUTPUT_ID,
    type: "metaBar",
    position,
    draggable: false,
    deletable: false,
    selectable: false,
    data: { bar: side, ports },
  } as unknown as NoodleNode;
}

interface InteriorMaterial {
  nodes: NoodleNode[];
  edges: Edge[];
}

function materializeInterior(
  meta: NoodleNode,
  byId: Record<string, NodeManifest>,
): InteriorMaterial {
  const params = meta.data.params as {
    subgraph?: { nodes: GraphNodeLike[]; edges: GraphEdgeLike[] };
    ports?: MetaPorts;
  };
  const sub = params.subgraph ?? { nodes: [], edges: [] };
  const ports = params.ports ?? { inputs: [], outputs: [] };

  const interiorNodes = sub.nodes.map((gn) => graphNodeToNode(gn, byId)!).filter(Boolean);
  const interiorEdges = sub.edges.map(graphEdgeToEdge);

  const xs = interiorNodes.map((n) => n.position.x);
  const ys = interiorNodes.map((n) => n.position.y);
  const minX = xs.length ? Math.min(...xs) : 0;
  const maxX = xs.length ? Math.max(...xs) : 200;
  const minY = ys.length ? Math.min(...ys) : 0;
  const GAP = 240;

  const inputBar = makeBar(
    "input",
    ports.inputs.map((p) => ({ id: p.port, label: p.port })),
    { x: minX - GAP, y: minY },
  );
  const outputBar = makeBar(
    "output",
    ports.outputs.map((p) => ({ id: p.port, label: p.port })),
    { x: maxX + GAP, y: minY },
  );

  const proxyEdges: Edge[] = [];
  for (const p of ports.inputs) {
    for (const t of p.targets) {
      proxyEdges.push({
        id: `proxy_in_${p.port}_${t.target}_${t.target_input}`,
        source: META_BAR_INPUT_ID,
        sourceHandle: p.port,
        target: t.target,
        targetHandle: t.target_input,
      } as Edge);
    }
  }
  for (const p of ports.outputs) {
    proxyEdges.push({
      id: `proxy_out_${p.port}_${p.source}_${p.source_output}`,
      source: p.source,
      sourceHandle: p.source_output,
      target: META_BAR_OUTPUT_ID,
      targetHandle: p.port,
    } as Edge);
  }

  return {
    nodes: [inputBar, ...interiorNodes, outputBar],
    edges: [...interiorEdges, ...proxyEdges],
  };
}

function origByIdOf(meta: NoodleNode): Record<string, GraphNodeShape> {
  const sub = (meta.data.params as { subgraph?: { nodes: GraphNodeShape[] } }).subgraph;
  return Object.fromEntries((sub?.nodes ?? []).map((gn) => [gn.id, gn]));
}

function foldInterior(
  liveNodes: NoodleNode[],
  liveEdges: Edge[],
  origById: Record<string, GraphNodeShape>,
): { subgraph: { nodes: GraphNodeLike[]; edges: GraphEdgeLike[] }; ports: MetaPorts } {
  const inputBar = liveNodes.find((n) => n.id === META_BAR_INPUT_ID);
  const outputBar = liveNodes.find((n) => n.id === META_BAR_OUTPUT_ID);
  const interior = liveNodes.filter((n) => !isMetaBar(n));
  const interiorIds = new Set(interior.map((n) => n.id));

  const subNodes: GraphNodeLike[] = interior.map((n) => {
    if (n.data?.unavailableType) {
      const orig = origById[n.id];
      return {
        ...(orig as unknown as GraphNodeLike),
        position: { x: n.position.x, y: n.position.y },
      };
    }
    return nodeToGraphNode(n);
  });

  const subEdges: GraphEdgeLike[] = liveEdges
    .filter((e) => interiorIds.has(e.source) && interiorIds.has(e.target))
    .map(edgeToGraphEdge);

  const inputDescriptors =
    (inputBar?.data as { ports?: { id: string }[] } | undefined)?.ports ?? [];
  const inputs = inputDescriptors.map((d) => ({
    port: d.id,
    targets: liveEdges
      .filter((e) => e.source === META_BAR_INPUT_ID && (e.sourceHandle ?? "") === d.id)
      .map((e) => ({ target: e.target, target_input: e.targetHandle ?? "input" })),
  }));

  const outputDescriptors =
    (outputBar?.data as { ports?: { id: string }[] } | undefined)?.ports ?? [];
  const outputs = outputDescriptors
    .map((d) => {
      const wire = liveEdges.find(
        (e) => e.target === META_BAR_OUTPUT_ID && (e.targetHandle ?? "") === d.id,
      );
      return wire
        ? { port: d.id, source: wire.source, source_output: wire.sourceHandle ?? "main" }
        : null;
    })
    .filter((p): p is MetaPorts["outputs"][number] => p !== null);

  return { subgraph: { nodes: subNodes, edges: subEdges }, ports: { inputs, outputs } };
}

function updateMetaNode(
  meta: NoodleNode,
  subgraph: { nodes: GraphNodeLike[]; edges: GraphEdgeLike[] },
  ports: MetaPorts,
): NoodleNode {
  return {
    ...meta,
    data: {
      ...meta.data,
      manifest: buildMetanodeManifest(
        ports.inputs.map((p) => p.port),
        ports.outputs.map((p) => p.port),
      ),
      params: { ...meta.data.params, subgraph, ports },
    },
  };
}

function reconcileParentEdges(parentEdges: Edge[], metaId: string, ports: MetaPorts): Edge[] {
  const validIn = new Set(ports.inputs.map((p) => p.port));
  const validOut = new Set(ports.outputs.map((p) => p.port));
  return parentEdges.filter((e) => {
    if (e.target === metaId && !validIn.has(e.targetHandle ?? "")) return false;
    if (e.source === metaId && !validOut.has(e.sourceHandle ?? "")) return false;
    return true;
  });
}

function serializeGraph(nodes: NoodleNode[], edges: Edge[]): WorkflowGraph {
  return {
    nodes: nodes
      .filter((n) => n.data?.manifest && !isMetaBar(n))
      .map((n) => ({
        id: n.id,
        type: n.data.manifest.id,
        params: n.data.params,
        position: { x: n.position.x, y: n.position.y },
        disabled: Boolean(n.data.disabled),
        outputs_override: n.data.outputsOverride,
        on_error: n.data.onError ?? "stop",
        retry_on_fail: Boolean(n.data.retryOnFail),
        retries: typeof n.data.retries === "number" ? n.data.retries : 1,
        retry_wait_seconds: typeof n.data.retryWaitSeconds === "number" ? n.data.retryWaitSeconds : 0,
        retry_backoff: Boolean(n.data.retryBackoff),
        always_output_data: Boolean(n.data.alwaysOutputData),
        timeout_seconds: typeof n.data.timeoutSeconds === "number" ? n.data.timeoutSeconds : null,
        tool_mode: Boolean(n.data.toolMode),
        tool_name: n.data.toolName ?? null,
        tool_description: n.data.toolDescription ?? "",
        label: n.data.label || undefined,
      })),
    edges: edges
      .filter((e) => !isMetaBar({ id: e.source }) && !isMetaBar({ id: e.target }))
      .map((e) => ({
        id: e.id,
        source: e.source,
        source_output: e.sourceHandle ?? "main",
        target: e.target,
        target_input: e.targetHandle ?? "input",
      })),
  };
}

/** Cheap cycle check over an adjacency map (DFS with a recursion stack). */
function adjacencyHasCycle(adj: Map<string, string[]>): boolean {
  const state = new Map<string, 0 | 1 | 2>(); // 0=unseen 1=on-stack 2=done
  const visit = (n: string): boolean => {
    state.set(n, 1);
    for (const m of adj.get(n) ?? []) {
      const st = state.get(m) ?? 0;
      if (st === 1) return true;
      if (st === 0 && visit(m)) return true;
    }
    state.set(n, 2);
    return false;
  };
  for (const n of adj.keys()) {
    if ((state.get(n) ?? 0) === 0 && visit(n)) return true;
  }
  return false;
}

function buildBodyIndex(childWorkflows: Record<string, ChildWorkflowState>): Record<string, string> {
  const index: Record<string, string> = {};
  for (const [mgId, cw] of Object.entries(childWorkflows)) {
    for (const node of cw.nodes) index[node.id] = mgId;
  }
  return index;
}

function buildBodyEdgeIndex(childWorkflows: Record<string, ChildWorkflowState>): Record<string, string> {
  const index: Record<string, string> = {};
  for (const [mgId, cw] of Object.entries(childWorkflows)) {
    for (const edge of cw.edges) index[edge.id] = mgId;
  }
  return index;
}

// Module-level cache so body indices are rebuilt only when childWorkflows
// reference changes (not on every node drag). Keyed by the childWorkflows
// object reference itself — a new reference (from any state update that
// touches childWorkflows) automatically invalidates both caches.
let _cachedCwRef: Record<string, ChildWorkflowState> | null = null;
let _cachedBodyIndex: Record<string, string> = {};
let _cachedBodyEdgeIndex: Record<string, string> = {};

function getBodyIndex(childWorkflows: Record<string, ChildWorkflowState>): Record<string, string> {
  if (childWorkflows !== _cachedCwRef) {
    _cachedCwRef = childWorkflows;
    _cachedBodyIndex = buildBodyIndex(childWorkflows);
    _cachedBodyEdgeIndex = buildBodyEdgeIndex(childWorkflows);
  }
  return _cachedBodyIndex;
}

function getBodyEdgeIndex(childWorkflows: Record<string, ChildWorkflowState>): Record<string, string> {
  if (childWorkflows !== _cachedCwRef) {
    _cachedCwRef = childWorkflows;
    _cachedBodyIndex = buildBodyIndex(childWorkflows);
    _cachedBodyEdgeIndex = buildBodyEdgeIndex(childWorkflows);
  }
  return _cachedBodyEdgeIndex;
}

function nodeChangeId(change: NodeChange<NoodleNode>): string {
  return change.type === "add" ? change.item.id : change.id;
}

function edgeChangeId(change: EdgeChange): string {
  return change.type === "add" ? change.item.id : change.id;
}

export const TRIGGER_CATEGORY = "Triggers";

export function isTriggerManifest(manifest: NodeManifest | null | undefined): boolean {
  return Boolean(
    manifest && (manifest.role === "trigger" || manifest.category === TRIGGER_CATEGORY),
  );
}

export function pickEditorRunTrigger(nodes: NoodleNode[]): NoodleNode | null {
  const triggers = nodes.filter((n) => isTriggerManifest(n.data.manifest));
  if (triggers.length === 0) return null;
  const manual = triggers.find((n) => n.data.manifest?.id === "manual_trigger");
  return manual ?? triggers[0];
}

export function isTriggerNode(node: NoodleNode | undefined): boolean {
  return Boolean(node && isTriggerManifest(node.data.manifest));
}

function deriveSwitchOutputs(rules: unknown): string[] {
  if (rules && typeof rules === "object" && !Array.isArray(rules)) {
    const keys = Object.keys(rules as Record<string, unknown>);
    return [...keys, "fallback"];
  }
  return ["fallback"];
}

// Mirrors `noodle_nodes.builtin.discover_code_output_ports`: scans the user's
// Code-node source for `output` and `output_<name>` assignments so the editor
// can render the right number of output handles before the workflow runs.
function deriveCodeOutputs(code: unknown): string[] {
  if (typeof code !== "string" || code.length === 0) return ["main"];
  const ports: string[] = [];
  const seen = new Set<string>();
  let hasMain = false;
  // Match `name =` at the start of a (possibly indented? no — top-level only)
  // line, allowing `: type` and `+=` style assignments.
  const re = /^[\t ]*(output(?:_[A-Za-z0-9_]+)?)[\t ]*(?::[^=]*)?=(?!=)/gm;
  let match: RegExpExecArray | null;
  while ((match = re.exec(code)) !== null) {
    const name = match[1];
    if (name === "output") {
      hasMain = true;
      continue;
    }
    const suffix = name.slice("output_".length);
    if (!suffix || seen.has(suffix)) continue;
    seen.add(suffix);
    ports.push(suffix);
  }
  if (ports.length === 0) return ["main"];
  return hasMain ? ["main", ...ports] : ports;
}

// Mirrors `triggers._match_api_route`'s view of the route table: one output
// handle per route row's `output` name (deduped, in order). Lets the canvas
// render the API Endpoint node's branches before the workflow runs.
function deriveApiEndpointOutputs(routes: unknown): string[] {
  if (!Array.isArray(routes) || routes.length === 0) return ["main"];
  const ports: string[] = [];
  const seen = new Set<string>();
  for (const route of routes) {
    if (!route || typeof route !== "object") continue;
    const raw = (route as Record<string, unknown>).output;
    const name = typeof raw === "string" ? raw.trim() : "";
    if (!name || seen.has(name)) continue;
    seen.add(name);
    ports.push(name);
  }
  return ports.length > 0 ? ports : ["main"];
}

function shouldSeedFromAiParam(spec: ParamSpec, value: unknown): boolean {
  if (spec.credential || spec.group || spec.choices) return false;
  if (spec.type === "boolean" || spec.type === "object" || spec.type === "array") {
    return false;
  }
  if (isFromAiExpr(value)) return false;
  if (typeof value === "string") return value.trim() === "";
  return value === null || value === undefined;
}

function seedBlankToolParams(
  manifest: NodeManifest,
  params: Record<string, unknown>,
): Record<string, unknown> {
  let changed = false;
  const next: Record<string, unknown> = { ...params };
  for (const spec of manifest.params) {
    const current = Object.prototype.hasOwnProperty.call(next, spec.name)
      ? next[spec.name]
      : spec.default;
    if (!shouldSeedFromAiParam(spec, current)) continue;
    next[spec.name] = fromAiExpr(
      spec.name,
      spec.description || spec.placeholder || spec.name,
      paramArgType(spec.type),
    );
    changed = true;
  }
  return changed ? next : params;
}

const AI_INPUT_PORT_KINDS = new Set([
  "ai_language_model",
  "ai_embedding_model",
  "ai_memory",
  "ai_tool",
  "ai_output_parser",
  "ai_retriever",
  "ai_vector_store",
  "ai_document_loader",
  "ai_guardrail",
]);

function shouldEnableSourceForConnection(
  nodes: NoodleNode[],
  connection: Connection,
): boolean {
  if (!connection.source || !connection.target) return false;
  const source = nodes.find((node) => node.id === connection.source);
  const target = nodes.find((node) => node.id === connection.target);
  if (!source?.data.disabled || !target?.data.manifest) return false;
  const targetPort = findInputPort(target.data.manifest, connection.targetHandle);
  return AI_INPUT_PORT_KINDS.has(targetPort?.data_kind ?? "any");
}

function disabledAgentDependencySourceIds(
  nodes: NoodleNode[],
  edges: Edge[],
): Set<string> {
  const ids = new Set<string>();
  for (const edge of edges) {
    if (
      shouldEnableSourceForConnection(nodes, {
        source: edge.source,
        sourceHandle: edge.sourceHandle ?? null,
        target: edge.target,
        targetHandle: edge.targetHandle ?? null,
      })
    ) {
      ids.add(edge.source);
    }
  }
  return ids;
}

export type NoodleNode = Node<NoodleNodeData, string>;

// ---------------------------------------------------------------------------
// Agent live activity (n8n-style sub-node highlighting)
// ---------------------------------------------------------------------------
// While an AI Agent node runs it consults its connected model / memory and
// fires individual tool nodes. The engine streams flat agent_tool_* events over
// the run socket; we fold those into `agentActive` so the canvas can pulse each
// sub-node exactly when it is in use, instead of leaving them as a static
// "running" spinner for the whole turn.
export type AgentActivityStatus = "running" | "done" | "error";

const AGENT_MANIFEST_IDS = new Set(["ai_agent", "ai_agent_v2"]);
const AGENT_SUBNODE_HANDLES = ["model", "memory", "tool"];

function collectAgentIds(nodes: NoodleNode[]): Set<string> {
  return new Set(
    nodes
      .filter((n) => n.data?.manifest && AGENT_MANIFEST_IDS.has(n.data.manifest.id))
      .map((n) => n.id),
  );
}

/** A node is an agent "sub-node" when it feeds an agent's model/memory/tool
 *  port, or is configured as a callable tool. Such nodes are driven by live
 *  agent events rather than the normal node lifecycle, so they should not show
 *  the generic upstream "running" spinner for the whole turn. */
function isAgentSubNode(
  node: NoodleNode,
  edges: Edge[],
  agentIds: Set<string>,
): boolean {
  if (node.data.toolMode) return true;
  return edges.some(
    (e) =>
      e.source === node.id &&
      agentIds.has(e.target) &&
      AGENT_SUBNODE_HANDLES.includes(e.targetHandle ?? ""),
  );
}

function agentProviderNodeIds(
  edges: Edge[],
  agentId: string,
  handles: string[],
): string[] {
  const ids: string[] = [];
  for (const e of edges) {
    if (e.target === agentId && handles.includes(e.targetHandle ?? "")) {
      ids.push(e.source);
    }
  }
  return ids;
}

function allowsMultipleTargetConnections(
  nodes: NoodleNode[],
  connection: Connection,
): boolean {
  if (!connection.target) return false;
  const target = nodes.find((node) => node.id === connection.target);
  if (!target || !AGENT_MANIFEST_IDS.has(target.data.manifest.id)) return false;
  const targetPort = findInputPort(target.data.manifest, connection.targetHandle);
  return (
    (connection.targetHandle ?? targetPort?.name ?? "input") === "tool" &&
    targetPort?.data_kind === "ai_tool"
  );
}

function keepEdgesForConnection(
  edges: Edge[],
  nodes: NoodleNode[],
  connection: Connection,
): Edge[] {
  const targetHandle = connection.targetHandle ?? null;
  const sourceHandle = connection.sourceHandle ?? null;
  if (!allowsMultipleTargetConnections(nodes, connection)) {
    return edges.filter(
      (e) => !(e.target === connection.target && (e.targetHandle ?? null) === targetHandle),
    );
  }

  // Agent tool ports can accept multiple tools, but avoid adding an exact
  // duplicate wire when the user reconnects the same handle pair.
  return edges.filter(
    (e) =>
      !(
        e.source === connection.source &&
        (e.sourceHandle ?? null) === sourceHandle &&
        e.target === connection.target &&
        (e.targetHandle ?? null) === targetHandle
      ),
  );
}

/** Map a live ``tool_name`` from an agent event back to the canvas node that
 *  provides it, so we can pulse the right tile. Prefers tools wired to the
 *  agent's tool port, then any tool-mode node, then a sole connected tool. */
function resolveAgentToolNodeId(
  nodes: NoodleNode[],
  edges: Edge[],
  agentId: string,
  toolName: string,
): string | undefined {
  const want = toolName.trim().toLowerCase();
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const connected = edges
    .filter((e) => e.target === agentId && (e.targetHandle ?? "") === "tool")
    .map((e) => byId.get(e.source))
    .filter((n): n is NoodleNode => Boolean(n));
  const names = (n: NoodleNode) =>
    [
      n.data.toolName,
      n.data.params.name,
      n.data.params.tool_name,
      n.data.manifest.id,
      n.data.manifest.name,
    ]
      .map((value) => String(value ?? "").trim().toLowerCase())
      .filter(Boolean);
  for (const n of connected) {
    if (names(n).includes(want)) return n.id;
  }
  for (const n of nodes) {
    if (n.data.toolMode && names(n).includes(want)) return n.id;
  }
  if (connected.length === 1) return connected[0].id;
  return undefined;
}

function runEventToolCalls(
  event: RunEvent,
): Array<{ callId: string; toolName: string }> {
  if (!Array.isArray(event.tool_calls)) return [];
  return event.tool_calls
    .map((call, index) => {
      const toolName = String(call.name ?? call.tool_name ?? "").trim();
      if (!toolName) return null;
      const explicitId = String(call.id ?? call.tool_call_id ?? "").trim();
      const callId =
        explicitId ||
        `${event.agent_node_id ?? "agent"}:${event.step ?? 0}:${toolName}:${index}`;
      return { callId, toolName };
    })
    .filter((call): call is { callId: string; toolName: string } => Boolean(call));
}

export interface EditorStore {
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
  // Per loop-body node: how many iterations have been observed this run and the
  // latest iteration index seen. Driven by the iteration_path on node events so
  // the canvas can show a "×N" progress badge instead of the tile blinking once
  // per loop iteration.
  runIterations: Record<string, { index: number; count: number }>;
  // Per node: text streamed live via node_chunk events while it runs (e.g. LLM
  // tokens). Cleared when the node restarts or finishes (its final output then
  // supersedes the preview).
  runChunks: Record<string, string>;
  runError: string | null;

  // Live agent sub-node activity: nodeId -> status. Driven by agent_tool_*
  // events so connected model / memory / tool tiles light up as they're used.
  agentActive: Record<string, AgentActivityStatus>;
  // Maps an in-flight tool_call_id -> the node providing that tool, so a
  // finish event can release the right tile (ref-counted for parallel calls).
  agentToolCalls: Record<string, string>;

  // Current run-environment context, mirrored from EditorPage so the NDV can
  // warn when a node needs a package the workflow's env doesn't have.
  envId: string | null;
  envName: string | null;
  envPackages: string[];
  environmentsList: { id: string; name: string; packages: string[]; backend?: string }[];
  applyEnvSwitch: ((id: string) => void) | null;
  setEnvContext: (ctx: {
    envId: string | null;
    envName: string | null;
    envPackages: string[];
    environmentsList: { id: string; name: string; packages: string[]; backend?: string }[];
  }) => void;
  setEnvPackages: (packages: string[]) => void;
  setApplyEnvSwitch: (fn: ((id: string) => void) | null) => void;

  setManifests: (manifests: NodeManifest[]) => void;
  loadGraph: (graph: WorkflowGraph, opts?: { dirty?: boolean }) => void;
  toGraph: () => WorkflowGraph;
  onNodesChange: (changes: NodeChange<NoodleNode>[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => ConnectionCheck;
  addNode: (manifestId: string, position: { x: number; y: number }) => void;
  insertNodeBetweenEdge: (
    edgeId: string,
    manifestId: string,
    position: { x: number; y: number },
  ) => { ok: boolean; nodeId?: string; message?: string };
  insertQuickFixNode: (quickFixId: NonNullable<ConnectionCheck["quickFixId"]>, connection: Connection) => { ok: boolean; check: ConnectionCheck; nodeId?: string };
  addStickyNote: (position: { x: number; y: number }) => void;
  addGroupNode: (position: { x: number; y: number }) => void;
  autoLayout: () => void;
  duplicateNode: (id: string) => void;
  collapseToMetanode: (nodeIds: string[]) => string | null;
  ungroupMetanode: (id: string) => void;
  copySelection: () => ClipboardResult;
  cutSelection: () => ClipboardResult;
  pasteSelection: () => ClipboardResult;
  clipboardNodeCount: number;
  _clipboard: EditorClipboard | null;
  updateParams: (id: string, params: Record<string, unknown>) => void;
  replaceNodeManifest: (id: string, manifest: NodeManifest) => void;
  setSelected: (id: string | null) => void;
  markClean: () => void;

  ndvOpenId: string | null;
  openNdv: (id: string) => void;
  closeNdv: () => void;
  chatOpen: boolean;
  openChat: () => void;
  closeChat: () => void;
  showLoopFrames: boolean;
  toggleLoopFrames: () => void;

  deleteNode: (id: string) => void;
  deleteSelection: () => number;
  selectAll: () => void;
  duplicateSelection: () => number;
  toggleDisabled: (id: string) => void;
  autoEnableAgentDependencies: () => number;
  updateNodeSettings: (id: string, patch: NodeSettingsPatch) => void;

  runHandler: ((targets?: string[], options?: RunOptions) => Promise<void>) | null;
  setRunHandler: (
    fn: ((targets?: string[], options?: RunOptions) => Promise<void>) | null,
  ) => void;
  runFromNode: (id: string, options?: RunOptions) => void;
  runFromTrigger: (id: string) => void;

  startRun: (runId: string, targets?: string[], cache?: Record<string, unknown>) => void;
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

  pinned: Record<string, PinnedOutput>;
  setPinned: (pinned: Record<string, PinnedOutput>) => void;
  setPinnedFor: (nodeId: string, payload: unknown | null, updatedAt?: string | null) => void;

  // History (undo/redo) — snapshots of {nodes, edges} only. Reset whenever
  // `loadGraph` is called for a different workflow so undo never crosses
  // workflow boundaries.
  _past: Array<{ nodes: NoodleNode[]; edges: Edge[] }>;
  _future: Array<{ nodes: NoodleNode[]; edges: Edge[] }>;
  undo: () => void;
  redo: () => void;

  // Metanode drill-in editing.
  drillStack: DrillFrame[];
  drillOrig: Record<string, GraphNodeShape>;
  drillPortSeq: number;
  enterMetanode: (metaId: string) => void;
  exitMetanode: () => void;
  exitToDepth: (depth: number) => void;
  addMetaPort: (side: "input" | "output") => void;
  removeMetaPort: (side: "input" | "output", portId: string) => void;
  runKeyFor: (nodeId: string) => string;
  drillStepRunDisabledReason: () => string | null;

  devMode: boolean;
  toggleDevMode: () => void;

  // Map Group inline editing — child workflow graphs keyed by map_group node ID.
  childWorkflows: Record<string, ChildWorkflowState>;
  loadChildGraph: (mapGroupId: string, workflowId: string, graph: WorkflowGraph) => void;
  setChildWorkflowLoading: (mapGroupId: string, loading: boolean, error?: string | null) => void;
  markChildClean: (mapGroupId: string) => void;
  removeChildWorkflow: (mapGroupId: string) => void;
  addBodyNode: (mapGroupId: string, manifestId: string, position: { x: number; y: number }) => void;
}

let seq = 0;
function newNodeId(): string {
  seq += 1;
  return `n_${Date.now().toString(36)}_${seq}`;
}

function newEdgeId(
  source: string,
  target: string,
  sourceHandle: string | null | undefined,
  targetHandle: string | null | undefined,
): string {
  seq += 1;
  return `e_${source}_${target}_${sourceHandle ?? "main"}_${targetHandle ?? "input"}_${seq}`;
}

interface EditorClipboard {
  nodes: NoodleNode[];
  edges: Edge[];
  pasteCount: number;
}

// editorClipboard is now kept in the store state (_clipboard) so each editor
// instance has its own clipboard and tabs don't bleed into each other.
// This declaration is intentionally removed; references below use get()._clipboard.

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

// Coalesce the history commit when one user gesture is split across separate
// change handlers in the same tick (React Flow deletes a node by emitting the
// node-removal and its connected-edge-removals as two calls). The first commit
// in a tick captures the pristine pre-change state; later commits in the same
// tick reuse it instead of pushing a second, half-mutated snapshot. Reset on the
// next microtask so distinct user gestures each get their own history entry.
let _historyTickOpen = false;
function openHistoryTick(): boolean {
  if (_historyTickOpen) return false;
  _historyTickOpen = true;
  queueMicrotask(() => {
    _historyTickOpen = false;
  });
  return true;
}

function cloneParams(params: Record<string, unknown>): Record<string, unknown> {
  return cloneValue(params) as Record<string, unknown>;
}

function cloneValue<T>(value: T): T {
  try {
    return structuredClone(value) as T;
  } catch {
    return JSON.parse(JSON.stringify(value)) as T;
  }
}

function cloneNode(node: NoodleNode): NoodleNode {
  const cloned = cloneValue(node);
  delete (cloned as { dragging?: boolean }).dragging;
  delete (cloned as { resizing?: boolean }).resizing;
  return cloned;
}

function selectedNodes(nodes: NoodleNode[], selectedId: string | null): NoodleNode[] {
  const selected = nodes.filter((node) => node.selected && !isMetaBar(node));
  if (selected.length > 0) return selected;
  const fallback = selectedId ? nodes.find((node) => node.id === selectedId && !isMetaBar(node)) : null;
  return fallback ? [fallback] : [];
}

function buildClipboard(nodes: NoodleNode[], edges: Edge[]): EditorClipboard | null {
  if (nodes.length === 0) return null;
  const copiedIds = new Set(nodes.map((node) => node.id));
  const copiedEdges = edges.filter(
    (edge) => copiedIds.has(edge.source) && copiedIds.has(edge.target),
  );
  return {
    nodes: nodes.map((node) => ({
      ...cloneNode(node),
      selected: false,
    })),
    edges: copiedEdges.map((edge) => ({
      ...cloneValue(edge),
      selected: false,
    })),
    pasteCount: 0,
  };
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

/** The innermost loop iteration index from a node event's iteration_path, or
 * null when the event didn't come from inside a loop body. */
function loopIterationIndex(path: number[] | null | undefined): number | null {
  if (!Array.isArray(path) || path.length === 0) return null;
  const last = path[path.length - 1];
  return typeof last === "number" ? last : null;
}

export const useEditor = create<EditorStore>((set, get) => ({
  ...graphInitialState,
  ...runInitialState,
  ...clipboardInitialState,
  ...childWorkflowInitialState,
  ...drillInitialState,
  setEnvContext: (ctx) => set(ctx),
  setEnvPackages: (packages) => set({ envPackages: packages }),
  setApplyEnvSwitch: (fn) => set({ applyEnvSwitch: fn }),

  setManifests: (manifests) =>
    set({
      manifests,
      manifestsById: Object.fromEntries(manifests.map((m) => [m.id, m])),
    }),

  loadGraph: (graph, opts) => {
    const byId = get().manifestsById;
    const nodes: NoodleNode[] = [];
    for (const n of graph.nodes) {
      // Metanodes carry an embedded sub-graph; rebuild their (synthetic)
      // manifest from the stored boundary ports so they survive a reload even
      // though "meta_node" isn't a backend-registered node type.
      let manifest = byId[n.type];
      if (!manifest && n.type === "meta_node") {
        const ports = ((n.params ?? {}).ports ?? {}) as {
          inputs?: { port: string }[];
          outputs?: { port: string }[];
        };
        manifest = buildMetanodeManifest(
          (ports.inputs ?? []).map((p) => p.port),
          (ports.outputs ?? []).map((p) => p.port),
        );
      }
      if (!manifest) continue;
      const params = n.params ?? {};
      let outputsOverride: string[] | null = n.outputs_override ?? null;
      if (!outputsOverride && manifest.id === "switch") {
        outputsOverride = deriveSwitchOutputs(params.rules);
      }
      if (!outputsOverride && manifest.id === "code") {
        outputsOverride = deriveCodeOutputs(params.code);
      }
      if (!outputsOverride && manifest.id === "api_endpoint") {
        outputsOverride = deriveApiEndpointOutputs(params.routes);
      }
      const rfType = n.type === "map_group" ? "mapGroup" : "noodle";
      nodes.push({
        id: n.id,
        type: rfType,
        position: n.position,
        ...(rfType === "mapGroup" ? { style: { width: 380, height: 280, zIndex: -1 } } : {}),
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
          toolMode: Boolean(n.tool_mode),
          toolName: typeof n.tool_name === "string" ? n.tool_name : null,
          toolDescription:
            typeof n.tool_description === "string" ? n.tool_description : "",
          label: typeof n.label === "string" && n.label ? n.label : undefined,
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
      childWorkflows: {},
      drillStack: [],
      drillOrig: {},
      drillPortSeq: 1,
    });
  },

  toGraph: () => {
    const state = get();
    if (state.drillStack.length === 0) {
      return serializeGraph(state.nodes, state.edges);
    }
    // Fold the live interior up the stack to reconstruct the root workflow.
    let nodes = state.nodes;
    let edges = state.edges;
    let orig = state.drillOrig;
    for (let i = state.drillStack.length - 1; i >= 0; i -= 1) {
      const frame = state.drillStack[i];
      const { subgraph, ports } = foldInterior(nodes, edges, orig);
      const meta = frame.nodes.find((n) => n.id === frame.metaId);
      const parentNodes = meta
        ? frame.nodes.map((n) => (n.id === frame.metaId ? updateMetaNode(meta, subgraph, ports) : n))
        : frame.nodes;
      nodes = parentNodes;
      edges = reconcileParentEdges(frame.edges, frame.metaId, ports);
      orig = frame.orig;
    }
    return serializeGraph(nodes, edges);
  },

  onNodesChange: (changes) => {
    const state = get();
    const bodyIndex = getBodyIndex(state.childWorkflows);

    const parentChanges: NodeChange<NoodleNode>[] = [];
    const byGroup: Record<string, NodeChange<NoodleNode>[]> = {};
    for (const change of changes) {
      const mgId = bodyIndex[nodeChangeId(change)];
      if (mgId) (byGroup[mgId] ??= []).push(change);
      else parentChanges.push(change);
    }

    const structural = parentChanges.some((c) => STRUCTURAL.has(c.type));
    const commit = shouldCommitChanges(parentChanges as Array<{ type: string; dragging?: boolean }>);
    const pushHistory = commit && openHistoryTick();

    const nextCw = { ...state.childWorkflows };
    for (const [mgId, grpChanges] of Object.entries(byGroup)) {
      const cw = nextCw[mgId];
      if (!cw) continue;
      nextCw[mgId] = {
        ...cw,
        nodes: applyNodeChanges(grpChanges, cw.nodes),
        dirty: cw.dirty || grpChanges.some((c) => STRUCTURAL.has(c.type)),
      };
    }

    set({
      nodes: applyNodeChanges(parentChanges, state.nodes),
      childWorkflows: nextCw,
      dirty: state.dirty || structural,
      ...(pushHistory
        ? {
            _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
            _future: [],
          }
        : {}),
    });
  },

  onEdgesChange: (changes) => {
    const state = get();
    const bodyEdgeIndex = getBodyEdgeIndex(state.childWorkflows);

    const parentChanges: EdgeChange[] = [];
    const byGroup: Record<string, EdgeChange[]> = {};
    for (const change of changes) {
      const mgId = bodyEdgeIndex[edgeChangeId(change)];
      if (mgId) (byGroup[mgId] ??= []).push(change);
      else parentChanges.push(change);
    }

    const structural = parentChanges.some((c) => STRUCTURAL.has(c.type));
    const commit = shouldCommitChanges(parentChanges as Array<{ type: string; dragging?: boolean }>);
    const pushHistory = commit && openHistoryTick();

    const nextCw = { ...state.childWorkflows };
    for (const [mgId, grpChanges] of Object.entries(byGroup)) {
      const cw = nextCw[mgId];
      if (!cw) continue;
      nextCw[mgId] = {
        ...cw,
        edges: applyEdgeChanges(grpChanges, cw.edges),
        dirty: cw.dirty || grpChanges.some((c) => STRUCTURAL.has(c.type)),
      };
    }

    set({
      edges: applyEdgeChanges(parentChanges, state.edges),
      childWorkflows: nextCw,
      dirty: state.dirty || structural,
      ...(pushHistory
        ? {
            _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
            _future: [],
          }
        : {}),
    });
  },

  onConnect: (connection) => {
    const state = get();

    // Boundary-bar connections bypass manifest validation (bar ports are `any`).
    if (
      connection.source === META_BAR_INPUT_ID ||
      connection.target === META_BAR_OUTPUT_ID
    ) {
      const s = get();
      const dupe = (e: Edge) =>
        e.source === connection.source &&
        (e.sourceHandle ?? null) === (connection.sourceHandle ?? null) &&
        e.target === connection.target &&
        (e.targetHandle ?? null) === (connection.targetHandle ?? null);
      const kept =
        connection.target === META_BAR_OUTPUT_ID
          ? s.edges.filter(
              (e) =>
                !(e.target === META_BAR_OUTPUT_ID &&
                  (e.targetHandle ?? null) === (connection.targetHandle ?? null)),
            )
          : s.edges.filter((e) => !dupe(e));
      set({
        edges: addEdge(connection, kept),
        dirty: true,
        _past: [...s._past, { nodes: s.nodes, edges: s.edges }].slice(-HISTORY_LIMIT),
        _future: [],
      });
      return { ok: true, message: "", severity: "ok" };
    }

    const allNodes = [
      ...state.nodes,
      ...Object.values(state.childWorkflows).flatMap((cw) => cw.nodes),
    ];
    const check = validateConnection(allNodes, connection);
    if (!check.ok) return check;

    const bodyIndex = getBodyIndex(state.childWorkflows);
    const sourceGroupId = bodyIndex[connection.source!];
    const targetGroupId = bodyIndex[connection.target!];

    if (sourceGroupId && sourceGroupId === targetGroupId) {
      const cw = state.childWorkflows[sourceGroupId];
      const kept = keepEdgesForConnection(cw.edges, allNodes, connection);
      set({
        childWorkflows: {
          ...state.childWorkflows,
          [sourceGroupId]: { ...cw, edges: addEdge(connection, kept), dirty: true },
        },
        _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
        _future: [],
      });
    } else {
      const kept = keepEdgesForConnection(state.edges, allNodes, connection);
      const enableSource = shouldEnableSourceForConnection(state.nodes, connection);
      set({
        nodes: enableSource
          ? state.nodes.map((node) =>
              node.id === connection.source
                ? { ...node, data: { ...node.data, disabled: false } }
                : node,
            )
          : state.nodes,
        edges: addEdge(connection, kept),
        dirty: true,
        _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
        _future: [],
      });
    }
    return check;
  },

  insertQuickFixNode: (quickFixId, connection) => {
    const state = get();
    const check = validateConnection(state.nodes, connection);
    if (check.ok) return { ok: false, check };
    const manifest = state.manifestsById[quickFixId];
    const source = state.nodes.find((node) => node.id === connection.source);
    const target = state.nodes.find((node) => node.id === connection.target);
    if (!manifest || !source || !target) return { ok: false, check };

    const helperId = newNodeId();
    const helper: NoodleNode = {
      id: helperId,
      type: "noodle",
      position: {
        x: (source.position.x + target.position.x) / 2,
        y: (source.position.y + target.position.y) / 2 + 72,
      },
      data: {
        manifest,
        params: defaultParams(manifest),
        disabled: false,
        outputsOverride: manifest.id === "switch" ? ["fallback"] : null,
        onError: "stop",
        retryOnFail: false,
        retries: 1,
        retryWaitSeconds: 0,
        retryBackoff: false,
        alwaysOutputData: false,
        timeoutSeconds: null,
      },
    };

    const kept = state.edges.filter(
      (e) =>
        !(
          e.target === connection.target &&
          e.targetHandle === connection.targetHandle
        ),
    );
    const sourceOut = connection.sourceHandle ?? source.data.manifest.outputs[0]?.name ?? "main";
    const helperIn = manifest.inputs[0]?.name ?? "input";
    const helperOut = manifest.outputs[0]?.name ?? "main";
    const targetIn = connection.targetHandle ?? target.data.manifest.inputs[0]?.name ?? "input";

    set({
      nodes: [...state.nodes, helper],
      edges: [
        ...kept,
        {
          id: `e_${connection.source}_${helperId}_${sourceOut}_${helperIn}`,
          source: connection.source!,
          sourceHandle: sourceOut,
          target: helperId,
          targetHandle: helperIn,
        },
        {
          id: `e_${helperId}_${connection.target}_${helperOut}_${targetIn}`,
          source: helperId,
          sourceHandle: helperOut,
          target: connection.target!,
          targetHandle: targetIn,
        },
      ],
      selectedId: helperId,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
    return { ok: true, check, nodeId: helperId };
  },

  insertNodeBetweenEdge: (edgeId, manifestId, position) => {
    const state = get();
    const edge = state.edges.find((item) => item.id === edgeId);
    const manifest = state.manifestsById[manifestId];
    if (!edge || !manifest) {
      return { ok: false, message: "Could not find that edge or node type." };
    }
    const source = state.nodes.find((node) => node.id === edge.source);
    const target = state.nodes.find((node) => node.id === edge.target);
    if (!source?.data?.manifest || !target?.data?.manifest) {
      return { ok: false, message: "That edge cannot be edited here." };
    }
    const nodeInput = manifest.inputs[0]?.name;
    const nodeOutput = manifest.outputs[0]?.name;
    if (!nodeInput || !nodeOutput) {
      return { ok: false, message: "Pick a node with at least one input and one output." };
    }

    const nodeId = newNodeId();
    const node: NoodleNode = {
      id: nodeId,
      type: manifest.id === "map_group" ? "mapGroup" : "noodle",
      position,
      ...(manifest.id === "map_group" ? { style: { width: 380, height: 280, zIndex: -1 } } : {}),
      data: {
        manifest,
        params: defaultParams(manifest),
        disabled: false,
        outputsOverride: manifest.id === "switch" ? ["fallback"] : null,
        onError: "stop",
        retryOnFail: false,
        retries: 1,
        retryWaitSeconds: 0,
        retryBackoff: false,
        alwaysOutputData: false,
        timeoutSeconds: null,
      },
    };

    const sourceOut = edge.sourceHandle ?? findOutputPort(source.data.manifest, edge.sourceHandle)?.name ?? "main";
    const targetIn = edge.targetHandle ?? findInputPort(target.data.manifest, edge.targetHandle)?.name ?? "input";
    const candidateNodes = [...state.nodes, node];
    const sourceCheck = validateConnection(candidateNodes, {
      source: edge.source,
      sourceHandle: sourceOut,
      target: nodeId,
      targetHandle: nodeInput,
    });
    if (!sourceCheck.ok) {
      return { ok: false, message: sourceCheck.message };
    }
    const targetCheck = validateConnection(candidateNodes, {
      source: nodeId,
      sourceHandle: nodeOutput,
      target: edge.target,
      targetHandle: targetIn,
    });
    if (!targetCheck.ok) {
      return { ok: false, message: targetCheck.message };
    }

    set({
      nodes: [...state.nodes, node],
      edges: [
        ...state.edges.filter((item) => item.id !== edgeId),
        {
          id: newEdgeId(edge.source, nodeId, sourceOut, nodeInput),
          source: edge.source,
          sourceHandle: sourceOut,
          target: nodeId,
          targetHandle: nodeInput,
        },
        {
          id: newEdgeId(nodeId, edge.target, nodeOutput, targetIn),
          source: nodeId,
          sourceHandle: nodeOutput,
          target: edge.target,
          targetHandle: targetIn,
        },
      ],
      selectedId: nodeId,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
    return { ok: true, nodeId };
  },

  addNode: (manifestId, position) => {
    const state = get();
    const manifest = state.manifestsById[manifestId];
    if (!manifest) return;

    const makeNode = (m: NodeManifest, pos: { x: number; y: number }): NoodleNode => ({
      id: newNodeId(),
      type: m.id === "map_group" ? "mapGroup" : "noodle",
      position: pos,
      ...(m.id === "map_group" ? { style: { width: 380, height: 280, zIndex: -1 } } : {}),
      data: {
        manifest: m,
        params: defaultParams(m),
        disabled: false,
        outputsOverride: m.id === "switch" ? ["fallback"] : null,
        onError: "stop",
        retryOnFail: false,
        retries: 1,
        retryWaitSeconds: 0,
        retryBackoff: false,
        alwaysOutputData: false,
        timeoutSeconds: null,
      },
    });

    // Dropping a Loop Start also drops a pre-paired Loop End so users author
    // the region as one gesture (mirrors how map_group seeds its body).
    const loopEndManifest =
      manifestId === "loop_start" ? state.manifestsById["loop_end"] : undefined;
    if (manifestId === "loop_start" && loopEndManifest) {
      const start = makeNode(manifest, position);
      const end = makeNode(loopEndManifest, { x: position.x + 320, y: position.y });
      end.data.params = { ...end.data.params, loop_start_id: start.id };
      set({
        nodes: [...state.nodes, start, end],
        selectedId: start.id,
        dirty: true,
        _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
        _future: [],
      });
      return;
    }

    const node = makeNode(manifest, position);
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

  collapseToMetanode: (nodeIds) => {
    const state = get();
    const sel = new Set(nodeIds.filter((id) => state.nodes.some((n) => n.id === id)));
    if (sel.size === 0) return null;
    const selNodes = state.nodes.filter((n) => sel.has(n.id));
    const edges = state.edges;

    const internal = edges.filter((e) => sel.has(e.source) && sel.has(e.target));
    const inbound = edges.filter((e) => !sel.has(e.source) && sel.has(e.target));
    const outbound = edges.filter((e) => sel.has(e.source) && !sel.has(e.target));

    const metaId = newNodeId();

    // Reject if collapsing would create a cycle through the metanode.
    const adj = new Map<string, string[]>();
    const addEdge = (s: string, t: string) => {
      if (s === t) return;
      (adj.get(s) ?? adj.set(s, []).get(s)!).push(t);
    };
    for (const e of edges) {
      addEdge(sel.has(e.source) ? metaId : e.source, sel.has(e.target) ? metaId : e.target);
    }
    if (adjacencyHasCycle(adj)) return null;

    // Input ports: one per distinct external (source, sourceHandle), fanning to
    // every internal target it fed.
    const inGroups = new Map<
      string,
      { port: string; src: string; srcHandle: string; targets: { target: string; target_input: string }[] }
    >();
    let inIdx = 0;
    for (const e of inbound) {
      const srcHandle = e.sourceHandle ?? "main";
      const key = `${e.source}::${srcHandle}`;
      let g = inGroups.get(key);
      if (!g) {
        g = { port: `in_${inIdx++}`, src: e.source, srcHandle, targets: [] };
        inGroups.set(key, g);
      }
      g.targets.push({ target: e.target, target_input: e.targetHandle ?? "input" });
    }

    // Output ports: one per distinct internal (source, sourceHandle) leaving.
    const outGroups = new Map<
      string,
      { port: string; src: string; srcHandle: string; consumers: { target: string; targetHandle: string }[] }
    >();
    let outIdx = 0;
    for (const e of outbound) {
      const srcHandle = e.sourceHandle ?? "main";
      const key = `${e.source}::${srcHandle}`;
      let g = outGroups.get(key);
      if (!g) {
        g = { port: `out_${outIdx++}`, src: e.source, srcHandle, consumers: [] };
        outGroups.set(key, g);
      }
      g.consumers.push({ target: e.target, targetHandle: e.targetHandle ?? "input" });
    }

    const inputs = [...inGroups.values()].map((g) => ({ port: g.port, targets: g.targets }));
    const outputs = [...outGroups.values()].map((g) => ({
      port: g.port,
      source: g.src,
      source_output: g.srcHandle,
    }));

    const subgraph = {
      nodes: selNodes.map(nodeToGraphNode),
      edges: internal.map(edgeToGraphEdge),
    };
    const cx = selNodes.reduce((a, n) => a + n.position.x, 0) / selNodes.length;
    const cy = selNodes.reduce((a, n) => a + n.position.y, 0) / selNodes.length;

    const metaNode: NoodleNode = {
      id: metaId,
      type: "noodle",
      position: { x: cx, y: cy },
      data: {
        manifest: buildMetanodeManifest(inputs.map((p) => p.port), outputs.map((p) => p.port)),
        params: { execution: "transparent", name: "Metanode", subgraph, ports: { inputs, outputs } },
        disabled: false,
        outputsOverride: null,
        onError: "stop",
        retryOnFail: false,
        retries: 1,
        retryWaitSeconds: 0,
        retryBackoff: false,
        alwaysOutputData: false,
        timeoutSeconds: null,
      },
    } as NoodleNode;

    const keepEdges = edges.filter((e) => !sel.has(e.source) && !sel.has(e.target));
    const newEdges: Edge[] = [...keepEdges];
    for (const g of inGroups.values()) {
      newEdges.push({
        id: `e_${g.src}_${metaId}_${g.port}`,
        source: g.src,
        sourceHandle: g.srcHandle,
        target: metaId,
        targetHandle: g.port,
      } as Edge);
    }
    for (const g of outGroups.values()) {
      for (const c of g.consumers) {
        newEdges.push({
          id: `e_${metaId}_${c.target}_${g.port}_${c.targetHandle}`,
          source: metaId,
          sourceHandle: g.port,
          target: c.target,
          targetHandle: c.targetHandle,
        } as Edge);
      }
    }

    set({
      nodes: [...state.nodes.filter((n) => !sel.has(n.id)), metaNode],
      edges: newEdges,
      selectedId: metaId,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
    return metaId;
  },

  ungroupMetanode: (id) => {
    const state = get();
    const meta = state.nodes.find((n) => n.id === id);
    if (!meta || meta.data.manifest.id !== "meta_node") return;
    const params = meta.data.params as {
      subgraph?: { nodes: GraphNodeLike[]; edges: GraphEdgeLike[] };
      ports?: {
        inputs?: { port: string; targets: { target: string; target_input: string }[] }[];
        outputs?: { port: string; source: string; source_output: string }[];
      };
    };
    const subgraph = params.subgraph ?? { nodes: [], edges: [] };
    const ports = params.ports ?? { inputs: [], outputs: [] };
    const byId = state.manifestsById;

    const childNodes = subgraph.nodes
      .map((gn) => graphNodeToNode(gn, byId))
      .filter((n): n is NoodleNode => n !== null);
    const childEdges = subgraph.edges.map(graphEdgeToEdge);

    const inputsByPort = new Map((ports.inputs ?? []).map((p) => [p.port, p.targets]));
    const outputsByPort = new Map((ports.outputs ?? []).map((p) => [p.port, p]));

    const restored: Edge[] = [];
    for (const e of state.edges) {
      if (e.target === id) {
        for (const t of inputsByPort.get(e.targetHandle ?? "") ?? []) {
          restored.push({
            id: `e_${e.source}_${t.target}_restored_${restored.length}`,
            source: e.source,
            sourceHandle: e.sourceHandle,
            target: t.target,
            targetHandle: t.target_input,
          } as Edge);
        }
      } else if (e.source === id) {
        const o = outputsByPort.get(e.sourceHandle ?? "");
        if (o) {
          restored.push({
            id: `e_${o.source}_${e.target}_restored_${restored.length}`,
            source: o.source,
            sourceHandle: o.source_output,
            target: e.target,
            targetHandle: e.targetHandle,
          } as Edge);
        }
      }
    }

    set({
      nodes: [...state.nodes.filter((n) => n.id !== id), ...childNodes],
      edges: [...state.edges.filter((e) => e.source !== id && e.target !== id), ...childEdges, ...restored],
      selectedId: null,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  enterMetanode: (metaId) => {
    const state = get();
    const meta = state.nodes.find((n) => n.id === metaId);
    if (!meta || meta.data.manifest.id !== "meta_node") return;
    const material = materializeInterior(meta, state.manifestsById);
    const ports = (meta.data.params as { ports?: MetaPorts }).ports ?? { inputs: [], outputs: [] };
    set({
      drillStack: [
        ...state.drillStack,
        {
          metaId,
          name: String((meta.data.params as { name?: string }).name || "Metanode"),
          nodes: state.nodes,
          edges: state.edges,
          _past: state._past,
          _future: state._future,
          orig: state.drillOrig,
          portSeq: state.drillPortSeq,
        },
      ],
      drillOrig: origByIdOf(meta),
      drillPortSeq: maxPortSuffix(ports) + 1,
      nodes: material.nodes,
      edges: material.edges,
      _past: [],
      _future: [],
      selectedId: null,
      ndvOpenId: null,
    });
  },

  exitMetanode: () => {
    const state = get();
    if (state.drillStack.length === 0) return;
    const frame = state.drillStack[state.drillStack.length - 1];
    const meta = frame.nodes.find((n) => n.id === frame.metaId);
    if (!meta) {
      set({
        nodes: frame.nodes, edges: frame.edges, _past: frame._past, _future: frame._future,
        drillStack: state.drillStack.slice(0, -1), drillOrig: frame.orig, drillPortSeq: frame.portSeq,
      });
      return;
    }
    const { subgraph, ports } = foldInterior(state.nodes, state.edges, state.drillOrig);
    const prev = meta.data.params as { subgraph?: unknown; ports?: unknown };
    const changed =
      JSON.stringify(prev.subgraph ?? null) !== JSON.stringify(subgraph) ||
      JSON.stringify(prev.ports ?? null) !== JSON.stringify(ports);
    const updatedMeta = updateMetaNode(meta, subgraph, ports);
    const parentNodes = frame.nodes.map((n) => (n.id === frame.metaId ? updatedMeta : n));
    const parentEdges = reconcileParentEdges(frame.edges, frame.metaId, ports);
    set({
      nodes: parentNodes,
      edges: parentEdges,
      _past: frame._past,
      _future: frame._future,
      drillStack: state.drillStack.slice(0, -1),
      drillOrig: frame.orig,
      drillPortSeq: frame.portSeq,
      selectedId: frame.metaId,
      dirty: state.dirty || changed,
    });
  },

  exitToDepth: (depth) => {
    while (get().drillStack.length > depth) {
      get().exitMetanode();
    }
  },

  addMetaPort: (side) => {
    const state = get();
    const barId = side === "input" ? META_BAR_INPUT_ID : META_BAR_OUTPUT_ID;
    const seq = state.drillPortSeq;
    const newId = `${side === "input" ? "in" : "out"}_${seq}`;
    set({
      nodes: state.nodes.map((n) =>
        n.id === barId
          ? {
              ...n,
              data: {
                ...n.data,
                ports: [...((n.data as unknown as { ports: { id: string; label: string }[] }).ports), { id: newId, label: newId }],
              },
            }
          : n,
      ),
      drillPortSeq: seq + 1,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  removeMetaPort: (side, portId) => {
    const state = get();
    const barId = side === "input" ? META_BAR_INPUT_ID : META_BAR_OUTPUT_ID;
    set({
      nodes: state.nodes.map((n) =>
        n.id === barId
          ? {
              ...n,
              data: {
                ...n.data,
                ports: ((n.data as unknown as { ports: { id: string }[] }).ports).filter((p) => p.id !== portId),
              },
            }
          : n,
      ),
      edges: state.edges.filter((e) =>
        side === "input"
          ? !(e.source === barId && (e.sourceHandle ?? "") === portId)
          : !(e.target === barId && (e.targetHandle ?? "") === portId),
      ),
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  addGroupNode: (position) => {
    const state = get();
    const node = {
      id: newNodeId(),
      type: "group",
      position,
      data: { label: "Group", color: "rgba(99,102,241,0.08)" },
      style: { width: 320, height: 220, zIndex: -1 },
    } as unknown as NoodleNode;
    set({
      nodes: [node, ...state.nodes],
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

  copySelection: () => {
    const state = get();
    const copiedNodes = selectedNodes(state.nodes, state.selectedId);
    const clipboard = buildClipboard(copiedNodes, state.edges);
    if (!clipboard) return { nodeCount: 0, edgeCount: 0 };
    set({ _clipboard: clipboard, clipboardNodeCount: clipboard.nodes.length });
    return { nodeCount: clipboard.nodes.length, edgeCount: clipboard.edges.length };
  },

  duplicateSelection: () => {
    const state = get();
    const targets = selectedNodes(state.nodes, state.selectedId);
    if (targets.length === 0) return 0;
    const idMap = new Map<string, string>();
    for (const node of targets) idMap.set(node.id, newNodeId());
    const pastedNodes: NoodleNode[] = targets.map((node) => ({
      ...cloneNode(node),
      id: idMap.get(node.id)!,
      selected: true,
      position: { x: node.position.x + 48, y: node.position.y + 48 },
      data: cloneValue(node.data),
      style: node.style ? cloneValue(node.style) : node.style,
      parentId:
        typeof node.parentId === "string"
          ? (idMap.get(node.parentId) ?? node.parentId)
          : node.parentId,
    }));
    const copiedIds = new Set(targets.map((n) => n.id));
    const pastedEdges: Edge[] = [];
    for (const edge of state.edges) {
      if (!copiedIds.has(edge.source) || !copiedIds.has(edge.target)) continue;
      pastedEdges.push({
        ...cloneValue(edge),
        id: newEdgeId(idMap.get(edge.source)!, idMap.get(edge.target)!, edge.sourceHandle, edge.targetHandle),
        source: idMap.get(edge.source)!,
        target: idMap.get(edge.target)!,
        selected: false,
      });
    }
    set({
      nodes: [...state.nodes.map((n) => ({ ...n, selected: false })), ...pastedNodes],
      edges: [...state.edges.map((e) => ({ ...e, selected: false })), ...pastedEdges],
      dirty: true,
      selectedId: pastedNodes.length === 1 ? pastedNodes[0].id : null,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
    return pastedNodes.length;
  },

  cutSelection: () => {
    const state = get();
    const cutNodes = selectedNodes(state.nodes, state.selectedId);
    const clipboard = buildClipboard(cutNodes, state.edges);
    if (!clipboard) return { nodeCount: 0, edgeCount: 0 };
    const cutIds = new Set(cutNodes.map((node) => node.id));
    set({
      _clipboard: clipboard,
      nodes: state.nodes.filter((node) => !cutIds.has(node.id)),
      edges: state.edges.filter((edge) => !cutIds.has(edge.source) && !cutIds.has(edge.target)),
      selectedId: state.selectedId && cutIds.has(state.selectedId) ? null : state.selectedId,
      ndvOpenId: state.ndvOpenId && cutIds.has(state.ndvOpenId) ? null : state.ndvOpenId,
      clipboardNodeCount: clipboard.nodes.length,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
    return { nodeCount: clipboard.nodes.length, edgeCount: clipboard.edges.length };
  },

  pasteSelection: () => {
    const state = get();
    const clipboard = state._clipboard;
    if (!clipboard || clipboard.nodes.length === 0 || state.clipboardNodeCount === 0) {
      return { nodeCount: 0, edgeCount: 0 };
    }
    clipboard.pasteCount += 1;
    const offset = clipboard.pasteCount * 48;
    const idMap = new Map<string, string>();
    for (const node of clipboard.nodes) {
      idMap.set(node.id, newNodeId());
    }

    const pastedNodes = clipboard.nodes.map((node) => {
      const nextId = idMap.get(node.id)!;
      const parentId =
        typeof node.parentId === "string"
          ? (idMap.get(node.parentId) ?? node.parentId)
          : node.parentId;
      return {
        ...cloneNode(node),
        id: nextId,
        parentId,
        selected: true,
        position: {
          x: node.position.x + offset,
          y: node.position.y + offset,
        },
        data: cloneValue(node.data),
        style: node.style ? cloneValue(node.style) : node.style,
      };
    });
    const pastedEdges: Edge[] = [];
    for (const edge of clipboard.edges) {
      const source = idMap.get(edge.source);
      const target = idMap.get(edge.target);
      if (!source || !target) continue;
      pastedEdges.push({
        ...cloneValue(edge),
        id: newEdgeId(source, target, edge.sourceHandle, edge.targetHandle),
        source,
        target,
        selected: false,
      });
    }

    set({
      nodes: [
        ...state.nodes.map((node) => ({ ...node, selected: false })),
        ...pastedNodes,
      ],
      edges: [...state.edges.map((edge) => ({ ...edge, selected: false })), ...pastedEdges],
      selectedId: pastedNodes.length === 1 ? pastedNodes[0].id : null,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
    return { nodeCount: pastedNodes.length, edgeCount: pastedEdges.length };
  },

  updateParams: (id, params) => {
    const state = get();
    const node = state.nodes.find((n) => n.id === id);
    let outputsOverride = node?.data.outputsOverride ?? null;
    let edges = state.edges;
    if (node && node.data.manifest?.id === "switch") {
      outputsOverride = deriveSwitchOutputs(params.rules);
      const valid = new Set(outputsOverride);
      edges = state.edges.filter(
        (e) => e.source !== id || valid.has(e.sourceHandle ?? "main"),
      );
    }
    if (node && node.data.manifest?.id === "code") {
      outputsOverride = deriveCodeOutputs(params.code);
      const valid = new Set(outputsOverride);
      edges = state.edges.filter(
        (e) => e.source !== id || valid.has(e.sourceHandle ?? "main"),
      );
    }
    if (node && node.data.manifest?.id === "api_endpoint") {
      outputsOverride = deriveApiEndpointOutputs(params.routes);
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

  replaceNodeManifest: (id, manifest) => {
    const state = get();
    const node = state.nodes.find((n) => n.id === id);
    if (!node) return;
    // Carry over any param whose name still exists on the new manifest;
    // fall back to the new manifest's defaults for everything else.
    const merged = defaultParams(manifest);
    const valid = new Set(manifest.params.map((p) => p.name));
    for (const [k, v] of Object.entries(node.data.params)) {
      if (valid.has(k)) merged[k] = v;
    }
    set({
      nodes: state.nodes.map((n) =>
        n.id === id
          ? {
              ...n,
              data: { ...n.data, manifest, params: merged, outputsOverride: null },
            }
          : n,
      ),
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  setSelected: (id) => set({ selectedId: id }),
  markClean: () => set({ dirty: false }),

  openNdv: (id) => set({ ndvOpenId: id, selectedId: id }),
  closeNdv: () => set({ ndvOpenId: null }),
  openChat: () => set({ chatOpen: true }),
  closeChat: () => set({ chatOpen: false }),
  toggleLoopFrames: () => set((s) => ({ showLoopFrames: !s.showLoopFrames })),

  deleteNode: (id) => {
    const state = get();
    if (!state.nodes.some((n) => n.id === id)) return;
    const nextCw = { ...state.childWorkflows };
    delete nextCw[id];
    const survivors = state.nodes.filter((n) => n.id !== id);
    set({
      nodes: unpairOrphanedLoopEnds(survivors, new Set([id])),
      edges: state.edges.filter((e) => e.source !== id && e.target !== id),
      childWorkflows: nextCw,
      selectedId: state.selectedId === id ? null : state.selectedId,
      ndvOpenId: state.ndvOpenId === id ? null : state.ndvOpenId,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  deleteSelection: () => {
    const state = get();
    const targets = selectedNodes(state.nodes, state.selectedId);
    if (targets.length === 0) return 0;
    const ids = new Set(targets.map((node) => node.id));
    const nextCw = { ...state.childWorkflows };
    for (const id of ids) delete nextCw[id];
    const survivors = state.nodes.filter((node) => !ids.has(node.id));
    set({
      nodes: unpairOrphanedLoopEnds(survivors, ids),
      edges: state.edges.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target)),
      childWorkflows: nextCw,
      selectedId: state.selectedId && ids.has(state.selectedId) ? null : state.selectedId,
      ndvOpenId: state.ndvOpenId && ids.has(state.ndvOpenId) ? null : state.ndvOpenId,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
    return targets.length;
  },

  selectAll: () => {
    const state = get();
    set({
      nodes: state.nodes.map((n) => ({ ...n, selected: !isMetaBar(n) })),
      selectedId: null,
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

  autoEnableAgentDependencies: () => {
    const state = get();
    const ids = disabledAgentDependencySourceIds(state.nodes, state.edges);
    if (ids.size === 0) return 0;
    set({
      nodes: state.nodes.map((node) =>
        ids.has(node.id)
          ? { ...node, data: { ...node.data, disabled: false } }
          : node,
      ),
      dirty: true,
    });
    return ids.size;
  },

  updateNodeSettings: (id, patch) => {
    const state = get();
    // Turning tool mode OFF reverts every "From AI" param back to Fixed:
    // a $fromAI() expression only resolves while the Agent drives the node, so
    // it would be dead config on a normally-wired node. Reset to spec defaults.
    const revertFromAi = patch.toolMode === false;
    const seedFromAi = patch.toolMode === true;
    set({
      nodes: state.nodes.map((n) => {
        if (n.id !== id) return n;
        let params = n.data.params;
        if (revertFromAi) {
          const defaults = new Map(
            n.data.manifest.params.map((spec) => [spec.name, spec.default]),
          );
          let changed = false;
          const next: Record<string, unknown> = { ...params };
          for (const [key, value] of Object.entries(params)) {
            if (isFromAiExpr(value)) {
              next[key] = defaults.has(key) ? defaults.get(key) ?? "" : "";
              changed = true;
            }
          }
          if (changed) params = next;
        }
        if (seedFromAi) {
          params = seedBlankToolParams(n.data.manifest, params);
        }
        return {
          ...n,
          data: {
            ...n.data,
            ...patch,
            params,
            disabled: seedFromAi ? false : n.data.disabled,
          },
        };
      }),
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  setRunHandler: (fn) => set({ runHandler: fn }),
  runKeyFor: (nodeId) => drillPrefix(get().drillStack) + nodeId,

  drillStepRunDisabledReason: () => {
    const state = get();
    if (state.drillStack.length === 0) return null;
    for (const frame of state.drillStack) {
      const meta = frame.nodes.find((n) => n.id === frame.metaId);
      const exec = String((meta?.data.params as { execution?: string } | undefined)?.execution ?? "transparent");
      if (exec === "isolated") {
        return "Step-run isn't available inside an isolated metanode — run the metanode from the parent, or set its execution to transparent.";
      }
    }
    return null;
  },

  runFromNode: (id, options = { reuseUpstream: true }) => {
    const state = get();
    if (state.drillStepRunDisabledReason()) return;
    const handler = state.runHandler;
    if (handler) void handler([state.runKeyFor(id)], options);
  },
  runFromTrigger: (id) => {
    const state = get();
    if (state.drillStepRunDisabledReason()) return;
    const handler = state.runHandler;
    if (handler) void handler(undefined, { triggerNodeId: state.runKeyFor(id) });
  },

  startRun: (runId, targets, cache?) => {
    const state = get();
    const { nodes, edges, runStatus } = state;
    const drillStack = state.drillStack;
    const prefix = drillStack.length > 0
      ? drillStack.map((f) => f.metaId).join("/") + "/"
      : "";
    const stripId = (id: string) => id.startsWith(prefix) ? id.slice(prefix.length) : id;
    const cachedIds = cache ? new Set(Object.keys(cache)) : new Set<string>();
    const planned = new Set<string>();
    const targetSet = targets && targets.length > 0
      ? new Set(targets.map(stripId))
      : null;
    if (targetSet) {
      const visit = (id: string) => {
        if (planned.has(id)) return;
        planned.add(id);
        // Stop walking into cached nodes — their outputs are already known and
        // they won't re-execute, so their ancestors aren't needed either.
        if (cachedIds.has(id)) return;
        for (const edge of edges) {
          // Skip bar-proxy edges — bars are not real runnable nodes.
          if (edge.source === META_BAR_INPUT_ID || edge.target === META_BAR_OUTPUT_ID) continue;
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
    //
    // For targeted runs: preserve status for cached upstream nodes (they're
    // being reused, not re-run) and clear nodes outside the execution scope.
    const nextStatus = targetSet
      ? Object.fromEntries(
          Object.entries(runStatus).filter(
            ([id]) => planned.has(id) || cachedIds.has(id),
          ),
        )
      : { ...runStatus };
    // Agent sub-nodes (model / memory / tools) are driven by live agent events,
    // not the normal node lifecycle, so don't pin them to a "running" spinner
    // for the whole turn — they'll pulse individually as the agent uses them.
    const agentIds = collectAgentIds(nodes);
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    for (const id of planned) {
      if (isMetaBar({ id })) continue;
      // Cached nodes already have their output; don't flash them to "running".
      if (cachedIds.has(id)) continue;
      const node = nodeById.get(id);
      if (node && isAgentSubNode(node, edges, agentIds)) continue;
      nextStatus[id] = "running";
    }
    set({
      runId,
      running: true,
      runStatus: nextStatus,
      runIterations: {},
      runChunks: {},
      runError: null,
      agentActive: {},
      agentToolCalls: {},
    });
  },

  applyRunEvent: (event) => {
    if (event.type === "node_started" && event.node_id) {
      const nid = event.node_id;
      set((state) => {
        // Loop bodies re-emit node_started for the same node id every iteration.
        // Keep the last iteration's output/meta visible while the next iteration
        // is mid-flight (only the very first iteration, or a normal one-shot
        // node, clears the prior output) so the tile doesn't blink empty N times.
        const iterIndex = loopIterationIndex(event.iteration_path);
        const isLoopReiteration = iterIndex !== null && iterIndex > 0;
        const nextOutputs = { ...state.runOutputs };
        const nextMeta = { ...state.runMeta };
        if (!isLoopReiteration) {
          delete nextOutputs[nid];
          delete nextMeta[nid];
        }
        // Each (re)start streams fresh — drop any prior preview text.
        let nextChunks = state.runChunks;
        if (nid in nextChunks) {
          nextChunks = { ...nextChunks };
          delete nextChunks[nid];
        }
        const runIterations =
          iterIndex === null
            ? state.runIterations
            : {
                ...state.runIterations,
                [nid]: {
                  index: iterIndex,
                  count: Math.max(
                    state.runIterations[nid]?.count ?? 0,
                    iterIndex + 1,
                  ),
                },
              };
        // When an agent node starts, light up its model + memory sub-nodes —
        // the agent consults them throughout the turn.
        let agentActive = state.agentActive;
        const node = state.nodes.find((n) => n.id === nid);
        if (node && AGENT_MANIFEST_IDS.has(node.data.manifest.id)) {
          const providers = agentProviderNodeIds(state.edges, nid, [
            "model",
            "memory",
          ]);
          if (providers.length) {
            agentActive = { ...agentActive };
            for (const pid of providers) agentActive[pid] = "running";
          }
        }
        return {
          runStatus: { ...state.runStatus, [nid]: "running" },
          runOutputs: nextOutputs,
          runMeta: nextMeta,
          runIterations,
          runChunks: nextChunks,
          agentActive,
        };
      });
    } else if (event.type === "node_chunk" && event.node_id) {
      const nid = event.node_id;
      // A retry emits a reset so the failed attempt's streamed text is
      // discarded rather than concatenated with the fresh stream.
      if (event.reset) {
        set((state) => ({
          runChunks: { ...state.runChunks, [nid]: "" },
        }));
        return;
      }
      const delta = event.delta;
      if (typeof delta !== "string" || delta.length === 0) return;
      set((state) => ({
        runChunks: {
          ...state.runChunks,
          [nid]: (state.runChunks[nid] ?? "") + delta,
        },
      }));
    } else if (event.type === "agent_action_requested") {
      const agentId = event.agent_node_id;
      const calls = runEventToolCalls(event);
      if (!agentId || calls.length === 0) return;
      set((state) => {
        let agentActive = state.agentActive;
        let agentToolCalls = state.agentToolCalls;
        let changed = false;
        for (const call of calls) {
          const toolNodeId = resolveAgentToolNodeId(
            state.nodes,
            state.edges,
            agentId,
            call.toolName,
          );
          if (!toolNodeId) continue;
          if (!changed) {
            agentActive = { ...agentActive };
            agentToolCalls = { ...agentToolCalls };
            changed = true;
          }
          agentActive[toolNodeId] = "running";
          agentToolCalls[call.callId] = toolNodeId;
        }
        return changed ? { agentActive, agentToolCalls } : {};
      });
    } else if (event.type === "agent_tool_started") {
      const agentId = event.agent_node_id;
      const toolName = event.tool_name;
      const callId = event.tool_call_id;
      if (!agentId || !toolName || !callId) return;
      set((state) => {
        const toolNodeId = resolveAgentToolNodeId(
          state.nodes,
          state.edges,
          agentId,
          toolName,
        );
        if (!toolNodeId) return {};
        return {
          agentActive: { ...state.agentActive, [toolNodeId]: "running" },
          agentToolCalls: { ...state.agentToolCalls, [callId]: toolNodeId },
        };
      });
    } else if (event.type === "agent_tool_finished") {
      const callId = event.tool_call_id;
      if (!callId) return;
      set((state) => {
        const toolNodeId = state.agentToolCalls[callId];
        if (!toolNodeId) return {};
        const agentToolCalls = { ...state.agentToolCalls };
        delete agentToolCalls[callId];
        // Keep the tile "running" if another in-flight call still uses it.
        const stillBusy = Object.values(agentToolCalls).includes(toolNodeId);
        const nextStatus: AgentActivityStatus = stillBusy
          ? "running"
          : event.status === "error"
            ? "error"
            : "done";
        return {
          agentActive: { ...state.agentActive, [toolNodeId]: nextStatus },
          agentToolCalls,
        };
      });
    } else if (event.type === "node_finished" && event.node_id) {
      const nid = event.node_id;
      set((state) => {
        // When an agent node finishes, release all of its sub-nodes.
        let agentActive = state.agentActive;
        let agentToolCalls = state.agentToolCalls;
        const node = state.nodes.find((n) => n.id === nid);
        if (node && AGENT_MANIFEST_IDS.has(node.data.manifest.id)) {
          const subs = new Set(
            agentProviderNodeIds(
              state.edges,
              nid,
              AGENT_SUBNODE_HANDLES,
            ),
          );
          agentActive = Object.fromEntries(
            Object.entries(agentActive).filter(([id]) => !subs.has(id)),
          );
          agentToolCalls = Object.fromEntries(
            Object.entries(agentToolCalls).filter(
              ([, tnid]) => !subs.has(tnid),
            ),
          );
        }
        let nextChunks = state.runChunks;
        if (nid in nextChunks) {
          nextChunks = { ...nextChunks };
          delete nextChunks[nid];
        }
        return {
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
          runChunks: nextChunks,
          agentActive,
          agentToolCalls,
        };
      });
    } else if (event.type === "run_error") {
      set((state) => ({
        running: false,
        runError: event.error ?? "Run failed",
        agentActive: {},
        agentToolCalls: {},
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
        agentActive: {},
        agentToolCalls: {},
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
            : event.status === "waiting"
              ? "waiting"
              : "skipped";
      set((state) => ({
        running: false,
        runError:
          event.status === "error"
            ? (state.runError ?? "Run failed")
            : event.status === "cancelled"
              ? (state.runError ?? "Run cancelled")
              : state.runError,
        agentActive: {},
        agentToolCalls: {},
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
      runIterations: {},
      runChunks: {},
      runError: null,
      agentActive: {},
      agentToolCalls: {},
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
      runIterations: {},
      runChunks: {},
      running: false,
      runError: null,
    });
  },

  setWorkflowId: (id) => set({ workflowId: id }),

  setPinned: (pinned) => set({ pinned }),
  setPinnedFor: (nodeId, payload, updatedAt = null) =>
    set((state) => {
      const next = { ...state.pinned };
      if (payload === null || payload === undefined) {
        delete next[nodeId];
      } else {
        next[nodeId] = { payload, updatedAt };
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

  toggleDevMode: () => set((s) => ({ devMode: !s.devMode })),

  loadChildGraph: (mapGroupId, workflowId, graph) => {
    const byId = get().manifestsById;
    const nodes: NoodleNode[] = [];
    for (const n of graph.nodes) {
      const manifest = byId[n.type];
      if (!manifest) continue;
      const params = n.params ?? {};
      let outputsOverride: string[] | null = n.outputs_override ?? null;
      if (!outputsOverride && manifest.id === "switch") outputsOverride = deriveSwitchOutputs(params.rules);
      if (!outputsOverride && manifest.id === "code") outputsOverride = deriveCodeOutputs(params.code);
      if (!outputsOverride && manifest.id === "api_endpoint") outputsOverride = deriveApiEndpointOutputs(params.routes);
      nodes.push({
        id: n.id,
        type: "noodle",
        position: n.position,
        parentId: mapGroupId,
        extent: "parent" as const,
        data: {
          manifest,
          params,
          disabled: Boolean(n.disabled),
          outputsOverride,
          onError: typeof n.on_error === "string" ? n.on_error : "stop",
          retryOnFail: Boolean(n.retry_on_fail),
          retries: typeof n.retries === "number" ? n.retries : 1,
          retryWaitSeconds: typeof n.retry_wait_seconds === "number" ? n.retry_wait_seconds : 0,
          retryBackoff: Boolean(n.retry_backoff),
          alwaysOutputData: Boolean(n.always_output_data),
          timeoutSeconds: typeof n.timeout_seconds === "number" ? n.timeout_seconds : null,
          toolMode: Boolean(n.tool_mode),
          toolName: typeof n.tool_name === "string" ? n.tool_name : null,
          toolDescription: typeof n.tool_description === "string" ? n.tool_description : "",
          label: typeof n.label === "string" && n.label ? n.label : undefined,
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
    set((state) => ({
      childWorkflows: {
        ...state.childWorkflows,
        [mapGroupId]: { workflowId, nodes, edges, dirty: false, loading: false, error: null },
      },
    }));
  },

  setChildWorkflowLoading: (mapGroupId, loading, error = null) =>
    set((state) => ({
      childWorkflows: {
        ...state.childWorkflows,
        [mapGroupId]: {
          ...(state.childWorkflows[mapGroupId] ?? {
            workflowId: "",
            nodes: [],
            edges: [],
            dirty: false,
          }),
          loading,
          error: loading ? null : (error ?? null),
        },
      },
    })),

  markChildClean: (mapGroupId) =>
    set((state) => {
      const cw = state.childWorkflows[mapGroupId];
      if (!cw) return state;
      return {
        childWorkflows: { ...state.childWorkflows, [mapGroupId]: { ...cw, dirty: false } },
      };
    }),

  removeChildWorkflow: (mapGroupId) =>
    set((state) => {
      const next = { ...state.childWorkflows };
      delete next[mapGroupId];
      return { childWorkflows: next };
    }),

  addBodyNode: (mapGroupId, manifestId, position) => {
    const state = get();
    const manifest = state.manifestsById[manifestId];
    if (!manifest) return;
    const cw = state.childWorkflows[mapGroupId];
    if (!cw) return;
    const node: NoodleNode = {
      id: newNodeId(),
      type: "noodle",
      position,
      parentId: mapGroupId,
      extent: "parent" as const,
      data: {
        manifest,
        params: defaultParams(manifest),
        disabled: false,
        outputsOverride: manifest.id === "switch" ? ["fallback"] : null,
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
      childWorkflows: {
        ...state.childWorkflows,
        [mapGroupId]: { ...cw, nodes: [...cw.nodes, node], dirty: true },
      },
    });
  },
}));
