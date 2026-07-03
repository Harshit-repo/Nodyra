import type { NodeProps } from "@xyflow/react";
import { DiffNode } from "./DiffNode";
import { DiffEdge } from "./DiffEdge";
import { LoopFrame } from "./LoopFrame";
import { MapGroupNode } from "./MapGroupNode";
import { MetaBar } from "./MetaBar";
import { NodeCard } from "./NodeCard";
import { NodeGroup } from "./NodeGroup";
import { NodyraEdge } from "./NodyraEdge";
import { StickyNote } from "./StickyNote";
import { McpToolNode } from "./nodes/McpToolNode";

export const nodeTypes = {
  nodyra: NodeCard,
  sticky: StickyNote,
  group: NodeGroup,
  mapGroup: MapGroupNode,
  loopFrame: LoopFrame,
  metaBar: MetaBar,
  mcpTool: McpToolNode,
};

export const edgeTypes = { default: NodyraEdge };

export const diffNodeTypes = Object.fromEntries(
  Object.entries(nodeTypes).map(([type, Component]) => [
    type,
    (props: NodeProps) => (
      <DiffNode {...props} WrappedComponent={Component as React.ComponentType<NodeProps>} />
    ),
  ]),
) as unknown as typeof nodeTypes;

export const diffEdgeTypes = { default: DiffEdge };
