import { NodeResizer } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { memo, useState } from "react";

import { useEditor } from "./store";

interface NodeGroupData {
  label: string;
  color: string;
}

function NodeGroupComponent({ data, id }: NodeProps) {
  const groupData = data as unknown as NodeGroupData;
  const [label, setLabel] = useState(groupData.label ?? "Group");
  const onNodesChange = useEditor((s) => s.onNodesChange);

  function handleLabelChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = e.target.value;
    setLabel(val);
    const node = useEditor.getState().nodes.find((item) => item.id === id);
    if (!node) return;
    onNodesChange([{
      type: "replace",
      id: node.id,
      item: { ...node, data: { ...node.data, label: val } },
    }]);
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

export const NodeGroup = memo(
  NodeGroupComponent,
  (previous, next) => previous.id === next.id && previous.data === next.data,
);
