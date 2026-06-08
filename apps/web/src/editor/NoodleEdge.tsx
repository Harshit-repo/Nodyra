import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";
import { Play, X } from "@phosphor-icons/react";

import { useEditor } from "./store";

export function NoodleEdge({
  id,
  source,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  style,
  label,
  selected,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const onEdgesChange = useEditor((s) => s.onEdgesChange);
  const runHandler = useEditor((s) => s.runHandler);
  const running = useEditor((s) => s.running);
  // While an agent uses a connected sub-node, animate the wire so data appears
  // to flow from the model / memory / tool into the agent (n8n-style).
  const agentFlowClass = useEditor((s) => {
    if (s.agentActive[source] !== "running") return undefined;
    const node = s.nodes.find((n) => n.id === source);
    return node?.data.toolMode
      ? "edge-agent-flow is-tool"
      : "edge-agent-flow";
  });

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={style}
        className={agentFlowClass}
      />
      <EdgeLabelRenderer>
        <div
          className={`noodle-edge-mid nodrag nopan${selected ? " is-selected" : ""}`}
          style={{
            position: "absolute",
            transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: "all",
          }}
        >
          {label && <span className="noodle-edge-label">{String(label)}</span>}
          <div className="noodle-edge-actions">
            <button
              type="button"
              className="noodle-edge-btn"
              title="Run up to this node"
              disabled={running || !runHandler}
              onClick={() => void runHandler?.([source])}
            >
              <Play size={9} weight="fill" />
            </button>
            <button
              type="button"
              className="noodle-edge-btn noodle-edge-btn-delete"
              title="Delete connection"
              onClick={() => onEdgesChange([{ type: "remove", id }])}
            >
              <X size={9} weight="bold" />
            </button>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
