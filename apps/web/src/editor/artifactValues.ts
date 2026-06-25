import { safeGetItem } from "../safeStorage";

export interface ArtifactRef {
  __noodle_artifact__: true;
  version: number;
  artifact_id: string;
  run_id?: string;
  node_id?: string;
  name: string;
  kind: string;
  content_type: string;
  size_bytes: number;
  metadata?: Record<string, unknown>;
  preview?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function asArtifactRef(value: unknown): ArtifactRef | null {
  if (!isRecord(value)) return null;
  if (value.__noodle_artifact__ !== true || value.version !== 1) return null;
  if (typeof value.artifact_id !== "string") return null;
  return value as unknown as ArtifactRef;
}

export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "0 B";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  let unit = units[0];
  for (let i = 1; i < units.length && size >= 1024; i += 1) {
    size /= 1024;
    unit = units[i];
  }
  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${unit}`;
}

export function artifactSummary(ref: ArtifactRef): string {
  return `${ref.kind || "file"} · ${formatBytes(ref.size_bytes)}`;
}

export function artifactDownloadUrl(ref: ArtifactRef): string {
  const token = safeGetItem("noodle_token");
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return `/api/artifacts/${encodeURIComponent(ref.artifact_id)}/download${qs}`;
}

/** Download URL that asks the server for an ``inline`` Content-Disposition so
 *  the browser renders images/PDFs/media in-page instead of downloading. */
export function artifactInlineUrl(ref: ArtifactRef): string {
  const token = safeGetItem("noodle_token");
  const params = new URLSearchParams({ inline: "1" });
  if (token) params.set("token", token);
  return `/api/artifacts/${encodeURIComponent(ref.artifact_id)}/download?${params.toString()}`;
}

export type ArtifactMediaKind =
  | "image"
  | "pdf"
  | "audio"
  | "video"
  | "text"
  | "none";

/** Classify an artifact by content type / filename into a previewable kind. */
export function artifactMediaKind(ref: ArtifactRef): ArtifactMediaKind {
  const ct = (ref.content_type || "").toLowerCase();
  const name = (ref.name || "").toLowerCase();
  if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/.test(name))
    return "image";
  if (ct === "application/pdf" || name.endsWith(".pdf")) return "pdf";
  if (ct.startsWith("audio/") || /\.(mp3|wav|ogg|m4a|flac)$/.test(name))
    return "audio";
  if (ct.startsWith("video/") || /\.(mp4|webm|mov|mkv)$/.test(name))
    return "video";
  if (
    ct.startsWith("text/") ||
    ct.includes("json") ||
    ct.includes("xml") ||
    ct.includes("csv") ||
    /\.(txt|md|json|xml|csv|log|ya?ml|html?)$/.test(name)
  )
    return "text";
  return "none";
}
