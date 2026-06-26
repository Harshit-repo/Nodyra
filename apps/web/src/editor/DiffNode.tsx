import { useContext } from "react";
import type { NodeProps } from "@xyflow/react";
import { DiffContext } from "./diffWorkflowGraphs";

const ringColor: Record<string, string> = {
  added: "#22c55e",
  removed: "#f87171",
  changed: "#f59e0b",
};

interface DiffNodeProps extends NodeProps {
  WrappedComponent: React.ComponentType<NodeProps>;
}

export function DiffNode({ WrappedComponent, ...props }: DiffNodeProps) {
  const statusMap = useContext(DiffContext);
  const status = statusMap.get(props.id) ?? "unchanged";
  const isRemoved = status === "removed";

  return (
    // position:relative so the ring overlay positions against the node root
    <div style={{ position: "relative", opacity: isRemoved ? 0.4 : 1, pointerEvents: isRemoved ? "none" : undefined }}>
      <WrappedComponent {...props} />
      {status !== "unchanged" && (
        <div
          aria-hidden
          style={{
            position: "absolute",
            inset: -2,
            borderRadius: 8,
            boxShadow: `0 0 0 2px ${ringColor[status]}`,
            pointerEvents: "none",
          }}
        />
      )}
    </div>
  );
}
