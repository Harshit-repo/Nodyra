import {
  Background,
  BackgroundVariant,
  MiniMap,
  ReactFlow,
  useReactFlow,
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
  TreeStructure,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import type { Connection, Edge } from "@xyflow/react";

import { CANVAS_STARTERS } from "../workflowTemplates";
import { useToast } from "../ToastProvider";
import { LoopFrame } from "./LoopFrame";
import { LOOP_FRAME_ID_PREFIX, computeLoopFrames } from "./loopFrames";
import { MapGroupNode } from "./MapGroupNode";
import { MetanodePreview } from "./MetanodePreview";
import { MiniMapNoodleNode } from "./MiniMapNoodleNode";
import { NodeCard } from "./NodeCard";
import { NodeGroup } from "./NodeGroup";
import { NoodleEdge } from "./NoodleEdge";
import { PortLegend } from "./PortLegend";
import { StickyNote } from "./StickyNote";
import { datasetConnectionIssues, validateConnection, type ConnectionCheck } from "./connectionValidation";
import { pickEditorRunTrigger, useEditor, type NoodleNode } from "./store";

const nodeTypes = {
  noodle: NodeCard,
  sticky: StickyNote,
  group: NodeGroup,
  mapGroup: MapGroupNode,
  loopFrame: LoopFrame,
};
const edgeTypes = { default: NoodleEdge };

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
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const issues = useMemo(() => datasetConnectionIssues(nodes, edges), [nodes, edges]);
  if (issues.length === 0) return null;
  return (
    <div className="connection-health" role="status">
      <strong>{issues.length} incompatible wire{issues.length === 1 ? "" : "s"}</strong>
      <span>{issues[0].check.message}</span>
    </div>
  );
}

