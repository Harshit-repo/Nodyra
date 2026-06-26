import type { NodeProps } from "@xyflow/react";
import { DiffNode } from "./DiffNode";
import { DiffEdge } from "./DiffEdge";
import { LoopFrame } from "./LoopFrame";
import { MapGroupNode } from "./MapGroupNode";
import { MetaBar } from "./MetaBar";
import { NodeCard } from "./NodeCard";
import { NodeGroup } from "./NodeGroup";
import { NoodleEdge } from "./NoodleEdge";
import { StickyNote } from "./StickyNote";

export const nodeTypes = {
  noodle: NodeCard,
  sticky: StickyNote,
  group: NodeGroup,
  mapGroup: MapGroupNode,
  loopFrame: LoopFrame,
  metaBar: MetaBar,
};

export const edgeTypes = { default: NoodleEdge };

export const diffNodeTypes = Object.fromEntries(
  Object.entries(nodeTypes).map(([type, Component]) => [
    type,
    (props: NodeProps) => (
      <DiffNode {...props} WrappedComponent={Component as React.ComponentType<NodeProps>} />
    ),
  ]),
) as unknown as typeof nodeTypes;

export const diffEdgeTypes = { default: DiffEdge };
