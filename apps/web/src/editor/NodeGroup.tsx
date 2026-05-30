import { NodeResizer } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { useState } from "react";

import { useEditor } from "./store";

interface NodeGroupData {
  label: string;
  color: string;
}

export function NodeGroup({ data, id }: NodeProps) {
  const groupData = data as unknown as NodeGroupData;
  const [label, setLabel] = useState(groupData.label ?? "Group");
  const nodes = useEditor((s) => s.nodes);
  const onNodesChange = useEditor((s) => s.onNodesChange);

  function handleLabelChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = e.target.value;
    setLabel(val);
    const changes = nodes
      .filter((n) => n.id === id)
      .map((n) => ({
        type: "replace" as const,
        id: n.id,
        item: { ...n, data: { ...n.data, label: val } },
      }));
    if (changes.length > 0) onNodesChange(changes);
  }

  const bg = groupData.color ?? "rgba(99,102,241,0.08)";

  return (
    <>
      <NodeResizer minWidth={160} minHeight={120} />
      <div
        className="node-group"
        style={{
          background: bg,
          border: "1.5px dashed rgba(99,102,241,0.45)",
          borderRadius: 10,
          width: "100%",
          height: "100%",
          boxSizing: "border-box",
          pointerEvents: "none",
        }}
      >
        <div
          style={{
            pointerEvents: "all",
            padding: "4px 8px",
            display: "flex",
            alignItems: "center",
          }}
        >
          <input
            className="node-group-label nodrag"
            value={label}
            onChange={handleLabelChange}
            style={{
              background: "transparent",
              border: "none",
              outline: "none",
              fontWeight: 600,
              fontSize: 12,
              color: "rgba(99,102,241,0.9)",
              cursor: "text",
              width: "100%",
            }}
          />
        </div>
      </div>
    </>
  );
}
