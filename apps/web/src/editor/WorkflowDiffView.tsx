import { useCallback, useEffect, useMemo, useState } from "react";
import { ReactFlow, ReactFlowProvider, Background, BackgroundVariant, Panel } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";
import { api } from "../api";
import type { WorkflowGraph, WorkflowVersionInfo, DiffStatus } from "../types";
import { useEditor, graphNodeToNode, graphEdgeToEdge, type NoodleNode } from "./store";
import { DiffContext, diffWorkflowGraphs } from "./diffWorkflowGraphs";
import { diffNodeTypes, diffEdgeTypes } from "./nodeTypes";
import { DiffSummaryBar } from "./DiffSummaryBar";
import { NodeParamDiffPanel } from "./NodeParamDiffPanel";

const MAX_RENDER_NODES = 300;
const DRAFT_SENTINEL = "__draft__";

type FetchState = "idle" | "loading" | "error" | "loaded";
interface GraphFetch { state: FetchState; graph: WorkflowGraph | null; }

interface Props {
  workflowId: string;
  initialVersion: WorkflowVersionInfo;
  versions: WorkflowVersionInfo[];
  onClose: () => void;
}

// GraphDiffView: plain-graph variant — no version fetching, no pickers.
// Used for inline diffs (e.g. AI draft modal).
export interface GraphDiffViewProps {
  baseGraph: WorkflowGraph;
  compareGraph: WorkflowGraph;
  onClose?: () => void;
  rejectedNodeIds?: Set<string>;
  onToggleReject?: (nodeId: string) => void;
  readOnly?: boolean;
}

export function GraphDiffView({
  baseGraph,
  compareGraph,
  onClose,
  rejectedNodeIds,
  onToggleReject,
  readOnly,
}: GraphDiffViewProps) {
  const manifestsById = useEditor((s) => s.manifestsById);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const result = useMemo(
    () => diffWorkflowGraphs(baseGraph, compareGraph),
    [baseGraph, compareGraph],
  );

  const statusMap = useMemo(
    () =>
      result
        ? new Map<string, DiffStatus>([
            ...result.added.map((id) => [id, "added"] as const),
            ...result.changed.map((id) => [id, "changed"] as const),
            ...result.unchanged.map((id) => [id, "unchanged"] as const),
            ...result.removed.map((id) => [id, "removed"] as const),
          ])
        : new Map<string, DiffStatus>(),
    [result],
  );

  const { renderNodes, renderEdges, truncated } = useMemo(() => {
    if (!result) {
      return { renderNodes: [] as Node[], renderEdges: [] as Edge[], truncated: false };
    }

    const shownNodeIds = new Set<string>();
    const compareRF = compareGraph.nodes
      .map((gn) => {
        const status = statusMap.get(gn.id) ?? "unchanged";
        // If node is removed AND rejected, include it
        if (status === "removed" && !rejectedNodeIds?.has(gn.id)) return null;
        shownNodeIds.add(gn.id);
        return graphNodeToNode(gn, manifestsById);
      })
      .filter((n): n is NoodleNode => n !== null);

    const ghostRF = result.removedNodes
      .filter((gn) => rejectedNodeIds?.has(gn.id))
      .map((gn) => graphNodeToNode({ ...gn, position: gn.position ?? { x: 0, y: 0 } }, manifestsById))
      .filter((n): n is NoodleNode => n !== null);
    ghostRF.forEach((n) => shownNodeIds.add(n.id));

    const allNodes = [...compareRF, ...ghostRF];
    const isTruncated = allNodes.length > MAX_RENDER_NODES;
    const filtered = isTruncated
      ? allNodes.filter((n) => statusMap.get(n.id) !== "unchanged")
      : allNodes;

    const renderNodeIds = new Set(filtered.map((n) => n.id));

    const compareEdgesRF = compareGraph.edges
      .filter((e) => renderNodeIds.has(e.source) && renderNodeIds.has(e.target))
      .map((e) => ({
        ...graphEdgeToEdge(e),
        data: { diffStatus: result.addedEdges.includes(e.id) ? "added" : "unchanged" },
      }));

    const removedEdgesRF = result.removedEdges
      .map((eid) => baseGraph.edges.find((e) => e.id === eid))
      .filter(
        (e): e is NonNullable<typeof e> =>
          e != null && renderNodeIds.has(e.source) && renderNodeIds.has(e.target),
      )
      .map((e) => ({ ...graphEdgeToEdge(e), data: { diffStatus: "removed" as DiffStatus } }));

    return {
      renderNodes: filtered as Node[],
      renderEdges: [...compareEdgesRF, ...removedEdgesRF] as Edge[],
      truncated: isTruncated,
    };
  }, [result, compareGraph, baseGraph, manifestsById, statusMap, rejectedNodeIds]);

  const selectedParams =
    result && selectedNodeId && result.changedParams[selectedNodeId]
      ? result.changedParams[selectedNodeId]
      : null;
  const selectedNodeLabel =
    (renderNodes.find((n) => n.id === selectedNodeId) as NoodleNode | undefined)?.data.label;

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <DiffContext.Provider value={statusMap}>
        <ReactFlow
          nodes={renderNodes}
          edges={renderEdges}
          nodeTypes={diffNodeTypes}
          edgeTypes={diffEdgeTypes}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={!readOnly}
          onNodeClick={(_, node) => {
            if (readOnly) return;
            const status = statusMap.get(node.id);
            // Ctrl+Click to toggle reject for added/modified/removed nodes
            if (onToggleReject && status && status !== "unchanged") {
              onToggleReject(node.id);
              return;
            }
            if (status === "changed") {
              setSelectedNodeId((prev) => (prev === node.id ? null : node.id));
            }
          }}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
          {result && (
            <Panel position="top-left">
              <DiffSummaryBar
                added={result.added}
                removed={result.removed}
                changed={result.changed}
                unchanged={result.unchanged}
              />
            </Panel>
          )}
        </ReactFlow>
      </DiffContext.Provider>
      {selectedParams && selectedNodeId && (
        <NodeParamDiffPanel
          nodeId={selectedNodeId}
          nodeLabel={selectedNodeLabel ?? undefined}
          params={selectedParams}
          onClose={() => setSelectedNodeId(null)}
        />
      )}
      {onClose && (
        <button
          type="button"
          className="ndv-close"
          onClick={onClose}
          style={{ position: "absolute", top: 8, right: 8, zIndex: 10 }}
          aria-label="Close diff view"
        >
          ×
        </button>
      )}
      {truncated && (
        <div className="diff-overlay__truncate-banner">
          Large workflow — showing only changed nodes.
        </div>
      )}
    </div>
  );
}

