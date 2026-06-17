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

// Matches --surface (node card background) and --canvas
const NODE_BG = "#10141c";

export function MiniMapNoodleNode({ id, x, y, width, height, selected }: MiniMapNodeProps) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === id)) as Node | undefined;

  if (!node) return null;

  if (node.type === "sticky") {
    const fill =
      STICKY_COLORS[(node.data as Record<string, unknown>).color as string] ??
      STICKY_COLORS.yellow!;
    return (
      <rect x={x} y={y} width={width} height={height} rx={3} fill={fill} opacity={0.9} />
    );
  }

  if (node.type === "group") {
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

  // Boundary bars (and any other manifest-less render node, e.g. loop frames)
  // aren't real graph nodes — draw a neutral slab instead of reading a manifest.
  const manifest = (node as NoodleNode).data.manifest;
  if (!manifest) {
    return (
      <rect
        x={x} y={y} width={width} height={height} rx={4}
        fill="rgba(120,130,160,0.12)"
        stroke="rgba(120,130,160,0.4)"
        strokeWidth={1.2}
      />
    );
  }
  const color = categoryColor(manifest.category);
  const rx = Math.min(Math.round(width * 0.16), 10);

  // Icon area: centered inset square, ~56% of tile dimensions
  const pad = width * 0.22;
  const iconX = x + pad;
  const iconY = y + pad;
  const iconW = width - pad * 2;
  const iconH = height - pad * 2;
  const iconRx = Math.min(iconW * 0.22, 5);

  // Port dot radius
  const portR = Math.max(1.8, width * 0.058);

  return (
    <g>
      {/* Node body — dark card matching --surface */}
      <rect
        x={x} y={y} width={width} height={height} rx={rx}
        fill={NODE_BG}
        stroke={selected ? "#6aa9ff" : color}
        strokeWidth={selected ? 2.2 : 1.2}
      />
      {/* Subtle top highlight line */}
      <rect
        x={x + rx} y={y} width={width - rx * 2} height={1.5}
        fill={color}
        opacity={0.35}
        rx={0}
      />
      {/* Icon area — colored rounded rect in center */}
      <rect
        x={iconX} y={iconY} width={iconW} height={iconH} rx={iconRx}
        fill={color + "2e"}
        stroke={color + "80"}
        strokeWidth={0.8}
      />
      {/* Port dots on left and right edges */}
      <circle cx={x} cy={y + height / 2} r={portR} fill={color} />
      <circle cx={x + width} cy={y + height / 2} r={portR} fill={color} />
    </g>
  );
}
