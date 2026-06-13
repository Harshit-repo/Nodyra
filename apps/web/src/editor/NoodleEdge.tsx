import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";
import { Plus, X } from "@phosphor-icons/react";

import { useEditor } from "./store";

const AGENT_FLOW_TARGETS = new Set(["ai_agent", "ai_agent_v2"]);
const AGENT_FLOW_HANDLES = new Set(["model", "memory", "tool"]);

type AgentEdgeFlowState = Pick<
  ReturnType<typeof useEditor.getState>,
  "agentActive" | "agentToolCalls" | "edges" | "nodes" | "runStatus"
>;

export function getAgentEdgeFlowClass(
  state: AgentEdgeFlowState,
  edgeInfo: {
    id: string;
    source?: string | null;
    target?: string | null;
    targetHandleId?: string | null;
  },
): string | undefined {
  const edge = state.edges.find((item) => item.id === edgeInfo.id);
  const sourceNodeId = edgeInfo.source || edge?.source;
  const targetNodeId = edgeInfo.target || edge?.target;
  if (!sourceNodeId || !targetNodeId) return undefined;

  const targetNode = state.nodes.find((node) => node.id === targetNodeId);
  const targetHandle = edgeInfo.targetHandleId ?? edge?.targetHandle ?? "";
  if (
    !targetNode?.data?.manifest ||
    !AGENT_FLOW_TARGETS.has(targetNode.data.manifest.id) ||
    !AGENT_FLOW_HANDLES.has(targetHandle)
  ) {
    return undefined;
  }

  const sourceNode = state.nodes.find((node) => node.id === sourceNodeId);
  const sourceActive = state.agentActive[sourceNodeId] === "running";
  const targetActive =
    state.agentActive[targetNodeId] === "running" ||
    state.runStatus[targetNodeId] === "running";
  const activeToolCall = Object.values(state.agentToolCalls).includes(sourceNodeId);
  const shouldAnimate =
    sourceActive ||
    (targetHandle !== "tool" && targetActive) ||
    (targetHandle === "tool" && activeToolCall);
  if (!shouldAnimate) return undefined;

  return targetHandle === "tool" || sourceNode?.data?.toolMode
    ? "edge-agent-flow is-tool"
    : "edge-agent-flow";
}

export function NoodleEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  targetHandleId,
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
  // While an agent uses a connected sub-node, animate the wire so data appears
  // to flow from the model / memory / tool into the agent (n8n-style).
  const agentFlowClass = useEditor((s) =>
    getAgentEdgeFlowClass(s, { id, source, target, targetHandleId }),
  );

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={style}
      />
      {agentFlowClass ? (
        <BaseEdge
          id={`${id}-agent-flow`}
          path={edgePath}
          markerEnd={markerEnd}
          className={agentFlowClass}
          interactionWidth={0}
        />
      ) : null}
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
              className="noodle-edge-btn noodle-edge-btn-add"
              title="Insert node into connection"
              onClick={(event) => {
                event.stopPropagation();
                window.dispatchEvent(new CustomEvent("noodle:open-edge-quick-add", {
                  detail: { edgeId: id, clientX: event.clientX, clientY: event.clientY },
                }));
              }}
            >
              <Plus size={10} weight="bold" />
            </button>
            <button
              type="button"
              className="noodle-edge-btn noodle-edge-btn-delete"
              title="Delete connection"
              onClick={(event) => {
                event.stopPropagation();
                onEdgesChange([{ type: "remove", id }]);
              }}
            >
              <X size={9} weight="bold" />
            </button>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
