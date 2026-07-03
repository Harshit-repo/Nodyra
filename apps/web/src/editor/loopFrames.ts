import type { Edge } from "@xyflow/react";

import type { NodyraNode } from "./store";
import { loopBadgeText } from "./NodeCard";

// Fallbacks when a node hasn't been measured yet (first render).
const DEFAULT_W = 80;
const DEFAULT_H = 96;
const PAD = 30;
const LABEL_SPACE = 14; // extra headroom for the frame label

export interface LoopFrame {
  id: string;
  type: "loopFrame";
  position: { x: number; y: number };
  data: { label: string };
  style: { width: number; height: number };
  draggable: false;
  selectable: false;
  connectable: false;
  deletable: false;
  zIndex: number;
}

function nodeWidth(n: NodyraNode): number {
  return n.measured?.width ?? n.width ?? DEFAULT_W;
}
function nodeHeight(n: NodyraNode): number {
  return n.measured?.height ?? n.height ?? DEFAULT_H;
}

function reach(start: string, adj: Map<string, string[]>): Set<string> {
  const seen = new Set<string>();
  const stack = [...(adj.get(start) ?? [])];
  while (stack.length) {
    const n = stack.pop()!;
    if (seen.has(n)) continue;
    seen.add(n);
    for (const m of adj.get(n) ?? []) stack.push(m);
  }
  return seen;
}

/**
 * Compute one read-only frame per paired loop_start/loop_end, sized to enclose
 * the loop's body — the nodes that are graph-descendants of the Loop Start AND
 * graph-ancestors of the Loop End (the SESE region). Frames are derived from the
 * graph each render; they are never persisted.
 */
export function computeLoopFrames(nodes: NodyraNode[], edges: Edge[]): LoopFrame[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const succ = new Map<string, string[]>();
  const pred = new Map<string, string[]>();
  for (const e of edges) {
    if (!byId.has(e.source) || !byId.has(e.target)) continue;
    (succ.get(e.source) ?? succ.set(e.source, []).get(e.source)!).push(e.target);
    (pred.get(e.target) ?? pred.set(e.target, []).get(e.target)!).push(e.source);
  }

  // Pair each loop_start id with its loop_end (by loop_start_id param).
  const endForStart = new Map<string, string>();
  for (const n of nodes) {
    if (n.data.manifest?.id === "loop_end") {
      const sid = n.data.params.loop_start_id;
      if (typeof sid === "string" && sid) endForStart.set(sid, n.id);
    }
  }

  const frames: LoopFrame[] = [];
  for (const start of nodes) {
    if (start.data.manifest?.id !== "loop_start") continue;
    const endId = endForStart.get(start.id);
    if (!endId || !byId.has(endId)) continue;

    const desc = reach(start.id, succ);
    const anc = reach(endId, pred);
    const region = new Set<string>([start.id, endId]);
    for (const id of desc) if (anc.has(id)) region.add(id);

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const id of region) {
      const n = byId.get(id);
      if (!n) continue;
      minX = Math.min(minX, n.position.x);
      minY = Math.min(minY, n.position.y);
      maxX = Math.max(maxX, n.position.x + nodeWidth(n));
      maxY = Math.max(maxY, n.position.y + nodeHeight(n));
    }
    if (!Number.isFinite(minX)) continue;

    frames.push({
      id: `loopframe:${start.id}`,
      type: "loopFrame",
      position: { x: minX - PAD, y: minY - PAD - LABEL_SPACE },
      data: { label: loopBadgeText("loop_start", start.data.params) },
      style: {
        width: maxX - minX + PAD * 2,
        height: maxY - minY + PAD * 2 + LABEL_SPACE,
      },
      draggable: false,
      selectable: false,
      connectable: false,
      deletable: false,
      zIndex: -10,
    });
  }
  return frames;
}

export const LOOP_FRAME_ID_PREFIX = "loopframe:";
