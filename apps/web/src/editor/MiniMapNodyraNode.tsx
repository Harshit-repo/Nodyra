import type { MiniMapNodeProps, Node } from "@xyflow/react";

import { categoryColor } from "../categories";
import type { NodyraNode } from "./store";

const STICKY_COLORS: Record<string, string> = {
  yellow: "#fef08a",
  pink: "#fda4af",
  blue: "#93c5fd",
  green: "#86efac",
  purple: "#d8b4fe",
};

// Matches --surface (node card background) and --canvas
const NODE_BG = "#10141c";
const NEUTRAL_COLOR = "#7882a0";

export function miniMapNodeColor(node: Node): string {
  if (node.type === "sticky") {
    return (
      STICKY_COLORS[(node.data as Record<string, unknown>).color as string] ??
      STICKY_COLORS.yellow!
    );
  }
  const manifest = (node as NodyraNode).data.manifest;
  return manifest ? categoryColor(manifest.category) : NEUTRAL_COLOR;
}

export function miniMapNodeClassName(node: Node): string {
  if (node.type === "sticky") return "minimap-node--sticky";
  if (node.type === "group") return "minimap-node--group";
  return (node as NodyraNode).data.manifest ? "minimap-node--workflow" : "minimap-node--neutral";
}

export function MiniMapNodyraNode({
  x,
  y,
  width,
  height,
  selected,
  color = NEUTRAL_COLOR,
  className,
}: MiniMapNodeProps) {
  if (className === "minimap-node--sticky") {
    return (
      <rect x={x} y={y} width={width} height={height} rx={3} fill={color} opacity={0.9} />
    );
  }

  if (className === "minimap-node--group") {
    return (
      <rect
        x={x} y={y} width={width} height={height} rx={4}
        fill="rgba(255,255,255,0.02)"
        stroke="rgba(255,255,255,0.08)"
        strokeWidth={1.5}
        strokeDasharray="4 2"
      />
    );
  }

  if (className === "minimap-node--neutral") {
    return (
      <rect
        x={x} y={y} width={width} height={height} rx={4}
        fill="rgba(120,130,160,0.12)"
        stroke="rgba(120,130,160,0.4)"
        strokeWidth={1.2}
      />
    );
  }
  const rx = Math.min(Math.round(width * 0.16), 10);
  return (
    <rect
      x={x}
      y={y}
      width={width}
      height={height}
      rx={rx}
      fill={NODE_BG}
      stroke={selected ? "#6aa9ff" : color}
      strokeWidth={selected ? 2.2 : 1.2}
    />
  );
}
