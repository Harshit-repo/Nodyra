import {
  Background,
  BackgroundVariant,
  MiniMap,
  ReactFlow,
  useReactFlow,
  useStore,
} from "@xyflow/react";
import {
  ArrowClockwise,
  ArrowCounterClockwise,
  ArrowsOut,
  BoundingBox,
  CaretLeft,
  CaretRight,
  ClipboardText,
  Copy,
  Minus,
  Note,
  Play,
  Plus,
  Scissors,
  SquaresFour,
  TreeStructure,
  X,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import type { Connection, Edge } from "@xyflow/react";
import { useShallow } from "zustand/react/shallow";

import { categoryColor } from "../categories";
import { NodeIcon } from "../NodeIcon";
import type { NodeManifest } from "../types";
import { CANVAS_STARTERS } from "../workflowTemplates";
import { useToast } from "../ToastProvider";
import { LOOP_FRAME_ID_PREFIX, computeLoopFrames } from "./loopFrames";
import { MetanodeBreadcrumb } from "./MetanodeBreadcrumb";
import {
  MiniMapNoodleNode,
  miniMapNodeClassName,
  miniMapNodeColor,
} from "./MiniMapNoodleNode";
import { nodeTypes, edgeTypes } from "./nodeTypes";
import { PortLegend } from "./PortLegend";
import { datasetConnectionIssues, validateConnection, type ConnectionCheck } from "./connectionValidation";
import { OnboardingTour } from "./OnboardingTour";
import { pickEditorRunTrigger, useEditor, type NoodleNode } from "./store";

const defaultEdgeOptions = { type: "default" };
const CANVAS_QUICK_ADD_LIMIT = 8;
const CANVAS_QUICK_ADD_WIDTH = 252;
const CANVAS_QUICK_ADD_MAX_HEIGHT = 300;

interface CanvasQuickAddState {
  x: number;
  y: number;
  flowX: number;
  flowY: number;
  query: string;
  insertEdgeId?: string;
}

function rankCanvasQuickNode(node: NodeManifest, query: string): number {
  if (!query) return 1000;
  const q = query.toLowerCase();
  const id = node.id.toLowerCase();
  const name = node.name.toLowerCase();
  const category = node.category.toLowerCase();
  const description = (node.description ?? "").toLowerCase();
  if (id === q) return 0;
  if (name === q) return 1;
  if (id.startsWith(q)) return 10;
  if (name.startsWith(q)) return 11;
  if (category.startsWith(q)) return 20;
  if (name.includes(q)) return 30;
  if (id.includes(q)) return 31;
  if (category.includes(q)) return 40;
  if (description.includes(q)) return 50;
  return 1000;
}

function clampCanvasQuickAddPosition(x: number, y: number): { x: number; y: number } {
  if (typeof window === "undefined") return { x, y };
  return {
    x: Math.max(12, Math.min(window.innerWidth - CANVAS_QUICK_ADD_WIDTH - 12, x)),
    y: Math.max(12, Math.min(window.innerHeight - CANVAS_QUICK_ADD_MAX_HEIGHT - 12, y)),
  };
}

function edgeBridgeHandles(
  nodes: NoodleNode[],
  edge: Edge | undefined,
  manifest: NodeManifest,
): { input: string; output: string } | null {
  if (!edge || manifest.inputs.length === 0 || manifest.outputs.length === 0) return null;
  const input = manifest.inputs[0]?.name;
  const output = manifest.outputs[0]?.name;
  if (!input || !output) return null;
  const candidateId = "__edge_insert_candidate__";
  const candidate = {
    id: candidateId,
    type: manifest.id === "map_group" ? "mapGroup" : "noodle",
    position: { x: 0, y: 0 },
    data: {
      manifest,
      params: {},
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
  const candidateNodes = [...nodes, candidate];
  const sourceCheck = validateConnection(candidateNodes, {
    source: edge.source,
    sourceHandle: edge.sourceHandle ?? null,
    target: candidateId,
    targetHandle: input,
  });
  if (!sourceCheck.ok) return null;
  const targetCheck = validateConnection(candidateNodes, {
    source: candidateId,
    sourceHandle: output,
    target: edge.target,
    targetHandle: edge.targetHandle ?? null,
  });
  return targetCheck.ok ? { input, output } : null;
}

function quickFixLabel(quickFixId: ConnectionCheck["quickFixId"]): string {
  switch (quickFixId) {
    case "records_to_dataset":
      return "Add Records To Dataset";
    case "dataset_to_records":
      return "Add Dataset To Records";
    case "duckdb_sql":
      return "Add DuckDB SQL";
    default:
      return "Add helper node";
  }
}

function DatasetConnectionBanner({
  connection,
  check,
  onDismiss,
}: {
  connection: Connection;
  check: ConnectionCheck;
  onDismiss: () => void;
}) {
  const insertQuickFixNode = useEditor((s) => s.insertQuickFixNode);
  const { notify } = useToast();

  useEffect(() => {
    const timer = window.setTimeout(onDismiss, 7000);
    return () => window.clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div className={`connection-banner connection-banner-${check.severity}`}>
      <div>
        <strong>Wire not added</strong>
        <span>{check.message}</span>
      </div>
      <div className="connection-banner-actions">
        {check.quickFixId && (
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={() => {
              const result = insertQuickFixNode(check.quickFixId!, connection);
              if (result.ok) {
                notify(`${quickFixLabel(check.quickFixId)} inserted between those nodes.`, "success");
                onDismiss();
              } else {
                notify("Could not insert the helper node for this wire.", "error");
              }
            }}
          >
            {quickFixLabel(check.quickFixId)}
          </button>
        )}
        <button type="button" className="btn btn-sm btn-ghost" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}

function DatasetConnectionHealth() {
  // Position-only updates preserve these values, so dragging does not rerun
  // validation. Structural or node-data changes still invalidate the result.
  const nodeValidationInputs = useEditor(
    useShallow((s) =>
      s.nodes.flatMap((node) => [node.id, node.parentId ?? "", node.data]),
    ),
  );
  const edges = useEditor((s) => s.edges);
  const issues = useMemo(
    () => datasetConnectionIssues(useEditor.getState().nodes, edges),
    [nodeValidationInputs, edges],
  );
  if (issues.length === 0) return null;
  return (
    <div className="connection-health" role="status">
      <strong>{issues.length} incompatible wire{issues.length === 1 ? "" : "s"}</strong>
      <span>{issues[0].check.message}</span>
    </div>
  );
}

function CanvasControls({
  snapToGrid,
  onToggleSnap,
}: {
  snapToGrid: boolean;
  onToggleSnap: () => void;
}) {
  const { fitView, zoomIn, zoomOut } = useReactFlow();
  const zoom = useStore((s) => s.transform[2]);
  const { notify } = useToast();
  const autoLayout = useEditor((s) => s.autoLayout);
  const addStickyNote = useEditor((s) => s.addStickyNote);
  const showLoopFrames = useEditor((s) => s.showLoopFrames);
  const toggleLoopFrames = useEditor((s) => s.toggleLoopFrames);
  const collapseToMetanode = useEditor((s) => s.collapseToMetanode);
  const [hasLoop, hasTrigger, selectedCount, selectedMetanodeCandidateCount] =
    useEditor(
      useShallow((s) => {
        let loop = false;
        let selected = 0;
        let metanodeCandidates = 0;
        for (const node of s.nodes) {
          if (node.data?.manifest?.id === "loop_start") loop = true;
          if (!node.selected) continue;
          selected += 1;
          if (node.data?.manifest && node.data.manifest.id !== "meta_node") {
            metanodeCandidates += 1;
          }
        }
        const runNodes = s.drillStack.length > 0 ? s.drillStack[0]!.nodes : s.nodes;
        return [
          loop,
          pickEditorRunTrigger(runNodes) !== null,
          selected || (s.selectedId ? 1 : 0),
          metanodeCandidates,
        ] as const;
      }),
    );
  const nodeCount = useEditor((s) => s.nodes.length);
  const running = useEditor((s) => s.running);
  const runHandler = useEditor((s) => s.runHandler);
  const undo = useEditor((s) => s.undo);
  const redo = useEditor((s) => s.redo);
  const copySelection = useEditor((s) => s.copySelection);
  const cutSelection = useEditor((s) => s.cutSelection);
  const pasteSelection = useEditor((s) => s.pasteSelection);
  const canUndo = useEditor((s) => s._past.length > 0);
  const canRedo = useEditor((s) => s._future.length > 0);
  const clipboardNodeCount = useEditor((s) => s.clipboardNodeCount);
  const [expanded, setExpanded] = useState(true);

  function copiedLabel(count: number): string {
    return `${count} node${count === 1 ? "" : "s"} copied.`;
  }

  function pastedLabel(count: number): string {
    return `${count} node${count === 1 ? "" : "s"} pasted.`;
  }

  return (
    <div
      className={`canvas-controls${expanded ? "" : " canvas-controls--collapsed"}`}
      aria-label="Canvas controls"
    >
      {/* Toggle collapse button — always visible */}
      <button
        type="button"
        title={expanded ? "Collapse toolbar" : "Expand toolbar"}
        aria-label={expanded ? "Collapse toolbar" : "Expand toolbar"}
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <CaretRight size={12} weight="bold" /> : <CaretLeft size={12} weight="bold" />}
      </button>

      {/* Always-visible: fit view + auto layout */}
      <span className="canvas-control-sep" />
      <button
        type="button"
        title="Fit view"
        aria-label="Fit view"
        onClick={() => void fitView({ padding: 0.22, duration: 220 })}
      >
        <ArrowsOut size={14} weight="bold" />
      </button>
      <button
        type="button"
        title={nodeCount < 2 ? "Auto-layout requires at least 2 nodes" : "Auto layout (Shift+L)"}
        aria-label="Auto layout"
        disabled={nodeCount < 2}
        onClick={() => {
          autoLayout();
          window.setTimeout(() => void fitView({ padding: 0.24, duration: 220 }), 30);
        }}
      >
        <TreeStructure size={14} weight="bold" />
      </button>

      {/* Collapsible group — single wrapper so gap only fires once */}
      <div className="canvas-control-group">
        <span className="canvas-control-sep" />
        <button type="button" title="Zoom out" aria-label="Zoom out" onClick={() => void zoomOut({ duration: 160 })}>
          <Minus size={14} weight="bold" />
        </button>
        <span className="canvas-zoom-label" title={`Zoom: ${Math.round(zoom * 100)}%`}>{Math.round(zoom * 100)}%</span>
        <button type="button" title="Zoom in" aria-label="Zoom in" onClick={() => void zoomIn({ duration: 160 })}>
          <Plus size={14} weight="bold" />
        </button>
        <span className="canvas-control-sep" />
        <button
          type="button"
          title="Copy selected nodes (Ctrl+C)"
          aria-label="Copy selected nodes"
          disabled={selectedCount === 0}
          onClick={() => {
            const result = copySelection();
            if (result.nodeCount > 0) notify(copiedLabel(result.nodeCount), "success");
          }}
        >
          <Copy size={14} weight="bold" />
        </button>
        <button
          type="button"
          title={selectedCount === 0 ? "Select nodes to cut" : "Cut selected nodes (Ctrl+X)"}
          aria-label="Cut selected nodes"
          disabled={selectedCount === 0}
          onClick={() => {
            const result = cutSelection();
            if (result.nodeCount > 0) notify(`${result.nodeCount} node${result.nodeCount === 1 ? "" : "s"} cut.`, "success");
          }}
        >
          <Scissors size={14} weight="bold" />
        </button>
        <button
          type="button"
          title="Paste copied nodes (Ctrl+V)"
          aria-label="Paste copied nodes"
          disabled={clipboardNodeCount === 0}
          onClick={() => {
            const result = pasteSelection();
            if (result.nodeCount > 0) notify(pastedLabel(result.nodeCount), "success");
          }}
        >
          <ClipboardText size={14} weight="bold" />
        </button>
        <span className="canvas-control-sep" />
        <button type="button" title={canUndo ? "Undo (Ctrl+Z)" : "No undo history"} aria-label="Undo" disabled={!canUndo} onClick={() => undo()}>
          <ArrowCounterClockwise size={14} weight="bold" />
        </button>
        <button type="button" title={canRedo ? "Redo (Ctrl+Shift+Z)" : "No redo history"} aria-label="Redo" disabled={!canRedo} onClick={() => redo()}>
          <ArrowClockwise size={14} weight="bold" />
        </button>
        <button
          type="button"
          title="Add sticky note (Shift+N)"
          aria-label="Add sticky note"
          onClick={() => addStickyNote({ x: 200 + Math.random() * 200, y: 200 + Math.random() * 100 })}
        >
          <Note size={14} weight="bold" />
        </button>
        <button
          type="button"
          title={
            selectedMetanodeCandidateCount >= 2
              ? `Group ${selectedMetanodeCandidateCount} selected nodes into metanode`
              : "Select at least 2 nodes to create a metanode"
          }
          aria-label="Group selected nodes into metanode"
          disabled={selectedMetanodeCandidateCount < 2}
          onClick={() => {
            const selectedMetanodeCandidates = useEditor
              .getState()
              .nodes.filter(
                (node) =>
                  node.selected &&
                  node.data?.manifest &&
                  node.data.manifest.id !== "meta_node",
              )
              .map((node) => node.id);
            const id = collapseToMetanode(selectedMetanodeCandidates);
            if (id) notify(`Grouped ${selectedMetanodeCandidates.length} nodes into a metanode.`, "success");
            else notify("Can't group: that selection would create a cycle.", "error");
          }}
        >
          <BoundingBox size={14} weight="bold" />
        </button>
        {hasLoop && (
          <button
            type="button"
            className={showLoopFrames ? "is-active" : undefined}
            title={showLoopFrames ? "Hide loop frames" : "Show loop frames"}
            aria-label="Toggle loop frames"
            aria-pressed={showLoopFrames}
            onClick={() => toggleLoopFrames()}
          >
            <BoundingBox size={14} weight="bold" />
          </button>
        )}
        <button
          type="button"
          className={snapToGrid ? "is-active" : undefined}
          title={snapToGrid ? "Disable snap to grid" : "Enable snap to grid (20×20)"}
          aria-label="Toggle snap to grid"
          aria-pressed={snapToGrid}
          onClick={onToggleSnap}
        >
          <SquaresFour size={14} weight="bold" />
        </button>
      </div>

      {/* Run — always visible on the right */}
      <button
        type="button"
        className="is-primary"
        data-tour-id="run-button"
        title={hasTrigger ? "Run workflow" : "Add a trigger node to run"}
        aria-label="Run workflow"
        onClick={() => void runHandler?.()}
        disabled={running || !runHandler || !hasTrigger}
      >
        <Play size={14} weight="fill" />
      </button>
    </div>
  );
}

export function Canvas() {
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const manifests = useEditor((s) => s.manifests);
  const onNodesChange = useEditor((s) => s.onNodesChange);
  const showLoopFrames = useEditor((s) => s.showLoopFrames);
  const onEdgesChange = useEditor((s) => s.onEdgesChange);
  const onConnect = useEditor((s) => s.onConnect);
  const addNode = useEditor((s) => s.addNode);
  const insertNodeBetweenEdge = useEditor((s) => s.insertNodeBetweenEdge);
  const addBodyNode = useEditor((s) => s.addBodyNode);
  const childWorkflows = useEditor((s) => s.childWorkflows);
  const loadGraph = useEditor((s) => s.loadGraph);
  const setSelected = useEditor((s) => s.setSelected);
  const openNdv = useEditor((s) => s.openNdv);
  const collapseToMetanode = useEditor((s) => s.collapseToMetanode);
  const ungroupMetanode = useEditor((s) => s.ungroupMetanode);
  const toggleDisabled = useEditor((s) => s.toggleDisabled);
  const deleteNode = useEditor((s) => s.deleteNode);
  const copySelection = useEditor((s) => s.copySelection);
  const cutSelection = useEditor((s) => s.cutSelection);
  const pasteSelection = useEditor((s) => s.pasteSelection);
  const autoLayout = useEditor((s) => s.autoLayout);
  const autoEnableAgentDependencies = useEditor((s) => s.autoEnableAgentDependencies);
  const { fitView, screenToFlowPosition, getNodes, getIntersectingNodes } = useReactFlow();
  const { notify } = useToast();
  const [blockedConnection, setBlockedConnection] = useState<{
    connection: Connection;
    check: ConnectionCheck;
  } | null>(null);

  interface CtxMenu { x: number; y: number; nodeId?: string }
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);
  const enterMetanode = useEditor((s) => s.enterMetanode);
  const drillDepth = useEditor((s) => s.drillStack.length);
  const [paletteCollapsed, setPaletteCollapsed] = useState(() => {
    try { return localStorage.getItem("noodle_palette_collapsed") === "true"; } catch { return false; }
  });
  const [snapToGrid, setSnapToGrid] = useState(() => {
    try { return localStorage.getItem("noodle_snap_to_grid") !== "false"; } catch { return true; }
  });
  const [quickAdd, setQuickAdd] = useState<CanvasQuickAddState | null>(null);
  const [quickAddActiveIndex, setQuickAddActiveIndex] = useState(0);
  const [ctxActiveIndex, setCtxActiveIndex] = useState(0);
  const ctxMenuRef = useRef<HTMLDivElement | null>(null);
  const quickAddInputRef = useRef<HTMLInputElement | null>(null);

  // Left-drag on empty canvas pans (middle-drag also pans, dragging a node moves
  // it). Right-drag draws a selection box to select multiple nodes. Holding Space
  // switches to selection mode: left-drag then box-selects and nodes ignore the
  // pointer so the rect can be drawn over a dense cluster too.
  const [spaceDown, setSpaceDown] = useState(false);
  const rightDragRef = useRef<{ startX: number; startY: number; isDragging: boolean } | null>(null);
  const rightDragAbortRef = useRef<AbortController | null>(null);
  const [selectionRect, setSelectionRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const suppressCtxMenuRef = useRef(false);

  // Abort any in-progress right-drag on unmount to remove orphaned window listeners.
  useEffect(() => () => { rightDragAbortRef.current?.abort(); }, []);

  useEffect(() => {
    // Don't hijack Space from text entry or any control it would activate, so
    // Space-to-click on buttons keeps working when the canvas isn't focused.
    const ownsKey = (el: EventTarget | null): boolean => {
      const node = el as HTMLElement | null;
      if (!node) return false;
      const tag = node.tagName;
      return (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        tag === "BUTTON" ||
        tag === "A" ||
        node.isContentEditable ||
        node.getAttribute?.("role") === "button"
      );
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && !ownsKey(e.target)) {
        e.preventDefault();
        setSpaceDown(true);
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space") setSpaceDown(false);
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, []);

  const onDrop = useCallback(
    (event: DragEvent) => {
      event.preventDefault();
      const manifestId = event.dataTransfer.getData("application/noodle");
      if (!manifestId) return;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });

      // Detect if the drop lands inside a Map Group container.
      const flowNodes = getNodes();
      const mapGroup = flowNodes.find((n) => {
        if (n.type !== "mapGroup") return false;
        const w = ((n.measured?.width ?? (n.style?.width as number)) ?? 380) as number;
        const h = ((n.measured?.height ?? (n.style?.height as number)) ?? 280) as number;
        return (
          position.x >= n.position.x &&
          position.x <= n.position.x + w &&
          position.y >= n.position.y &&
          position.y <= n.position.y + h
        );
      });

      if (mapGroup) {
        const relPos = {
          x: position.x - mapGroup.position.x,
          y: position.y - mapGroup.position.y,
        };
        addBodyNode(mapGroup.id, manifestId, relPos);
      } else {
        addNode(manifestId, position);
      }
    },
    [screenToFlowPosition, addNode, addBodyNode, getNodes],
  );

  const onDragOver = useCallback((event: DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const visibleManifests = useMemo(
    () => manifests.filter((manifest) => !manifest.hidden),
    [manifests],
  );

  const quickAddResults = useMemo(() => {
    const query = quickAdd?.query.trim().toLowerCase() ?? "";
    const insertEdge = quickAdd?.insertEdgeId
      ? edges.find((edge) => edge.id === quickAdd.insertEdgeId)
      : undefined;
    const bridgeableManifests = insertEdge
      ? visibleManifests.filter((manifest) => edgeBridgeHandles(nodes, insertEdge, manifest))
      : visibleManifests;
    if (query) {
      return bridgeableManifests
        .filter((manifest) => rankCanvasQuickNode(manifest, query) < 1000)
        .sort((a, b) => {
          const rank = rankCanvasQuickNode(a, query) - rankCanvasQuickNode(b, query);
          return rank !== 0 ? rank : a.name.localeCompare(b.name);
        })
        .slice(0, CANVAS_QUICK_ADD_LIMIT);
    }
    const preferred = ["http_request", "code", "filter", "switch", "records_to_dataset", "duckdb_sql", "google_sheets", "slack"];
    const byId = new Map(bridgeableManifests.map((manifest) => [manifest.id, manifest]));
    const seen = new Set<string>();
    return [
      ...preferred.map((id) => byId.get(id)).filter((node): node is NodeManifest => Boolean(node)),
      ...bridgeableManifests,
    ]
      .filter((node) => {
        if (seen.has(node.id)) return false;
        seen.add(node.id);
        return true;
      })
      .slice(0, CANVAS_QUICK_ADD_LIMIT);
  }, [edges, nodes, quickAdd?.insertEdgeId, quickAdd?.query, visibleManifests]);

  const openQuickAddAt = useCallback((clientX: number, clientY: number) => {
    const clamped = clampCanvasQuickAddPosition(clientX, clientY);
    const position = screenToFlowPosition({ x: clientX, y: clientY });
    setQuickAdd({
      x: clamped.x,
      y: clamped.y,
      flowX: position.x,
      flowY: position.y,
      query: "",
    });
    setQuickAddActiveIndex(0);
    setCtxMenu(null);
  }, [screenToFlowPosition]);

  const openEdgeQuickAddAt = useCallback((edgeId: string, clientX: number, clientY: number) => {
    const clamped = clampCanvasQuickAddPosition(clientX, clientY);
    const position = screenToFlowPosition({ x: clientX, y: clientY });
    setQuickAdd({
      x: clamped.x,
      y: clamped.y,
      flowX: position.x,
      flowY: position.y,
      query: "",
      insertEdgeId: edgeId,
    });
    setQuickAddActiveIndex(0);
    setCtxMenu(null);
  }, [screenToFlowPosition]);

  const addQuickNode = useCallback((manifest: NodeManifest, index = 0) => {
    if (!quickAdd) return;
    const offset = (index % 3) * 28;
    if (quickAdd.insertEdgeId) {
      const result = insertNodeBetweenEdge(quickAdd.insertEdgeId, manifest.id, {
        x: quickAdd.flowX + offset,
        y: quickAdd.flowY + offset,
      });
      if (result.ok) {
        notify(`${manifest.name} inserted into the connection.`, "success");
        setQuickAdd(null);
      } else {
        notify(result.message ?? "That node cannot be inserted into this connection.", "error");
      }
      return;
    }
    addNode(manifest.id, {
      x: quickAdd.flowX + offset,
      y: quickAdd.flowY + offset,
    });
    setQuickAdd(null);
  }, [addNode, insertNodeBetweenEdge, notify, quickAdd]);

  useEffect(() => {
    function onFitView(): void {
      void fitView({ padding: 0.22, duration: 220 });
    }
    window.addEventListener("noodle:fit-view", onFitView);
    return () => window.removeEventListener("noodle:fit-view", onFitView);
  }, [fitView]);

  useEffect(() => {
    function onOpenEdgeQuickAdd(event: Event): void {
      const detail = (event as CustomEvent<{
        edgeId?: string;
        clientX?: number;
        clientY?: number;
      }>).detail;
      if (!detail?.edgeId || typeof detail.clientX !== "number" || typeof detail.clientY !== "number") {
        return;
      }
      openEdgeQuickAddAt(detail.edgeId, detail.clientX, detail.clientY);
    }
    window.addEventListener("noodle:open-edge-quick-add", onOpenEdgeQuickAdd);
    return () => window.removeEventListener("noodle:open-edge-quick-add", onOpenEdgeQuickAdd);
  }, [openEdgeQuickAddAt]);

  useEffect(() => {
    if (!quickAdd) return;
    setQuickAddActiveIndex(0);
    window.setTimeout(() => quickAddInputRef.current?.focus(), 0);
  }, [quickAdd?.x, quickAdd?.y]);

  useEffect(() => {
    setQuickAddActiveIndex((index) => Math.min(index, Math.max(0, quickAddResults.length - 1)));
  }, [quickAddResults.length]);

  useEffect(() => {
    function onAutoLayout(): void {
      autoLayout();
      setTimeout(() => { void fitView({ padding: 0.22, duration: 220 }); }, 30);
    }
    window.addEventListener("noodle:auto-layout", onAutoLayout);
    return () => window.removeEventListener("noodle:auto-layout", onAutoLayout);
  }, [autoLayout, fitView]);

  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      if (e.key === "Escape" && useEditor.getState().drillStack.length > 0) {
        e.preventDefault();
        useEditor.getState().exitMetanode();
        return;
      }
      const meta = e.ctrlKey || e.metaKey;
      if (!meta) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) {
        e.preventDefault();
        useEditor.getState().undo();
      } else if ((key === "z" && e.shiftKey) || key === "y") {
        e.preventDefault();
        useEditor.getState().redo();
      } else if (key === "c" && !e.shiftKey && !e.altKey) {
        const result = useEditor.getState().copySelection();
        if (result.nodeCount > 0) {
          e.preventDefault();
          notify(`${result.nodeCount} node${result.nodeCount === 1 ? "" : "s"} copied.`, "success");
        }
      } else if (key === "x" && !e.shiftKey && !e.altKey) {
        const result = useEditor.getState().cutSelection();
        if (result.nodeCount > 0) {
          e.preventDefault();
          notify(`${result.nodeCount} node${result.nodeCount === 1 ? "" : "s"} cut.`, "success");
        }
      } else if (key === "v" && !e.shiftKey && !e.altKey) {
        const result = useEditor.getState().pasteSelection();
        if (result.nodeCount > 0) {
          e.preventDefault();
          notify(`${result.nodeCount} node${result.nodeCount === 1 ? "" : "s"} pasted.`, "success");
        }
      } else if (key === "a" && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        useEditor.getState().selectAll();
      } else if (key === "d" && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        const count = useEditor.getState().duplicateSelection();
        if (count > 0) {
          notify(`Duplicated ${count} node${count === 1 ? "" : "s"}.`, "success");
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [notify]);

  useEffect(() => {
    if (!ctxMenu) return;
    setCtxActiveIndex(0);
    window.setTimeout(() => {
      ctxMenuRef.current?.querySelector<HTMLButtonElement>("[role='menuitem']")?.focus();
    }, 0);
  }, [ctxMenu]);

  useEffect(() => {
    autoEnableAgentDependencies();
  }, [autoEnableAgentDependencies, nodes, edges]);

  useEffect(() => {
    function onToggle(): void {
      try { setPaletteCollapsed(localStorage.getItem("noodle_palette_collapsed") === "true"); } catch {}
    }
    window.addEventListener("noodle:toggle-node-palette", onToggle);
    return () => window.removeEventListener("noodle:toggle-node-palette", onToggle);
  }, []);

  const handleCanvasMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 2) return;
    // Only initiate right-drag selection on the bare pane, not on nodes/controls.
    const target = e.target as HTMLElement;
    if (!target.classList.contains("react-flow__pane") && !target.classList.contains("react-flow__background")) return;

    const startX = e.clientX;
    const startY = e.clientY;
    rightDragRef.current = { startX, startY, isDragging: false };

    const onMove = (mv: MouseEvent) => {
      if (!rightDragRef.current) return;
      const dx = mv.clientX - startX;
      const dy = mv.clientY - startY;
      if (!rightDragRef.current.isDragging && (Math.abs(dx) > 5 || Math.abs(dy) > 5)) {
        rightDragRef.current.isDragging = true;
      }
      if (rightDragRef.current.isDragging) {
        setSelectionRect({
          x: Math.min(mv.clientX, startX),
          y: Math.min(mv.clientY, startY),
          w: Math.abs(dx),
          h: Math.abs(dy),
        });
      }
    };

    const onUp = (up: MouseEvent) => {
      rightDragAbortRef.current?.abort();
      rightDragAbortRef.current = null;

      if (rightDragRef.current?.isDragging) {
        const endX = up.clientX;
        const endY = up.clientY;
        const flowTL = screenToFlowPosition({ x: Math.min(endX, startX), y: Math.min(endY, startY) });
        const flowBR = screenToFlowPosition({ x: Math.max(endX, startX), y: Math.max(endY, startY) });
        const intersecting = getIntersectingNodes({
          x: flowTL.x,
          y: flowTL.y,
          width: flowBR.x - flowTL.x,
          height: flowBR.y - flowTL.y,
        });
        const hitIds = new Set(intersecting.map((n) => n.id));
        const changes = getNodes()
          .filter((n) => !n.id.startsWith(LOOP_FRAME_ID_PREFIX) && n.id !== "__meta_input_bar__" && n.id !== "__meta_output_bar__")
          .map((n) => ({ type: "select" as const, id: n.id, selected: hitIds.has(n.id) }));
        onNodesChange(changes);
        suppressCtxMenuRef.current = true;
      }

      setSelectionRect(null);
      rightDragRef.current = null;
    };

    const controller = new AbortController();
    rightDragAbortRef.current = controller;
    window.addEventListener("mousemove", onMove, { signal: controller.signal });
    window.addEventListener("mouseup", onUp, { signal: controller.signal });
  }, [screenToFlowPosition, getNodes, getIntersectingNodes, onNodesChange]);

  const onNodeContextMenu = useCallback((e: React.MouseEvent, node: { id: string }) => {
    e.preventDefault();
    setSelected(node.id);
    setCtxMenu({ x: e.clientX, y: e.clientY, nodeId: node.id });
  }, [setSelected]);

  const onPaneContextMenu = useCallback((e: React.MouseEvent | MouseEvent) => {
    e.preventDefault();
    if (suppressCtxMenuRef.current) {
      suppressCtxMenuRef.current = false;
      return;
    }
    setCtxMenu({ x: (e as React.MouseEvent).clientX, y: (e as React.MouseEvent).clientY });
  }, []);

  const handleConnect = useCallback((connection: Connection) => {
    const check = onConnect(connection);
    if (check.ok) {
      setBlockedConnection(null);
      if (check.message.includes("DatasetRef")) notify(check.message, "info");
      return;
    }
    setBlockedConnection({ connection, check });
    notify(check.message, "error");
  }, [notify, onConnect]);

  const isValidConnection = useCallback((connection: Connection | Edge) => {
    const state = useEditor.getState();
    const allN = [
      ...state.nodes,
      ...Object.values(state.childWorkflows).flatMap((cw) => cw.nodes),
    ];
    return validateConnection(allN, {
      source: connection.source,
      sourceHandle: connection.sourceHandle ?? null,
      target: connection.target,
      targetHandle: connection.targetHandle ?? null,
    }).ok;
  }, []);

  function applyStarter(templateId: string): void {
    const template = CANVAS_STARTERS.find((item) => item.id === templateId);
    if (!template?.graph) return;
    loadGraph(template.graph(), { dirty: true });
    window.setTimeout(() => void fitView({ padding: 0.24, duration: 220 }), 30);
  }

  const labeledEdges = useMemo(
    () =>
      edges.map((e) =>
        e.sourceHandle && e.sourceHandle !== "main" && e.sourceHandle !== "output"
          ? { ...e, label: e.sourceHandle }
          : e,
      ),
    [edges],
  );

  // Auto-frames behind each loop's body. Derived from the graph (never
  // persisted) and rendered first so they sit behind the real nodes.
  const loopFrames = useMemo(
    () => (showLoopFrames ? computeLoopFrames(nodes, edges) : []),
    [showLoopFrames, nodes, edges],
  );

  // Merge frames + body nodes/edges from child workflows into the render list.
  const allNodes = useMemo(
    () => [
      // Frames are render-only RF nodes; cast keeps allNodes a NoodleNode[].
      ...(loopFrames as unknown as NoodleNode[]),
      ...nodes,
      ...Object.values(childWorkflows).flatMap((cw) => cw.nodes),
    ],
    [loopFrames, nodes, childWorkflows],
  );
  // Derived loop frames are render-only; drop any change RF emits for them so
  // they never reach the persisted store.
  const handleNodesChange = useCallback(
    (changes: Parameters<typeof onNodesChange>[0]) =>
      onNodesChange(
        changes.filter((c) => {
          if (!("id" in c) || typeof c.id !== "string") return true;
          if (c.id.startsWith(LOOP_FRAME_ID_PREFIX)) return false;
          if (c.id === "__meta_input_bar__" || c.id === "__meta_output_bar__") {
            // Allow drag (position) so the user can slide a pillar aside; never
            // let React Flow remove a boundary bar.
            return c.type !== "remove";
          }
          return true;
        }),
      ),
    [onNodesChange],
  );

  const toggleSnapToGrid = useCallback(() => {
    setSnapToGrid((prev) => {
      const next = !prev;
      try { localStorage.setItem("noodle_snap_to_grid", String(next)); } catch {}
      return next;
    });
  }, []);

  const allEdges = useMemo(
    () => [
      ...labeledEdges,
      ...Object.values(childWorkflows).flatMap((cw) =>
        cw.edges.map((e) =>
          e.sourceHandle && e.sourceHandle !== "main" && e.sourceHandle !== "output"
            ? { ...e, label: e.sourceHandle }
            : e,
        ),
      ),
    ],
    [labeledEdges, childWorkflows],
  );

  const canvasSelection = useMemo(() => {
    let count = 0;
    const metanodeIds: string[] = [];
    for (const node of nodes) {
      if (!node.selected) continue;
      count += 1;
      if (node.data?.manifest && node.data.manifest.id !== "meta_node") {
        metanodeIds.push(node.id);
      }
    }
    return { count, metanodeIds };
  }, [nodes]);
  const selectedMetanodeIds = canvasSelection.metanodeIds;

  const contextMenuItems = useMemo(() => ctxMenu
    ? ctxMenu.nodeId
      ? [
          {
            label: "Open",
            action: () => {
              openNdv(ctxMenu.nodeId!);
              setCtxMenu(null);
            },
          },
          ...(() => {
            const ctxNode = nodes.find((n) => n.id === ctxMenu.nodeId);
            const isMeta = ctxNode?.data?.manifest?.id === "meta_node";
            const groupIds =
              ctxNode?.selected && selectedMetanodeIds.length >= 2
                ? selectedMetanodeIds
                : ctxMenu.nodeId
                  ? [ctxMenu.nodeId]
                  : [];
            const items: { label: string; action: () => void; danger?: boolean }[] = [];
            if (isMeta) {
              items.push({
                label: "Ungroup metanode",
                action: () => {
                  ungroupMetanode(ctxMenu.nodeId!);
                  setCtxMenu(null);
                },
              });
            } else if (groupIds.length >= 2) {
              items.push({
                label: `Group ${groupIds.length} nodes into metanode`,
                action: () => {
                  const id = collapseToMetanode(groupIds);
                  if (id) notify(`Grouped ${groupIds.length} nodes into a metanode.`, "success");
                  else notify("Can't group: that selection would create a cycle.", "error");
                  setCtxMenu(null);
                },
              });
            }
            return items;
          })(),
          {
            label: "Duplicate",
            action: () => {
              const r = copySelection();
              if (r.nodeCount) {
                const p = pasteSelection();
                if (p.nodeCount) {
                  notify(`Duplicated ${p.nodeCount} node${p.nodeCount === 1 ? "" : "s"}.`, "success");
                }
              }
              setCtxMenu(null);
            },
          },
          {
            label: nodes.find((n) => n.id === ctxMenu.nodeId)?.data.disabled ? "Enable" : "Disable",
            action: () => {
              toggleDisabled(ctxMenu.nodeId!);
              setCtxMenu(null);
            },
          },
          {
            label: "Cut",
            action: () => {
              const result = cutSelection();
              if (result.nodeCount) {
                notify(`${result.nodeCount} node${result.nodeCount === 1 ? "" : "s"} cut.`, "success");
              }
              setCtxMenu(null);
            },
          },
          {
            label: "Delete",
            danger: true,
            action: () => {
              deleteNode(ctxMenu.nodeId!);
              setCtxMenu(null);
            },
          },
        ]
      : [
          ...(
            selectedMetanodeIds.length >= 2
              ? [
                  {
                    label: `Group ${selectedMetanodeIds.length} nodes into metanode`,
                    action: () => {
                      const id = collapseToMetanode(selectedMetanodeIds);
                      if (id) notify(`Grouped ${selectedMetanodeIds.length} nodes into a metanode.`, "success");
                      else notify("Can't group: that selection would create a cycle.", "error");
                      setCtxMenu(null);
                    },
                  },
                ]
              : []
          ),
          {
            label: "Quick add node",
            action: () => {
              openQuickAddAt(ctxMenu.x, ctxMenu.y);
            },
          },
          {
            label: "Paste",
            action: () => {
              const r = pasteSelection();
              if (r.nodeCount) notify(`Pasted ${r.nodeCount} node${r.nodeCount === 1 ? "" : "s"}.`, "success");
              setCtxMenu(null);
            },
          },
          {
            label: "Fit view",
            action: () => {
              void fitView({ padding: 0.22, duration: 220 });
              setCtxMenu(null);
            },
          },
        ]
    : [], [ctxMenu, nodes, openNdv, ungroupMetanode, collapseToMetanode, notify, copySelection, pasteSelection, toggleDisabled, cutSelection, deleteNode, openQuickAddAt, fitView, setCtxMenu]);

  function focusContextItem(index: number): void {
    const buttons = Array.from(
      ctxMenuRef.current?.querySelectorAll<HTMLButtonElement>("[role='menuitem']") ?? [],
    );
    if (buttons.length === 0) return;
    const next = (index + buttons.length) % buttons.length;
    setCtxActiveIndex(next);
    buttons[next].focus();
  }

  function handleContextMenuKeyDown(e: React.KeyboardEvent<HTMLDivElement>): void {
    if (e.key === "Escape") {
      e.preventDefault();
      setCtxMenu(null);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusContextItem(ctxActiveIndex + 1);
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      focusContextItem(ctxActiveIndex - 1);
      return;
    }
    if (e.key === "Home") {
      e.preventDefault();
      focusContextItem(0);
      return;
    }
    if (e.key === "End") {
      e.preventDefault();
      focusContextItem(contextMenuItems.length - 1);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      contextMenuItems[ctxActiveIndex]?.action();
    }
  }

  function updateQuickAddQuery(value: string): void {
    setQuickAdd((current) => current ? { ...current, query: value } : current);
    setQuickAddActiveIndex(0);
  }

  function handleQuickAddKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === "Escape") {
      e.preventDefault();
      setQuickAdd(null);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setQuickAddActiveIndex((index) => Math.min(index + 1, Math.max(0, quickAddResults.length - 1)));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setQuickAddActiveIndex((index) => Math.max(0, index - 1));
      return;
    }
    if (e.key === "Enter") {
      const node = quickAddResults[quickAddActiveIndex] ?? quickAddResults[0];
      if (!node) return;
      e.preventDefault();
      addQuickNode(node, quickAddActiveIndex);
    }
  }

  return (
    <div
      className={`canvas${spaceDown ? " canvas--pan" : ""}`}
      data-tour-id="canvas"
      onDrop={onDrop}
      onDragOver={onDragOver}
      onClick={() => setCtxMenu(null)}
      onMouseDown={handleCanvasMouseDown}
    >
      <ReactFlow
        nodes={allNodes}
        edges={allEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={handleConnect}
        isValidConnection={isValidConnection}
        snapToGrid={snapToGrid}
        snapGrid={[20, 20]}
        onNodeClick={(_, node) => {
          setSelected(node.id);
          setQuickAdd(null);
        }}
        onNodeDoubleClick={(_, node) => {
          const sn = nodes.find((n) => n.id === node.id);
          if (sn?.data?.manifest?.id === "meta_node") {
            enterMetanode(node.id);
            window.setTimeout(() => void fitView({ padding: 0.22, duration: 220 }), 0);
            return;
          }
          if (node.type === "noodle" || node.type === "mapGroup") openNdv(node.id);
        }}
        onPaneClick={() => { setSelected(null); setCtxMenu(null); setQuickAdd(null); }}
        onNodeContextMenu={onNodeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        selectionOnDrag={spaceDown}
        panOnDrag={spaceDown ? [1, 2] : [0, 1]}
        onlyRenderVisibleElements
        colorMode="dark"
        fitView
        minZoom={0.2}
        maxZoom={2}
        defaultEdgeOptions={defaultEdgeOptions}
      >
        <MetanodeBreadcrumb />
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} />
        <MiniMap
          pannable
          zoomable
          nodeComponent={MiniMapNoodleNode}
          nodeColor={miniMapNodeColor}
          nodeClassName={miniMapNodeClassName}
          bgColor="#0b0e14"
          maskColor="rgba(11,14,20,0.72)"
          nodeStrokeWidth={0}
        />
        <PortLegend />
        <CanvasControls snapToGrid={snapToGrid} onToggleSnap={toggleSnapToGrid} />
        <DatasetConnectionHealth />
        {blockedConnection && (
          <DatasetConnectionBanner
            connection={blockedConnection.connection}
            check={blockedConnection.check}
            onDismiss={() => setBlockedConnection(null)}
          />
        )}
        {nodes.length === 0 && drillDepth === 0 && (
          <div className="canvas-empty-onboarding">
            <div className="canvas-empty-header">
              <h1>Start a workflow</h1>
              <p className="canvas-empty-subtitle">Drag nodes from the palette or pick a starter below to begin.</p>
            </div>
            <div className="canvas-starter-grid">
              {CANVAS_STARTERS.map((template) => (
                <button
                  type="button"
                  key={template.id}
                  className="canvas-starter-card"
                  onClick={() => applyStarter(template.id)}
                >
                  <strong>{template.name}</strong>
                  <span>{template.description}</span>
                </button>
              ))}
            </div>
            <p className="canvas-empty-hints">
              Press <kbd>Tab</kbd> to quickly add a node · <kbd>Shift+P</kbd> toggle node palette · <kbd>Ctrl+K</kbd> command palette
            </p>
            {paletteCollapsed && (
              <p className="canvas-empty-palette-hint">
                The node palette is collapsed — press <kbd>Shift+P</kbd> to open it and drag nodes onto the canvas.
              </p>
            )}
          </div>
        )}
      </ReactFlow>

      {canvasSelection.count >= 2 ? (
        <div className="canvas-batch-count">{canvasSelection.count} selected</div>
      ) : null}

      {ctxMenu && (
        <div
          ref={ctxMenuRef}
          className="canvas-ctx-menu"
          role="menu"
          tabIndex={-1}
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={handleContextMenuKeyDown}
        >
          {contextMenuItems.map((item, index) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className={`${item.danger ? "canvas-ctx-danger" : ""}${ctxActiveIndex === index ? " is-active" : ""}`}
              onClick={item.action}
              onFocus={() => setCtxActiveIndex(index)}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}

      {selectionRect && (
        <div
          className="canvas-selection-rect"
          style={{ left: selectionRect.x, top: selectionRect.y, width: selectionRect.w, height: selectionRect.h }}
        />
      )}

      {quickAdd && (
        <div
          className="canvas-quick-add"
          role="dialog"
          aria-modal="true"
          aria-label={quickAdd.insertEdgeId ? "Insert node into connection" : "Quick add node"}
          style={{ left: quickAdd.x, top: quickAdd.y }}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="canvas-quick-add-head">
            <strong>{quickAdd.insertEdgeId ? "Insert node" : "Add node"}</strong>
            <button
              type="button"
              aria-label="Close quick add"
              title="Close"
              onClick={() => setQuickAdd(null)}
            >
              <X size={12} weight="bold" />
            </button>
          </div>
          <input
            ref={quickAddInputRef}
            className="canvas-quick-add-input"
            placeholder={quickAdd.insertEdgeId ? "Search compatible nodes..." : "Search nodes..."}
            aria-label={quickAdd.insertEdgeId ? "Search compatible nodes" : "Search nodes"}
            value={quickAdd.query}
            onChange={(event) => updateQuickAddQuery(event.target.value)}
            onKeyDown={handleQuickAddKeyDown}
          />
          <div className="canvas-quick-add-list" role="listbox" aria-label="Matching nodes">
            {quickAddResults.map((node, index) => {
              const color = categoryColor(node.category);
              return (
                <button
                  type="button"
                  key={node.id}
                  className={`canvas-quick-add-item${quickAddActiveIndex === index ? " is-active" : ""}`}
                  role="option"
                  aria-selected={quickAddActiveIndex === index}
                  title={node.description}
                  onMouseEnter={() => setQuickAddActiveIndex(index)}
                  onClick={() => addQuickNode(node, index)}
                >
                  <span
                    className="canvas-quick-add-glyph"
                    style={{ color, background: `${color}1f` }}
                    aria-hidden
                  >
                    <NodeIcon name={node.icon} size={13} />
                  </span>
                  <span className="canvas-quick-add-body">
                    <span>{node.name}</span>
                    <small>{node.category}</small>
                  </span>
                </button>
              );
            })}
            {quickAddResults.length === 0 && (
              <div className="canvas-quick-add-empty">
                {quickAdd.insertEdgeId
                  ? "No compatible nodes fit this connection."
                  : `No nodes match "${quickAdd.query}".`}
              </div>
            )}
          </div>
        </div>
      )}

      <OnboardingTour />
    </div>
  );
}
