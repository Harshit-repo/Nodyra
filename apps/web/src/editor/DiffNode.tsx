import { useContext } from "react";
import type { NodeProps } from "@xyflow/react";
import { DiffContext } from "./diffWorkflowGraphs";

const ringStyles: Record<string, React.CSSProperties> = {
  added: { boxShadow: "0 0 0 2px #22c55e", borderRadius: 8 },
  removed: { boxShadow: "0 0 0 2px #f87171", borderRadius: 8, opacity: 0.4, pointerEvents: "none" },
  changed: { boxShadow: "0 0 0 2px #f59e0b", borderRadius: 8 },
  unchanged: {},
};

interface DiffNodeProps extends NodeProps {
  WrappedComponent: React.ComponentType<NodeProps>;
}

export function DiffNode({ WrappedComponent, ...props }: DiffNodeProps) {
  const statusMap = useContext(DiffContext);
  const status = statusMap.get(props.id) ?? "unchanged";
  const style = ringStyles[status] ?? {};

  return (
    <div style={style}>
      <WrappedComponent {...props} />
    </div>
  );
}
