import type { Connection, Edge } from "@xyflow/react";

import type { NodeManifest, PortSpec } from "../types";
import type { NoodleNode } from "./store";
import { isMetaBar, META_BAR_INPUT_ID, META_BAR_OUTPUT_ID } from "./store/drillSlice";

export type PortDataKind = NonNullable<PortSpec["data_kind"]>;

export interface ConnectionCheck {
  ok: boolean;
  severity: "ok" | "warning" | "error";
  message: string;
  quickFixId?: "records_to_dataset" | "dataset_to_records" | "duckdb_sql";
}

function portKind(port: PortSpec | undefined): PortDataKind {
  return port?.data_kind ?? "any";
}

const AI_PORT_KINDS = new Set<PortDataKind>([
  "ai_language_model",
  "ai_embedding_model",
  "ai_memory",
  "ai_tool",
  "ai_output_parser",
  "ai_retriever",
  "ai_vector_store",
  "ai_document_loader",
  "ai_guardrail",
  "ai_subagent",
]);

export function findInputPort(manifest: NodeManifest, name: string | null | undefined): PortSpec | undefined {
  const wanted = name ?? "input";
  return manifest.inputs.find((port) => port.name === wanted) ?? manifest.inputs[0];
}

export function findOutputPort(manifest: NodeManifest, name: string | null | undefined): PortSpec | undefined {
  const wanted = name ?? "main";
  return manifest.outputs.find((port) => port.name === wanted) ?? manifest.outputs[0];
}

function kindLabel(kind: PortDataKind): string {
  if (kind === "dataset") return "DatasetRef";
  if (kind === "artifact") return "artifact";
  if (kind === "file") return "file";
  if (kind === "control") return "control";
  if (kind === "main") return "main data";
  if (kind === "ai_language_model") return "AI language model";
  if (kind === "ai_embedding_model") return "AI embedding model";
  if (kind === "ai_memory") return "AI memory";
  if (kind === "ai_tool") return "AI tool";
  if (kind === "ai_output_parser") return "AI output parser";
  if (kind === "ai_retriever") return "AI retriever";
  if (kind === "ai_vector_store") return "AI vector store";
  if (kind === "ai_document_loader") return "AI document loader";
  if (kind === "ai_guardrail") return "AI guardrail";
  if (kind === "ai_subagent") return "AI Sub-Agent";
  return "any data";
}

export function checkConnectionKinds(
  source: NodeManifest,
  sourceHandle: string | null | undefined,
  target: NodeManifest,
  targetHandle: string | null | undefined,
  sourceKindOverride?: PortDataKind,
): ConnectionCheck {
  const sourceKind = sourceKindOverride ?? portKind(findOutputPort(source, sourceHandle));
  const targetKind = portKind(findInputPort(target, targetHandle));

  if (sourceKind === targetKind) {
    return {
      ok: true,
      severity: "ok",
      message:
        sourceKind === "dataset"
          ? "DatasetRef connection: schema + preview stay artifact-backed downstream."
          : "Compatible port kinds.",
    };
  }

  if (AI_PORT_KINDS.has(sourceKind) || AI_PORT_KINDS.has(targetKind)) {
    return {
      ok: false,
      severity: "error",
      message: `Port kind mismatch: ${kindLabel(sourceKind)} cannot connect to ${kindLabel(targetKind)}.`,
    };
  }

  if (sourceKind === "dataset" && targetKind === "dataset") {
    return {
      ok: true,
      severity: "ok",
      message: "DatasetRef connection: schema + preview stay artifact-backed downstream.",
    };
  }

  // DatasetRef ports are intentionally strict even when the other side is
  // declared as "any". In practice, "any" usually means inline JSON/records,
  // while DatasetRefs are artifact-backed table handles. Letting those wires
  // through creates runtime surprises, so require an explicit converter.
  if (targetKind === "dataset") {
    return {
      ok: false,
      severity: "error",
      message: `This input expects a DatasetRef, but the source provides ${kindLabel(sourceKind)}. Add a Records To Dataset node upstream.`,
      quickFixId: "records_to_dataset",
    };
  }

  if (sourceKind === "dataset") {
    return {
      ok: false,
      severity: "error",
      message: `This output is a DatasetRef, but the target expects ${kindLabel(targetKind)}. Add Dataset To Records or DuckDB SQL first.`,
      quickFixId: targetKind === "artifact" || targetKind === "file" ? "duckdb_sql" : "dataset_to_records",
    };
  }

  if (
    sourceKind === "any" ||
    targetKind === "any" ||
    sourceKind === "main" ||
    targetKind === "main"
  ) {
    return { ok: true, severity: "ok", message: "Compatible port kinds." };
  }

  return {
    ok: false,
    severity: "error",
    message: `Port kind mismatch: ${kindLabel(sourceKind)} cannot connect to ${kindLabel(targetKind)}.`,
  };
}

export function validateConnection(
  nodes: NoodleNode[],
  connection: Connection,
): ConnectionCheck {
  const sourceNode = nodes.find((node) => node.id === connection.source);
  const targetNode = nodes.find((node) => node.id === connection.target);
  return validateResolvedConnection(sourceNode, targetNode, connection);
}

function validateResolvedConnection(
  sourceNode: NoodleNode | undefined,
  targetNode: NoodleNode | undefined,
  connection: Connection,
): ConnectionCheck {
  if (!sourceNode || !targetNode) {
    return { ok: false, severity: "error", message: "Connection endpoint is missing." };
  }

  // Bar ports are wildcards: input-bar source handles and output-bar target
  // handles accept any data kind. Prevent output-bar from acting as a source
  // and input-bar from acting as a target (wrong direction).
  if (sourceNode && isMetaBar(sourceNode)) {
    return sourceNode.id === META_BAR_INPUT_ID
      ? { ok: true, severity: "ok", message: "Input bar — any internal target." }
      : { ok: false, severity: "error", message: "Output bar cannot serve as a connection source." };
  }
  if (targetNode && isMetaBar(targetNode)) {
    return targetNode.id === META_BAR_OUTPUT_ID
      ? { ok: true, severity: "ok", message: "Output bar — any internal source." }
      : { ok: false, severity: "error", message: "Input bar cannot serve as a connection target." };
  }

  // Reject cross-boundary connections between parent and body nodes.
  const sourceParent = sourceNode.parentId ?? null;
  const targetParent = targetNode.parentId ?? null;
  if (sourceParent !== targetParent) {
    return {
      ok: false,
      severity: "error",
      message:
        "Nodes inside a Map Group cannot connect to nodes outside it. " +
        "Use the Map Group's input/output handles instead.",
    };
  }

  // A tool-mode node exposes a single `tool` output of kind ai_tool.
  const sourceKindOverride: PortDataKind | undefined =
    sourceNode.data.toolMode && connection.sourceHandle === "tool"
      ? "ai_tool"
      : undefined;
  return checkConnectionKinds(
    sourceNode.data.manifest,
    connection.sourceHandle,
    targetNode.data.manifest,
    connection.targetHandle,
    sourceKindOverride,
  );
}

export function datasetConnectionIssues(nodes: NoodleNode[], edges: Edge[]): Array<{
  edge: Edge;
  check: ConnectionCheck;
}> {
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  return edges
    .map((edge) => ({
      edge,
      check: validateResolvedConnection(
        nodesById.get(edge.source),
        nodesById.get(edge.target),
        {
        source: edge.source,
        sourceHandle: edge.sourceHandle ?? null,
        target: edge.target,
        targetHandle: edge.targetHandle ?? null,
        },
      ),
    }))
    .filter((item) => !item.check.ok);
}