function CanvasControls() {
  const { fitView, zoomIn, zoomOut } = useReactFlow();
  const { notify } = useToast();
  const autoLayout = useEditor((s) => s.autoLayout);
  const addStickyNote = useEditor((s) => s.addStickyNote);
  const showLoopFrames = useEditor((s) => s.showLoopFrames);
  const toggleLoopFrames = useEditor((s) => s.toggleLoopFrames);
  const hasLoop = useEditor((s) =>
    s.nodes.some((n) => n.data.manifest?.id === "loop_start"),
  );
  const nodes = useEditor((s) => s.nodes);
  const running = useEditor((s) => s.running);
  const runHandler = useEditor((s) => s.runHandler);
  const hasTrigger = useEditor((s) => pickEditorRunTrigger(s.nodes) !== null);
  const undo = useEditor((s) => s.undo);
  const redo = useEditor((s) => s.redo);
  const copySelection = useEditor((s) => s.copySelection);
  const cutSelection = useEditor((s) => s.cutSelection);
  const pasteSelection = useEditor((s) => s.pasteSelection);
  const canUndo = useEditor((s) => s._past.length > 0);
  const canRedo = useEditor((s) => s._future.length > 0);
  const clipboardNodeCount = useEditor((s) => s.clipboardNodeCount);
  const selectedCount = useEditor((s) => {
    const selected = s.nodes.filter((node) => node.selected).length;
    return selected || (s.selectedId ? 1 : 0);
  });

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
        title={nodes.length < 2 ? "Auto-layout requires at least 2 nodes" : "Auto layout (Shift+L)"}
        aria-label="Auto layout"
        disabled={nodes.length < 2}
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
        <button type="button" title="Undo (Ctrl+Z)" aria-label="Undo" disabled={!canUndo} onClick={() => undo()}>
          <ArrowCounterClockwise size={14} weight="bold" />
        </button>
        <button type="button" title="Redo (Ctrl+Shift+Z)" aria-label="Redo" disabled={!canRedo} onClick={() => redo()}>
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
      </div>

      {/* Run — always visible on the right */}
      <button
        type="button"
        className="is-primary"
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
  const onNodesChange = useEditor((s) => s.onNodesChange);
  const showLoopFrames = useEditor((s) => s.showLoopFrames);
  const onEdgesChange = useEditor((s) => s.onEdgesChange);
  const onConnect = useEditor((s) => s.onConnect);
  const addNode = useEditor((s) => s.addNode);
  const addBodyNode = useEditor((s) => s.addBodyNode);
  const childWorkflows = useEditor((s) => s.childWorkflows);
  const loadGraph = useEditor((s) => s.loadGraph);
  const setSelected = useEditor((s) => s.setSelected);
  const openNdv = useEditor((s) => s.openNdv);
  const collapseToMetanode = useEditor((s) => s.collapseToMetanode);
  const ungroupMetanode = useEditor((s) => s.ungroupMetanode);
  const toggleDisabled = useEditor((s) => s.toggleDisabled);
  const autoEnableAgentDependencies = useEditor((s) => s.autoEnableAgentDependencies);
  const deleteNode = useEditor((s) => s.deleteNode);
  const copySelection = useEditor((s) => s.copySelection);
  const cutSelection = useEditor((s) => s.cutSelection);
  const pasteSelection = useEditor((s) => s.pasteSelection);
  const autoLayout = useEditor((s) => s.autoLayout);
  const { fitView, screenToFlowPosition, getNodes } = useReactFlow();
  const { notify } = useToast();
  const [blockedConnection, setBlockedConnection] = useState<{
    connection: Connection;
    check: ConnectionCheck;
  } | null>(null);

  useEffect(() => {
    autoEnableAgentDependencies();
  }, [autoEnableAgentDependencies, nodes, edges]);

  interface CtxMenu { x: number; y: number; nodeId?: string }
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);
  const [metaPreviewId, setMetaPreviewId] = useState<string | null>(null);
  const [ctxActiveIndex, setCtxActiveIndex] = useState(0);
  const ctxMenuRef = useRef<HTMLDivElement | null>(null);

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

  useEffect(() => {
    function onFitView(): void {
      void fitView({ padding: 0.22, duration: 220 });
    }
    window.addEventListener("noodle:fit-view", onFitView);
    return () => window.removeEventListener("noodle:fit-view", onFitView);
  }, [fitView]);

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

  const onNodeContextMenu = useCallback((e: React.MouseEvent, node: { id: string }) => {
    e.preventDefault();
    setSelected(node.id);
    setCtxMenu({ x: e.clientX, y: e.clientY, nodeId: node.id });
  }, [setSelected]);

  const onPaneContextMenu = useCallback((e: React.MouseEvent | MouseEvent) => {
    e.preventDefault();
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

  const labeledEdges = edges.map((e) =>
    e.sourceHandle && e.sourceHandle !== "main" && e.sourceHandle !== "output"
      ? { ...e, label: e.sourceHandle }
      : e,
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
        changes.filter(
          (c) => !("id" in c && typeof c.id === "string" && c.id.startsWith(LOOP_FRAME_ID_PREFIX)),
        ),
      ),
    [onNodesChange],
  );

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

  const contextMenuItems = ctxMenu
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
            const isMeta = ctxNode?.data.manifest?.id === "meta_node";
            const selectedIds = nodes.filter((n) => n.selected).map((n) => n.id);
            const groupIds =
              selectedIds.length >= 2
                ? selectedIds
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
    : [];

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

  return (
    <div className="canvas" onDrop={onDrop} onDragOver={onDragOver} onClick={() => setCtxMenu(null)}>
      <ReactFlow
        nodes={allNodes}
        edges={allEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={handleConnect}
        isValidConnection={isValidConnection}
        onNodeClick={(_, node) => setSelected(node.id)}
        onNodeDoubleClick={(_, node) => {
          const sn = nodes.find((n) => n.id === node.id);
          if (sn?.data.manifest?.id === "meta_node") {
            setMetaPreviewId(node.id);
            return;
          }
          if (node.type === "noodle" || node.type === "mapGroup") openNdv(node.id);
        }}
        onPaneClick={() => { setSelected(null); setCtxMenu(null); }}
        onNodeContextMenu={onNodeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        selectionOnDrag
        panOnDrag={[1, 2]}
        colorMode="dark"
        fitView
        minZoom={0.2}
        maxZoom={2}
        defaultEdgeOptions={{ type: "default" }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} />
        <MiniMap
          pannable
          zoomable
          nodeComponent={MiniMapNoodleNode}
          bgColor="#0b0e14"
          maskColor="rgba(11,14,20,0.72)"
          nodeStrokeWidth={0}
        />
        <PortLegend />
        <CanvasControls />
        <DatasetConnectionHealth />
        {blockedConnection && (
          <DatasetConnectionBanner
            connection={blockedConnection.connection}
            check={blockedConnection.check}
            onDismiss={() => setBlockedConnection(null)}
          />
        )}
        {nodes.length === 0 && (
          <div className="canvas-empty-onboarding">
            <div>
              <h2>Start a workflow</h2>
              <p>Choose a starter, then replace any placeholder values.</p>
            </div>
            <div className="canvas-starter-grid">
              {CANVAS_STARTERS.map((template) => (
                <button
                  type="button"
                  key={template.id}
                  onClick={() => applyStarter(template.id)}
                >
                  <strong>{template.name}</strong>
                  <span>{template.description}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </ReactFlow>

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

      {metaPreviewId && (() => {
        const meta = nodes.find((n) => n.id === metaPreviewId);
        if (!meta) return null;
        return (
          <MetanodePreview
            metaNode={meta}
            onClose={() => setMetaPreviewId(null)}
            onUngroup={() => {
              ungroupMetanode(metaPreviewId);
              setMetaPreviewId(null);
              notify("Metanode ungrouped.", "success");
            }}
          />
        );
      })()}
    </div>
  );
}