function buildVersionLabel(v: WorkflowVersionInfo): string {
  const parts = [`v${v.version}`];
  if (v.notes) parts.push(v.notes);
  if (v.published) parts.push("✓ published");
  return parts.join(" · ");
}

function WorkflowDiffViewInner({ workflowId, initialVersion, versions, onClose }: Props) {
  const idx = versions.findIndex((v) => v.id === initialVersion.id);
  const defaultBase = versions[idx + 1] ?? versions[0];

  const [compareId, setCompareId] = useState<string>(initialVersion.id);
  const [baseId, setBaseId] = useState<string>(defaultBase?.id ?? DRAFT_SENTINEL);
  const [compareFetch, setCompareFetch] = useState<GraphFetch>({ state: "idle", graph: null });
  const [baseFetch, setBaseFetch] = useState<GraphFetch>({ state: "idle", graph: null });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const manifestsById = useEditor((s) => s.manifestsById);
  const draftNodes = useEditor((s) => s.nodes);
  const draftEdges = useEditor((s) => s.edges);

  // Stable graph representation of the current editor draft.
  // useMemo ensures reference equality is preserved across re-renders so that
  // downstream memos (result, statusMap) don't recompute unless the draft changes.
  const draftGraph = useMemo((): WorkflowGraph => ({
    nodes: draftNodes.map((n: NoodleNode) => ({
      id: n.id,
      type: n.type ?? "noodle",
      params: n.data.params,
      position: n.position,
      disabled: n.data.disabled,
      outputs_override: n.data.outputsOverride,
      on_error: n.data.onError,
      retry_on_fail: n.data.retryOnFail,
      retries: n.data.retries,
      retry_wait_seconds: n.data.retryWaitSeconds,
      retry_backoff: n.data.retryBackoff,
      always_output_data: n.data.alwaysOutputData,
      timeout_seconds: n.data.timeoutSeconds,
      label: n.data.label,
    })) as WorkflowGraph["nodes"],
    edges: draftEdges.map((e: Edge) => ({
      id: e.id,
      source: e.source,
      source_output: e.sourceHandle ?? "main",
      target: e.target,
      target_input: e.targetHandle ?? "main",
    })) as WorkflowGraph["edges"],
  }), [draftNodes, draftEdges]);

  const fetchGraph = useCallback(
    (versionId: string, setter: (f: GraphFetch) => void) => {
      if (versionId === DRAFT_SENTINEL) return;
      const controller = new AbortController();
      setter({ state: "loading", graph: null });
      api
        .getVersionGraph(workflowId, versionId)
        .then(({ graph }) => {
          if (!controller.signal.aborted) setter({ state: "loaded", graph });
        })
        .catch(() => {
          if (!controller.signal.aborted) setter({ state: "error", graph: null });
        });
      return () => controller.abort();
    },
    [workflowId],
  );

  useEffect(() => {
    const cleanup = fetchGraph(compareId, setCompareFetch);
    return cleanup;
  }, [compareId, fetchGraph]);
  useEffect(() => {
    const cleanup = fetchGraph(baseId, setBaseFetch);
    return cleanup;
  }, [baseId, fetchGraph]);
  useEffect(() => {
    const cleanup = fetchGraph(compareId, setCompareFetch);
    return cleanup;
  }, [compareId, fetchGraph]);

  const compareGraph: WorkflowGraph | null =
    compareId === DRAFT_SENTINEL ? draftGraph : (compareFetch.state === "loaded" ? compareFetch.graph : null);
  const baseGraph: WorkflowGraph | null =
    baseId === DRAFT_SENTINEL ? draftGraph : (baseFetch.state === "loaded" ? baseFetch.graph : null);

  // Memoize the diff result — diffWorkflowGraphs is O(n) and must not run on
  // every render (e.g. when selectedNodeId changes or the user interacts with the panel).
  const result = useMemo(
    () => (compareGraph && baseGraph ? diffWorkflowGraphs(baseGraph, compareGraph) : null),
    [compareGraph, baseGraph],
  );

  // Stable Map so DiffNode's useContext reads the same reference between renders.
  const statusMap = useMemo(
    () =>
      result
        ? new Map<string, DiffStatus>([
            ...result.added.map((id) => [id, "added"] as const),
            ...result.changed.map((id) => [id, "changed"] as const),
            ...result.unchanged.map((id) => [id, "unchanged"] as const),
            ...result.removed.map((id) => [id, "removed"] as const),
          ])
        : new Map<string, DiffStatus>(),
    [result],
  );

  // Memoize the ReactFlow node/edge lists — converting GraphNode→NoodleNode via
  // graphNodeToNode is expensive and only changes when the diff result changes.
  const { renderNodes, renderEdges, truncated } = useMemo(() => {
    if (!result || !compareGraph || !baseGraph) {
      return { renderNodes: [] as Node[], renderEdges: [] as Edge[], truncated: false };
    }

    const compareRF = compareGraph.nodes
      .map((gn) => graphNodeToNode(gn, manifestsById))
      .filter((n): n is NoodleNode => n !== null);
    const ghostRF = result.removedNodes
      .map((gn) => graphNodeToNode({ ...gn, position: gn.position ?? { x: 0, y: 0 } }, manifestsById))
      .filter((n): n is NoodleNode => n !== null);

    const allNodes = [...compareRF, ...ghostRF];
    const isTruncated = allNodes.length > MAX_RENDER_NODES;
    const filtered = isTruncated
      ? allNodes.filter((n) => statusMap.get(n.id) !== "unchanged")
      : allNodes;

    const renderNodeIds = new Set(filtered.map((n) => n.id));

    const compareEdgesRF = compareGraph.edges
      .filter((e) => renderNodeIds.has(e.source) && renderNodeIds.has(e.target))
      .map((e) => ({
        ...graphEdgeToEdge(e),
        data: { diffStatus: result.addedEdges.includes(e.id) ? "added" : "unchanged" },
      }));

    const removedEdgesRF = result.removedEdges
      .map((eid) => baseGraph.edges.find((e) => e.id === eid))
      .filter(
        (e): e is NonNullable<typeof e> =>
          e != null && renderNodeIds.has(e.source) && renderNodeIds.has(e.target),
      )
      .map((e) => ({ ...graphEdgeToEdge(e), data: { diffStatus: "removed" as DiffStatus } }));

    return {
      renderNodes: filtered as Node[],
      renderEdges: [...compareEdgesRF, ...removedEdgesRF] as Edge[],
      truncated: isTruncated,
    };
  }, [result, compareGraph, baseGraph, manifestsById, statusMap]);

  const selectedParams =
    result && selectedNodeId && result.changedParams[selectedNodeId]
      ? result.changedParams[selectedNodeId]
      : null;

  const selectedNodeLabel =
    (renderNodes.find((n) => n.id === selectedNodeId) as NoodleNode | undefined)?.data.label;

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const loadingAny =
    (compareId !== DRAFT_SENTINEL && compareFetch.state === "loading") ||
    (baseId !== DRAFT_SENTINEL && baseFetch.state === "loading");

  return (
    <div className="diff-overlay">
      <div className="diff-overlay__header">
        <div className="diff-overlay__pickers">
          <div className="diff-picker">
            <label className="diff-picker__label">Base</label>
            <select
              className="diff-picker__select"
              value={baseId}
              onChange={(e) => {
                setBaseId(e.target.value);
                setBaseFetch({ state: "idle", graph: null });
              }}
            >
              <option value={DRAFT_SENTINEL}>Current draft (unsaved)</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id} disabled={v.id === compareId}>
                  {buildVersionLabel(v)}
                </option>
              ))}
            </select>
            {baseId !== DRAFT_SENTINEL && baseFetch.state === "error" && (
              <button
                className="btn btn-sm diff-picker__retry"
                onClick={() => { setBaseFetch({ state: "idle", graph: null }); fetchGraph(baseId, setBaseFetch); }}
              >
                Failed · Retry
              </button>
            )}
          </div>

          <span className="diff-overlay__arrow">→</span>

          <div className="diff-picker">
            <label className="diff-picker__label">Compare</label>
            <select
              className="diff-picker__select"
              value={compareId}
              onChange={(e) => {
                setCompareId(e.target.value);
                setCompareFetch({ state: "idle", graph: null });
              }}
            >
              <option value={DRAFT_SENTINEL}>Current draft (unsaved)</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id} disabled={v.id === baseId}>
                  {buildVersionLabel(v)}
                </option>
              ))}
            </select>
            {compareId !== DRAFT_SENTINEL && compareFetch.state === "error" && (
              <button
                className="btn btn-sm diff-picker__retry"
                onClick={() => { setCompareFetch({ state: "idle", graph: null }); fetchGraph(compareId, setCompareFetch); }}
              >
                Failed · Retry
              </button>
            )}
          </div>
        </div>

        <button className="ndv-close diff-overlay__close" onClick={onClose} aria-label="Close diff view">
          ×
        </button>
      </div>

      <div className="diff-overlay__body">
        <div className="diff-overlay__canvas-wrap" style={{ opacity: loadingAny ? 0.5 : 1 }}>
          {truncated && (
            <div className="diff-overlay__truncate-banner">
              Large workflow — showing only changed nodes.
            </div>
          )}

          <DiffContext.Provider value={statusMap}>
            <ReactFlow
              nodes={renderNodes}
              edges={renderEdges}
              nodeTypes={diffNodeTypes}
              edgeTypes={diffEdgeTypes}
              fitView
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={true}
              onNodeClick={(_, node) => {
                if (statusMap.get(node.id) === "changed") {
                  setSelectedNodeId((prev) => (prev === node.id ? null : node.id));
                }
              }}
            >
              <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
              {result && (
                <Panel position="top-left">
                  <DiffSummaryBar
                    added={result.added}
                    removed={result.removed}
                    changed={result.changed}
                    unchanged={result.unchanged}
                  />
                </Panel>
              )}
            </ReactFlow>
          </DiffContext.Provider>

          {!result && !loadingAny && (
            <div className="diff-overlay__empty">
              {(compareId !== DRAFT_SENTINEL && compareFetch.state === "error") ||
              (baseId !== DRAFT_SENTINEL && baseFetch.state === "error")
                ? "Failed to load one or both graphs."
                : "Loading…"}
            </div>
          )}
        </div>

        {selectedParams && selectedNodeId && (
          <NodeParamDiffPanel
            nodeId={selectedNodeId}
            nodeLabel={selectedNodeLabel ?? undefined}
            params={selectedParams}
            onClose={() => setSelectedNodeId(null)}
          />
        )}
      </div>
    </div>
  );
}

export function WorkflowDiffView(props: Props) {
  return (
    <div className="modal-overlay" onClick={props.onClose}>
      <div
        className="diff-overlay-container"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Workflow version diff"
      >
        <ReactFlowProvider>
          <WorkflowDiffViewInner {...props} />
        </ReactFlowProvider>
      </div>
    </div>
  );
}
