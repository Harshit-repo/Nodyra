import { createContext } from "react";
import type { DiffStatus } from "../types";
import type { WorkflowGraph, GraphNode, GraphEdge } from "../types";

export type ParamDiff = { key: string; before: unknown; after: unknown };

export type DiffResult = {
  added: string[];
  removed: string[];
  changed: string[];
  unchanged: string[];
  removedNodes: GraphNode[];
  addedEdges: string[];
  removedEdges: string[];
  changedParams: Record<string, ParamDiff[]>;
};

export const DiffContext = createContext<Map<string, DiffStatus>>(new Map());

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null || a === undefined || b === undefined) return false;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((item, i) => deepEqual(item, b[i]));
  }
  if (Array.isArray(a) || Array.isArray(b)) return false;
  if (typeof a === "object" && typeof b === "object") {
    const ao = a as Record<string, unknown>;
    const bo = b as Record<string, unknown>;
    const aKeys = Object.keys(ao).sort();
    const bKeys = Object.keys(bo).sort();
    if (!deepEqual(aKeys, bKeys)) return false;
    return aKeys.every((k) => deepEqual(ao[k], bo[k]));
  }
  return false;
}

function nodeParamsEqual(a: GraphNode, b: GraphNode): boolean {
  const ap = (a as unknown as Record<string, unknown>).params ?? {};
  const bp = (b as unknown as Record<string, unknown>).params ?? {};
  const aData = { ...(a as unknown as Record<string, unknown>) };
  const bData = { ...(b as unknown as Record<string, unknown>) };
  // Exclude position from comparison
  delete aData.position;
  delete bData.position;
  // Exclude params (compared separately for changedParams)
  delete aData.params;
  delete bData.params;
  return deepEqual(aData, bData) && deepEqual(ap, bp);
}

function changedParamsList(base: GraphNode, compare: GraphNode): ParamDiff[] {
  const bp = ((base as unknown as Record<string, unknown>).params ?? {}) as Record<string, unknown>;
  const cp = ((compare as unknown as Record<string, unknown>).params ?? {}) as Record<string, unknown>;
  const allKeys = new Set([...Object.keys(bp), ...Object.keys(cp)]);
  const diffs: ParamDiff[] = [];
  for (const key of allKeys) {
    if (!deepEqual(bp[key], cp[key])) {
      diffs.push({ key, before: bp[key], after: cp[key] });
    }
  }
  // Check non-param data fields excluding position
  const baseData = { ...(base as unknown as Record<string, unknown>) };
  const compareData = { ...(compare as unknown as Record<string, unknown>) };
  for (const field of ["position", "params", "id"]) {
    delete baseData[field];
    delete compareData[field];
  }
  for (const key of new Set([...Object.keys(baseData), ...Object.keys(compareData)])) {
    if (!deepEqual(baseData[key], compareData[key])) {
      diffs.push({ key, before: baseData[key], after: compareData[key] });
    }
  }
  return diffs;
}

export function diffWorkflowGraphs(base: WorkflowGraph, compare: WorkflowGraph): DiffResult {
  const baseNodes: GraphNode[] = base?.nodes ?? [];
  const compareNodes: GraphNode[] = compare?.nodes ?? [];
  const baseEdges: GraphEdge[] = base?.edges ?? [];
  const compareEdges: GraphEdge[] = compare?.edges ?? [];

  const baseMap = new Map(baseNodes.map((n) => [n.id, n]));
  const compareMap = new Map(compareNodes.map((n) => [n.id, n]));

  const added: string[] = [];
  const removed: string[] = [];
  const changed: string[] = [];
  const unchanged: string[] = [];
  const removedNodes: GraphNode[] = [];
  const changedParams: Record<string, ParamDiff[]> = {};

  for (const [id, bn] of baseMap) {
    const cn = compareMap.get(id);
    if (!cn) {
      removed.push(id);
      removedNodes.push(bn);
    } else if (bn.type !== cn.type) {
      // Type change = remove + add
      removed.push(id);
      removedNodes.push(bn);
      added.push(id);
    } else if (!nodeParamsEqual(bn, cn)) {
      changed.push(id);
      changedParams[id] = changedParamsList(bn, cn);
    } else {
      unchanged.push(id);
    }
  }

  for (const [id] of compareMap) {
    if (!baseMap.has(id)) {
      added.push(id);
    }
  }

  const baseEdgeIds = new Set(baseEdges.map((e) => e.id));
  const compareEdgeIds = new Set(compareEdges.map((e) => e.id));
  const addedEdges = [...compareEdgeIds].filter((id) => !baseEdgeIds.has(id));
  const removedEdges = [...baseEdgeIds].filter((id) => !compareEdgeIds.has(id));

  return {
    added,
    removed,
    changed,
    unchanged,
    removedNodes,
    addedEdges,
    removedEdges,
    changedParams,
  };
}
