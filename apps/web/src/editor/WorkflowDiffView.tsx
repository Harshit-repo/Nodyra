import { useCallback, useEffect, useState } from "react";
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
  const [truncated, setTruncated] = useState(false);

  const manifestsById = useEditor((s) => s.manifestsById);
  const draftNodes = useEditor((s) => s.nodes);
  const draftEdges = useEditor((s) => s.edges);
  const draftGraph: WorkflowGraph = {
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
  };

  function resolveGraph(id: string, fetch: GraphFetch): WorkflowGraph | null {
    if (id === DRAFT_SENTINEL) return draftGraph;
    return fetch.state === "loaded" ? fetch.graph : null;
  }

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

  useEffect(() => fetchGraph(compareId, setCompareFetch), [compareId, fetchGraph]);
  useEffect(() => fetchGraph(baseId, setBaseFetch), [baseId, fetchGraph]);

  const compareGraph = resolveGraph(compareId, compareFetch);
  const baseGraph = resolveGraph(baseId, baseFetch);

  const result = compareGraph && baseGraph ? diffWorkflowGraphs(baseGraph, compareGraph) : null;

  const statusMap = result
    ? new Map<string, DiffStatus>([
        ...result.added.map((id) => [id, "added"] as const),
        ...result.changed.map((id) => [id, "changed"] as const),
        ...result.unchanged.map((id) => [id, "unchanged"] as const),
        ...result.removed.map((id) => [id, "removed"] as const),
      ])
    : new Map<string, DiffStatus>();

  let renderNodes: Node[] = [];
  let renderEdges: Edge[] = [];

  if (result && compareGraph && baseGraph) {
    const compareRF = compareGraph.nodes
      .map((gn) => graphNodeToNode(gn, manifestsById))
      .filter((n): n is NoodleNode => n !== null);
    const ghostRF = result.removedNodes
      .map((gn) => graphNodeToNode({ ...gn, position: gn.position ?? { x: 0, y: 0 } }, manifestsById))
      .filter((n): n is NoodleNode => n !== null);

    const allNodes = [...compareRF, ...ghostRF];

    let filtered = allNodes;
    if (allNodes.length > MAX_RENDER_NODES) {
      setTruncated(true);
      filtered = allNodes.filter((n) => statusMap.get(n.id) !== "unchanged");
    } else {
      setTruncated(false);
    }

    renderNodes = filtered;

    const renderNodeIds = new Set(filtered.map((n) => n.id));

    const compareEdgesRF = compareGraph.edges
      .filter((e) => renderNodeIds.has(e.source) && renderNodeIds.has(e.target))
      .map((e) => {
        const rf = graphEdgeToEdge(e);
        return { ...rf, data: { diffStatus: result.addedEdges.includes(e.id) ? "added" : "unchanged" } };
      });

    const removedEdgesRF = result.removedEdges
      .map((eid) => baseGraph.edges.find((e) => e.id === eid))
      .filter(
        (e): e is NonNullable<typeof e> =>
          e != null && renderNodeIds.has(e.source) && renderNodeIds.has(e.target),
      )
      .map((e) => ({ ...graphEdgeToEdge(e), data: { diffStatus: "removed" as DiffStatus } }));

    renderEdges = [...compareEdgesRF, ...removedEdgesRF];
  }

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
