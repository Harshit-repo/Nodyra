import type { Edge } from "@xyflow/react";

import type { NodyraNode } from "./index";

export const META_BAR_INPUT_ID = "__meta_input_bar__";
export const META_BAR_OUTPUT_ID = "__meta_output_bar__";

/** A boundary port shown on a bar: stable id + display label. */
export interface MetaPortDescriptor {
  id: string;
  label: string;
}

/** Persisted boundary mapping on a metanode's `params.ports`. `data_kind` mirrors
 *  the data kind of the boundary internal port (e.g. "dataset") so the metanode
 *  surfaces typed ports on the parent instead of a generic "any". */
export interface MetaPorts {
  inputs: { port: string; data_kind?: string; targets: { target: string; target_input: string }[] }[];
  outputs: { port: string; data_kind?: string; source: string; source_output: string }[];
}

/** Minimal shape of a stored sub-graph node (mirrors GraphNodeLike in index.ts). */
export interface GraphNodeShape {
  id: string;
  type: string;
  params: Record<string, unknown>;
  position: { x: number; y: number };
  [key: string]: unknown;
}

/** A suspended parent level while the user edits a nested interior. */
export interface DrillFrame {
  metaId: string;
  name: string;
  nodes: NodyraNode[];
  edges: Edge[];
  _past: Array<{ nodes: NodyraNode[]; edges: Edge[] }>;
  _future: Array<{ nodes: NodyraNode[]; edges: Edge[] }>;
  /** origSubNodes of the PARENT level being suspended (for unknown carry-forward). */
  orig: Record<string, GraphNodeShape>;
  /** next-port-seq of the PARENT level. */
  portSeq: number;
}

export interface DrillSliceState {
  drillStack: DrillFrame[];
  drillOrig: Record<string, GraphNodeShape>;
  drillPortSeq: number;
}

export const drillInitialState: DrillSliceState = {
  drillStack: [],
  drillOrig: {},
  drillPortSeq: 1,
};

export function isMetaBar(node: { id?: string; type?: string } | null | undefined): boolean {
  return (
    !!node &&
    (node.type === "metaBar" ||
      node.id === META_BAR_INPUT_ID ||
      node.id === META_BAR_OUTPUT_ID)
  );
}

/** Runtime id prefix for the current interior, e.g. path [A,B] -> "A/B/". */
export function drillPrefix(stack: DrillFrame[]): string {
  return stack.length === 0 ? "" : stack.map((f) => f.metaId).join("/") + "/";
}

/** 1 + the highest numeric suffix across all existing port ids (in_3 -> 3). */
export function maxPortSuffix(ports: MetaPorts | undefined): number {
  let max = 0;
  for (const p of ports?.inputs ?? []) {
    const n = Number(/(\d+)$/.exec(p.port)?.[1] ?? -1);
    if (n > max) max = n;
  }
  for (const p of ports?.outputs ?? []) {
    const n = Number(/(\d+)$/.exec(p.port)?.[1] ?? -1);
    if (n > max) max = n;
  }
  return max;
}
