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
  ClipboardText,
  Copy,
  Minus,
  Play,
  Plus,
  TreeStructure,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { DragEvent } from "react";
import type { Connection, Edge } from "@xyflow/react";

import { CANVAS_STARTERS } from "../workflowTemplates";
import { useToast } from "../ToastProvider";
import { MiniMapNoodleNode } from "./MiniMapNoodleNode";
import { NodeCard } from "./NodeCard";
import { NodeGroup } from "./NodeGroup";
import { NoodleEdge } from "./NoodleEdge";
import { PortLegend } from "./PortLegend";
import { StickyNote } from "./StickyNote";
import { datasetConnectionIssues, validateConnection, type ConnectionCheck } from "./connectionValidation";
import { pickEditorRunTrigger, useEditor } from "./store";

const nodeTypes = { noodle: NodeCard, sticky: StickyNote, group: NodeGroup };
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
  const nodes = useEditor((s) => s.nodes);
  const running = useEditor((s) => s.running);
  const runHandler = useEditor((s) => s.runHandler);
  const hasTrigger = useEditor((s) => pickEditorRunTrigger(s.nodes) !== null);
  const undo = useEditor((s) => s.undo);
  const redo = useEditor((s) => s.redo);
  const copySelection = useEditor((s) => s.copySelection);
  const pasteSelection = useEditor((s) => s.pasteSelection);
  const canUndo = useEditor((s) => s._past.length > 0);
  const canRedo = useEditor((s) => s._future.length > 0);
  const clipboardNodeCount = useEditor((s) => s.clipboardNodeCount);
  const selectedCount = useEditor((s) => {
    const selected = s.nodes.filter((node) => node.selected).length;
    return selected || (s.selectedId ? 1 : 0);
  });

  function copiedLabel(count: number): string {
    return `${count} node${count === 1 ? "" : "s"} copied.`;
  }

  function pastedLabel(count: number): string {
    return `${count} node${count === 1 ? "" : "s"} pasted.`;
  }

  return (
    <div className="canvas-controls" aria-label="Canvas controls">
      <button
        type="button"
        title="Zoom out"
        aria-label="Zoom out"
        onClick={() => void zoomOut({ duration: 160 })}
      >
        <Minus size={14} weight="bold" />
      </button>
      <button
        type="button"
        title="Zoom in"
        aria-label="Zoom in"
        onClick={() => void zoomIn({ duration: 160 })}
      >
        <Plus size={14} weight="bold" />
      </button>
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
        title="Auto layout"
        aria-label="Auto layout"
        onClick={() => {
          autoLayout();
          window.setTimeout(
            () => void fitView({ padding: 0.24, duration: 220 }),
            30,
          );
        }}
        disabled={nodes.length < 2}
      >
        <TreeStructure size={14} weight="bold" />
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
      <button
        type="button"
        title="Undo (Ctrl+Z)"
        aria-label="Undo"
        disabled={!canUndo}
        onClick={() => undo()}
      >
        <ArrowCounterClockwise size={14} weight="bold" />
      </button>
      <button
        type="button"
        title="Redo (Ctrl+Shift+Z)"
        aria-label="Redo"
        disabled={!canRedo}
        onClick={() => redo()}
      >
        <ArrowClockwise size={14} weight="bold" />
      </button>
      <span className="canvas-control-sep" />
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
  const onEdgesChange = useEditor((s) => s.onEdgesChange);
  const onConnect = useEditor((s) => s.onConnect);
  const addNode = useEditor((s) => s.addNode);
  const loadGraph = useEditor((s) => s.loadGraph);
  const setSelected = useEditor((s) => s.setSelected);
  const openNdv = useEditor((s) => s.openNdv);
  const toggleDisabled = useEditor((s) => s.toggleDisabled);
  const deleteNode = useEditor((s) => s.deleteNode);
  const copySelection = useEditor((s) => s.copySelection);
  const pasteSelection = useEditor((s) => s.pasteSelection);
  const autoLayout = useEditor((s) => s.autoLayout);
  const { fitView, screenToFlowPosition } = useReactFlow();
  const { notify } = useToast();
  const [blockedConnection, setBlockedConnection] = useState<{
    connection: Connection;
    check: ConnectionCheck;
  } | null>(null);

  interface CtxMenu { x: number; y: number; nodeId?: string }
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);

  const onDrop = useCallback(
    (event: DragEvent) => {
      event.preventDefault();
      const manifestId = event.dataTransfer.getData("application/noodle");
      if (!manifestId) return;
      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });
      addNode(manifestId, position);
    },
    [screenToFlowPosition, addNode],
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
    return validateConnection(useEditor.getState().nodes, {
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

  return (
    <div className="canvas" onDrop={onDrop} onDragOver={onDragOver} onClick={() => setCtxMenu(null)}>
      <ReactFlow
        nodes={nodes}
        edges={labeledEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={handleConnect}
        isValidConnection={isValidConnection}
        onNodeClick={(_, node) => setSelected(node.id)}
        onNodeDoubleClick={(_, node) => openNdv(node.id)}
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
          className="canvas-ctx-menu"
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          {ctxMenu.nodeId ? (
            <>
              <button onClick={() => { openNdv(ctxMenu.nodeId!); setCtxMenu(null); }}>Open</button>
              <button onClick={() => {
                const r = copySelection();
                if (r.nodeCount) { const p = pasteSelection(); if (p.nodeCount) notify(`Duplicated ${p.nodeCount} node(s).`, "success"); }
                setCtxMenu(null);
              }}>Duplicate</button>
              <button onClick={() => { toggleDisabled(ctxMenu.nodeId!); setCtxMenu(null); }}>
                {nodes.find((n) => n.id === ctxMenu.nodeId)?.data.disabled ? "Enable" : "Disable"}
              </button>
              <div className="canvas-ctx-sep" />
              <button className="canvas-ctx-danger" onClick={() => { deleteNode(ctxMenu.nodeId!); setCtxMenu(null); }}>Delete</button>
            </>
          ) : (
            <>
              <button onClick={() => { const r = pasteSelection(); if (r.nodeCount) notify(`Pasted ${r.nodeCount} node(s).`, "success"); setCtxMenu(null); }}>Paste</button>
              <button onClick={() => { void fitView({ padding: 0.22, duration: 220 }); setCtxMenu(null); }}>Fit view</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
