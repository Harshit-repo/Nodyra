import type { ArtifactRef } from "./artifactValues";
import { asArtifactRef, formatBytes } from "./artifactValues";
import { safeGetItem } from "../safeStorage";

export interface DatasetSchemaColumn {
  name: string;
  type: string;
}

export interface DatasetRef {
  __noodle_dataset__: true;
  version: number;
  dataset_id: string;
  format: string;
  row_count: number | null;
  column_count: number | null;
  schema: DatasetSchemaColumn[];
  preview: Record<string, unknown>[];
  preview_truncated: boolean;
  artifact: ArtifactRef;
  metadata?: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function asDatasetRef(value: unknown): DatasetRef | null {
  if (!isRecord(value)) return null;
  if (value.__noodle_dataset__ !== true || value.version !== 1) return null;
  if (typeof value.dataset_id !== "string") return null;
  if (!asArtifactRef(value.artifact)) return null;
  return value as unknown as DatasetRef;
}

export function datasetSummary(ref: DatasetRef): string {
  const rows = ref.row_count == null ? "?" : ref.row_count.toLocaleString();
  const cols = ref.column_count ?? ref.schema?.length ?? 0;
  return `${rows} rows × ${cols} cols · ${formatBytes(ref.artifact.size_bytes)}`;
}

export function datasetDownloadUrl(ref: DatasetRef): string {
  const token = safeGetItem("noodle_token");
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return `/api/artifacts/${encodeURIComponent(ref.artifact.artifact_id)}/download${qs}`;
}
