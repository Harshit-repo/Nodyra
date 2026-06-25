import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";
import { Plus, X } from "@phosphor-icons/react";
import { useShallow } from "zustand/react/shallow";

import { useEditor } from "./store";

export function deriveEdgeType(value: unknown): { icon: string; label: string } {
  if (value === undefined) return { icon: "", label: "" };
  if (value === null) return { icon: "∅", label: "null" };
  if (Array.isArray(value)) return { icon: "[]", label: `array · ${value.length}` };
  if (typeof value === "object") return { icon: "{}", label: "object" };
  if (typeof value === "string") return { icon: "T", label: "string" };
  if (typeof value === "number") return { icon: "#", label: "number" };
  if (typeof value === "boolean") return { icon: "⊤", label: "boolean" };
  return { icon: "?", label: typeof value };
}

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
  // Nearly every edge is a regular data edge. Reject it before touching the
  // graph so large workflows do not perform per-edge linear scans on every
  // store update.
  if (
    edgeInfo.targetHandleId !== null &&
    edgeInfo.targetHandleId !== undefined &&
    !AGENT_FLOW_HANDLES.has(edgeInfo.targetHandleId)
  ) {
    return undefined;
  }
  const needsEdgeLookup =
    !edgeInfo.source || !edgeInfo.target || edgeInfo.targetHandleId == null;
  const edge = needsEdgeLookup
    ? state.edges.find((item) => item.id === edgeInfo.id)
    : undefined;
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
  sourceHandleId,
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
  const edgeType = useEditor(
    useShallow((s): { icon: string; label: string } => {
      if (!source) return { icon: "", label: "" };
      const outputs = s.runOutputs[source];
      if (!outputs || typeof outputs !== "object") return { icon: "", label: "" };
      const val = (outputs as Record<string, unknown>)[sourceHandleId ?? "main"];
      return deriveEdgeType(val);
    }),
  );
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
          {edgeType.icon && (
            <div className="noodle-edge-type-badge" title={edgeType.label}>
              <span className="noodle-edge-type-icon">{edgeType.icon}</span>
              <span className="noodle-edge-type-label">{edgeType.label}</span>
            </div>
          )}
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
