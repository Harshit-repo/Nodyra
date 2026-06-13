import type { Edge } from "@xyflow/react";
import type { NoodleNode } from "../store";

export interface UpstreamField {
  path: string;
  type: "str" | "int" | "float" | "bool" | "list" | "obj" | "null" | "unknown";
  valuePreview: string;
  expression: string;
  isExpandable: boolean;
}

export interface UpstreamNode {
  id: string;
  label: string;
  fields: UpstreamField[];
}

export function inferType(value: unknown): UpstreamField["type"] {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return "bool";
  if (typeof value === "number") return Number.isInteger(value) ? "int" : "float";
  if (typeof value === "string") return "str";
  if (Array.isArray(value)) return "list";
  if (typeof value === "object") return "obj";
  return "unknown";
}

export function formatValuePreview(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") {
    return `"${value.slice(0, 12).trimEnd()}${value.length > 12 ? "…" : ""}"`;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `[${value.length} items]`;
  if (typeof value === "object") {
    const keys = Object.keys(value as object);
    return `{${keys.slice(0, 2).join(", ")}${keys.length > 2 ? "…" : ""}}`;
  }
  return "";
}

export function buildNodeExpression(nodeId: string, path: string): string {
  return `{{ $node["${nodeId}"].main.${path} }}`;
}

export function flattenOutputFields(
  value: unknown,
  nodeId: string,
  path = "",
  depth = 0,
): UpstreamField[] {
  if (depth >= 3) return [];
  const fields: UpstreamField[] = [];

  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
      const fieldPath = path ? `${path}.${key}` : key;
      const isExpandable = val !== null && typeof val === "object";
      fields.push({
        path: fieldPath,
        type: inferType(val),
        valuePreview: formatValuePreview(val),
        expression: buildNodeExpression(nodeId, fieldPath),
        isExpandable,
      });
      if (isExpandable && depth < 2) {
        fields.push(...flattenOutputFields(val, nodeId, fieldPath, depth + 1));
      }
    }
  } else if (Array.isArray(value)) {
    const items = value.slice(0, 5);
    for (let i = 0; i < items.length; i++) {
      const fieldPath = path ? `${path}[${i}]` : `[${i}]`;
      const val = items[i];
      const isExpandable = val !== null && typeof val === "object";
      fields.push({
        path: fieldPath,
        type: inferType(val),
        valuePreview: formatValuePreview(val),
        expression: buildNodeExpression(nodeId, fieldPath),
        isExpandable,
      });
    }
  }

  return fields;
}

export function getUpstreamNodeIds(nodeId: string, edges: Edge[]): string[] {
  const result: string[] = [];
  const visited = new Set<string>([nodeId]);
  const queue = [nodeId];
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const edge of edges) {
      if (edge.target === current && !visited.has(edge.source)) {
        visited.add(edge.source);
        result.push(edge.source);
        queue.push(edge.source);
      }
    }
  }
  return result;
}

export function getUpstreamNodes(
  nodeId: string,
  nodes: NoodleNode[],
  edges: Edge[],
  runOutputs: Record<string, unknown>,
): UpstreamNode[] {
  const upstreamIds = getUpstreamNodeIds(nodeId, edges);
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const result: UpstreamNode[] = [];

  for (const id of upstreamIds) {
    const node = nodeById.get(id);
    if (!node) continue;
    const label = node.data.label ?? node.data.manifest.name;
    const raw = runOutputs[id];
    const mainOutput =
      raw && typeof raw === "object"
        ? (raw as Record<string, unknown>)["main"]
        : undefined;
    const fields =
      mainOutput !== undefined ? flattenOutputFields(mainOutput, id) : [];
    result.push({ id, label, fields });
  }

  return result;
}

export function searchUpstreamFields(
  nodes: UpstreamNode[],
  query: string,
): UpstreamNode[] {
  if (!query.trim()) return nodes;
  const lower = query.toLowerCase();
  return nodes
    .map((n) => ({
      ...n,
      fields: n.fields.filter(
        (f) =>
          f.path.toLowerCase().includes(lower) ||
          f.valuePreview.toLowerCase().includes(lower),
      ),
    }))
    .filter((n) => n.fields.length > 0);
}
