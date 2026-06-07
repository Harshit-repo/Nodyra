import { Background, BackgroundVariant, ReactFlow } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";

import type { NoodleNode } from "./store";

interface SubNode {
  id: string;
  type: string;
  position?: { x: number; y: number };
}
interface SubEdge {
  id?: string;
  source: string;
  target: string;
}

/**
 * Read-only drill-in view of a metanode's contents. Renders the embedded
 * sub-graph so the user can see inside; editing is done by ungrouping.
 */
export function MetanodePreview({
  metaNode,
  onClose,
  onUngroup,
}: {
  metaNode: NoodleNode;
  onClose: () => void;
  onUngroup: () => void;
}) {
  const params = metaNode.data.params as {
    name?: string;
    execution?: string;
    subgraph?: { nodes?: SubNode[]; edges?: SubEdge[] };
  };
  const sub = params.subgraph ?? { nodes: [], edges: [] };
  const name = params.name || "Metanode";

  const rfNodes: Node[] = (sub.nodes ?? []).map((n, i) => ({
    id: n.id,
    position: n.position ?? { x: (i % 4) * 200, y: Math.floor(i / 4) * 130 },
    data: { label: `${n.type}\n${n.id}` },
    type: "default",
  }));
  const rfEdges: Edge[] = (sub.edges ?? []).map((e) => ({
    id: e.id ?? `${e.source}->${e.target}`,
    source: e.source,
    target: e.target,
  }));

  return (
    <div className="meta-preview-overlay" onClick={onClose}>
      <div className="meta-preview" onClick={(e) => e.stopPropagation()}>
        <header className="meta-preview-head">
          <span className="meta-preview-crumb">
            Workflow <span className="meta-preview-sep">›</span> {name}
            {params.execution === "isolated" && (
              <span className="meta-preview-tag">isolated</span>
            )}
          </span>
          <div className="meta-preview-actions">
            <button type="button" onClick={onUngroup}>
              Ungroup to edit
            </button>
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </header>
        <div className="meta-preview-canvas">
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            fitView
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            colorMode="dark"
            minZoom={0.2}
            maxZoom={2}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} />
          </ReactFlow>
        </div>
      </div>
    </div>
  );
}
