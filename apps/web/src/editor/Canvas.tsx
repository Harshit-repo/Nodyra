import {
  Background,
  BackgroundVariant,
  MiniMap,
  ReactFlow,
  useReactFlow,
} from "@xyflow/react";
import { useCallback, useEffect } from "react";
import type { DragEvent } from "react";

import { categoryColor } from "../categories";
import { CANVAS_STARTERS } from "../workflowTemplates";
import { NodeCard } from "./NodeCard";
import { pickEditorRunTrigger, type NoodleNode, useEditor } from "./store";

const nodeTypes = { noodle: NodeCard };

function CanvasControls() {
  const { fitView, zoomIn, zoomOut } = useReactFlow();
  const autoLayout = useEditor((s) => s.autoLayout);
  const nodes = useEditor((s) => s.nodes);
  const running = useEditor((s) => s.running);
  const runHandler = useEditor((s) => s.runHandler);
  const hasTrigger = useEditor((s) => pickEditorRunTrigger(s.nodes) !== null);

  return (
    <div className="canvas-controls" aria-label="Canvas controls">
      <button
        type="button"
        title="Zoom out"
        aria-label="Zoom out"
        onClick={() => void zoomOut({ duration: 160 })}
      >
        −
      </button>
      <button
        type="button"
        title="Zoom in"
        aria-label="Zoom in"
        onClick={() => void zoomIn({ duration: 160 })}
      >
        +
      </button>
      <button
        type="button"
        title="Fit view"
        aria-label="Fit view"
        onClick={() => void fitView({ padding: 0.22, duration: 220 })}
      >
        ⤢
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
        ⇥
      </button>
      <button type="button" title="Undo (coming soon)" aria-label="Undo" disabled>
        ↶
      </button>
      <button type="button" title="Redo (coming soon)" aria-label="Redo" disabled>
        ↷
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
        ▶
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
  const { fitView, screenToFlowPosition } = useReactFlow();

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

  function applyStarter(templateId: string): void {
    const template = CANVAS_STARTERS.find((item) => item.id === templateId);
    if (!template?.graph) return;
    loadGraph(template.graph(), { dirty: true });
    window.setTimeout(() => void fitView({ padding: 0.24, duration: 220 }), 30);
  }

  return (
    <div className="canvas" onDrop={onDrop} onDragOver={onDragOver}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={(_, node) => setSelected(node.id)}
        onNodeDoubleClick={(_, node) => openNdv(node.id)}
        onPaneClick={() => setSelected(null)}
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
          nodeColor={(node) =>
            categoryColor((node as NoodleNode).data.manifest.category)
          }
          maskColor="rgba(8,11,16,0.74)"
        />
        <CanvasControls />
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
    </div>
  );
}
