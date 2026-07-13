import { DownloadSimple, MagnifyingGlass } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, errorMessage } from "./api";
import {
  artifactDownloadUrl,
  artifactInlineUrl,
  artifactMediaKind,
  formatBytes,
  type ArtifactRef,
} from "./editor/artifactValues";
import { SkeletonRows } from "./Skeleton";
import type { ArtifactInfo } from "./types";

const PAGE_SIZE = 50;
const PREVIEW_ROWS = 5;
const PREVIEW_COLUMNS = 4;

const KIND_OPTIONS = ["table", "binary", "image", "report", "dataset"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function formatCreatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function artifactRef(artifact: ArtifactInfo): ArtifactRef {
  return {
    __nodyra_artifact__: true,
    version: 1,
    artifact_id: artifact.id,
    run_id: artifact.run_id ?? undefined,
    node_id: artifact.node_id ?? undefined,
    name: artifact.name,
    kind: artifact.kind,
    content_type: artifact.content_type,
    size_bytes: artifact.size_bytes,
    metadata: artifact.metadata,
    preview: artifact.preview,
  };
}

function shortId(value: string | null | undefined): string {
  if (!value) return "-";
  return value.length > 12 ? `${value.slice(0, 8)}...` : value;
}

function shortChecksum(value: string | null | undefined): string | null {
  if (!value) return null;
  return value.length > 16 ? `${value.slice(0, 12)}...` : value;
}

function isCsvArtifact(artifact: ArtifactInfo): boolean {
  const contentType = artifact.content_type.toLowerCase();
  const name = artifact.name.toLowerCase();
  return (
    artifact.kind === "table" ||
    contentType.includes("csv") ||
    name.endsWith(".csv")
  );
}

function isQueryableDataset(artifact: ArtifactInfo): boolean {
  const contentType = artifact.content_type.toLowerCase();
  const name = artifact.name.toLowerCase();
  return (
    artifact.kind === "dataset" ||
    contentType.includes("parquet") ||
    name.endsWith(".parquet")
  );
}

function previewRows(preview: unknown): Record<string, unknown>[] {
  if (Array.isArray(preview)) {
    return preview.filter(isRecord);
  }
  if (isRecord(preview)) {
    for (const key of ["rows", "records", "preview", "data", "items"]) {
      const rows = preview[key];
      if (Array.isArray(rows)) return rows.filter(isRecord);
    }
  }
  return [];
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function ArtifactTablePreview({
  rows,
  caption,
}: {
  rows: Record<string, unknown>[];
  caption: string;
}) {
  const visibleRows = rows.slice(0, PREVIEW_ROWS);
  const columns = Array.from(
    new Set(visibleRows.flatMap((row) => Object.keys(row))),
  ).slice(0, PREVIEW_COLUMNS);
  if (visibleRows.length === 0 || columns.length === 0) {
    return <span className="artifact-browser-preview-muted">No preview rows</span>;
  }
  return (
    <div className="artifact-browser-preview-table" role="table" aria-label={caption}>
      <div className="artifact-browser-preview-table-head" role="row">
        {columns.map((column) => (
          <span role="columnheader" key={column} title={column}>
            {column}
          </span>
        ))}
      </div>
      {visibleRows.map((row, index) => (
        <div className="artifact-browser-preview-table-row" role="row" key={index}>
          {columns.map((column) => (
            <span role="cell" key={column} title={formatCell(row[column])}>
              {formatCell(row[column])}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

function DatasetArtifactPreview({ artifact }: { artifact: ArtifactInfo }) {
  const [reloadToken, setReloadToken] = useState(0);
  const query = useQuery({
    queryKey: ["artifact-preview", artifact.id, reloadToken],
    queryFn: () =>
      api.queryDataset(
        artifact.id,
        `SELECT * FROM dataset LIMIT ${PREVIEW_ROWS + 1} OFFSET 0`,
        PREVIEW_ROWS,
      ),
    enabled: isQueryableDataset(artifact),
    staleTime: 60_000,
  });

  if (query.isLoading) {
    return <span className="artifact-browser-preview-muted">Loading table...</span>;
  }
  if (query.isError) {
    return (
      <span className="artifact-browser-preview-error">
        {errorMessage(query.error)}
        <button type="button" onClick={() => setReloadToken((value) => value + 1)}>
          Retry
        </button>
      </span>
    );
  }
  return (
    <ArtifactTablePreview
      rows={query.data?.rows ?? []}
      caption={`${artifact.name} dataset preview`}
    />
  );
}

function ArtifactPreview({ artifact }: { artifact: ArtifactInfo }) {
  const ref = artifactRef(artifact);
  if (artifactMediaKind(ref) === "image") {
    return (
      <img
        className="artifact-browser-thumbnail"
        src={artifactInlineUrl(ref)}
        alt={`${artifact.name} thumbnail`}
        loading="lazy"
      />
    );
  }

  if (isCsvArtifact(artifact)) {
    return (
      <ArtifactTablePreview
        rows={previewRows(artifact.preview)}
        caption={`${artifact.name} CSV preview`}
      />
    );
  }

  if (isQueryableDataset(artifact)) {
    return <DatasetArtifactPreview artifact={artifact} />;
  }

  return <span className="artifact-browser-preview-muted">No preview</span>;
}

export function ArtifactsPage() {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [page, setPage] = useState(0);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => window.clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    setPage(0);
  }, [debouncedQuery, kind]);

  const params = useMemo(
    () => ({
      q: debouncedQuery || undefined,
      kind: kind === "all" ? undefined : kind,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [debouncedQuery, kind, page],
  );

  const artifactsQuery = useQuery({
    queryKey: ["artifacts", params],
    queryFn: () => api.listArtifacts(params),
  });

  const artifacts = artifactsQuery.data?.items ?? [];
  const total = artifactsQuery.data?.total ?? 0;
  const pageStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const pageEnd = Math.min(total, page * PAGE_SIZE + artifacts.length);
  const hasPrevious = page > 0;
  const hasNext = pageEnd < total;

  return (
    <div className="home artifacts-page">
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Artifacts
            <span className="home-count">{artifactsQuery.data ? total : "..."}</span>
          </h1>
        </div>

        <div className="artifact-browser-filters">
          <label className="artifact-browser-search">
            <MagnifyingGlass size={16} aria-hidden="true" />
            <input
              className="field-input"
              value={query}
              placeholder="Search artifact name, kind, run, node..."
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Search artifacts"
            />
          </label>
          <label className="artifact-browser-kind">
            <span>Kind</span>
            <select
              className="field-input"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              aria-label="Filter artifacts by kind"
            >
              <option value="all">All kinds</option>
              {KIND_OPTIONS.map((option) => (
                <option value={option} key={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>

        {artifactsQuery.isError && (
          <p className="error-text" role="alert">
            {errorMessage(artifactsQuery.error)}
          </p>
        )}

        {artifactsQuery.isLoading ? (
          <div className="artifact-browser-table" aria-label="Loading artifacts">
            <SkeletonRows count={8} />
          </div>
        ) : artifacts.length === 0 ? (
          <div className="empty-state">
            <h2>No artifacts found</h2>
            <p className="muted">
              Artifact outputs from runs and uploads will appear here.
            </p>
          </div>
        ) : (
          <>
            <div className="artifact-browser-table" role="table" aria-label="Artifacts">
              <div className="artifact-browser-row artifact-browser-row-head" role="row">
                <span>Name</span>
                <span>Kind</span>
                <span>Preview</span>
                <span>Size</span>
                <span>Run</span>
                <span>Created</span>
                <span>Download</span>
              </div>
              {artifacts.map((artifact) => (
                <div className="artifact-browser-row" role="row" key={artifact.id}>
                  <span className="artifact-browser-name">
                    <strong>{artifact.name}</strong>
                    {artifact.checksum_sha256 && (
                      <code title={artifact.checksum_sha256}>
                        sha256:{shortChecksum(artifact.checksum_sha256)}
                      </code>
                    )}
                  </span>
                  <span>
                    <span className="artifact-browser-kind-chip">{artifact.kind}</span>
                  </span>
                  <span className="artifact-browser-preview">
                    <ArtifactPreview artifact={artifact} />
                  </span>
                  <span>{formatBytes(artifact.size_bytes)}</span>
                  <span>
                    <code title={artifact.run_id ?? undefined}>{shortId(artifact.run_id)}</code>
                  </span>
                  <span>{formatCreatedAt(artifact.created_at)}</span>
                  <span>
                    <a
                      className="btn btn-sm"
                      href={artifactDownloadUrl(artifactRef(artifact))}
                      download
                    >
                      <DownloadSimple size={15} aria-hidden="true" />
                      Download
                    </a>
                  </span>
                </div>
              ))}
            </div>
            <div className="artifact-browser-pagination" aria-label="Artifact pagination">
              <span>
                Showing {pageStart.toLocaleString()}-{pageEnd.toLocaleString()} of{" "}
                {total.toLocaleString()}
              </span>
              <div>
                <button
                  className="btn btn-sm"
                  type="button"
                  disabled={!hasPrevious}
                  onClick={() => setPage((value) => Math.max(0, value - 1))}
                >
                  Previous
                </button>
                <button
                  className="btn btn-sm"
                  type="button"
                  disabled={!hasNext}
                  onClick={() => setPage((value) => value + 1)}
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
