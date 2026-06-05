import type { MiniMapNodeProps, Node } from "@xyflow/react";

import { categoryColor } from "../categories";
import { type NoodleNode, useEditor } from "./store";

const STICKY_COLORS: Record<string, string> = {
  yellow: "#fef08a",
  pink: "#fda4af",
  blue: "#93c5fd",
  green: "#86efac",
  purple: "#d8b4fe",
};

export function MiniMapNoodleNode({ id, x, y, width, height, selected }: MiniMapNodeProps) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === id)) as Node | undefined;

  if (!node || node.type === "sticky") {
    const fill = node
      ? (STICKY_COLORS[(node.data as Record<string, unknown>).color as string] ?? STICKY_COLORS.yellow!)
      : "#fef08a";
    return <rect x={x} y={y} width={width} height={height} rx={3} fill={fill} />;
  }

  if (node.type === "group") {
    return (
      <rect
        x={x} y={y} width={width} height={height} rx={4}
        fill="rgba(255,255,255,0.03)"
        stroke="rgba(255,255,255,0.1)"
        strokeWidth={1.5}
        strokeDasharray="4 2"
      />
    );
  }

  const manifest = (node as NoodleNode).data.manifest;
  const color = categoryColor(manifest.category);
  const rx = Math.min(Math.round(width * 0.22), 8);

  return (
    <rect
      x={x} y={y} width={width} height={height} rx={rx}
      fill={color + "1e"}
      stroke={selected ? "#6aa9ff" : color}
      strokeWidth={selected ? 2.5 : 1.5}
    />
  );
}
