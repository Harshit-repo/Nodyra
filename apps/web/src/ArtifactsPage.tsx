import {
  CaretDown,
  DownloadSimple,
  FileArrowUp,
  MagnifyingGlass,
  PlayCircle,
  ShieldCheck,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api, errorMessage, uploadArtifact } from "./api";
import { recordActivationEvent } from "./activation";
import {
  artifactDownloadUrl,
  artifactInlineUrl,
  artifactMediaKind,
  formatBytes,
  type ArtifactRef,
} from "./editor/artifactValues";
import { SkeletonRows } from "./Skeleton";
import type { ArtifactInfo, ArtifactLineage } from "./types";
import { formatDateTime, formatNumber, t } from "./i18n";

const PAGE_SIZE = 50;
const PREVIEW_ROWS = 5;
const PREVIEW_COLUMNS = 4;

const KIND_OPTIONS = ["table", "binary", "image", "report", "dataset"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function formatCreatedAt(value: string): string {
  return formatDateTime(value);
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

function ArtifactLineagePanel({ artifact }: { artifact: ArtifactInfo }) {
  const lineageQuery = useQuery({
    queryKey: ["artifact-lineage", artifact.id],
    queryFn: () => api.getArtifactLineage(artifact.id),
    staleTime: 60_000,
  });

  if (lineageQuery.isLoading) {
    return <p className="artifact-lineage-status" role="status">Loading provenance...</p>;
  }
  if (lineageQuery.isError) {
    return (
      <div className="artifact-lineage-status artifact-lineage-error" role="alert">
        <span>Could not load provenance. {errorMessage(lineageQuery.error)}</span>
        <button className="btn btn-sm" type="button" onClick={() => void lineageQuery.refetch()}>
          Retry
        </button>
      </div>
    );
  }

  const lineage = lineageQuery.data as ArtifactLineage;
  const producer = lineage.producer;
  const schemaSummary = lineage.schema
    ? JSON.stringify(lineage.schema, null, 2)
    : "No schema was recorded";
  const integritySummary = lineage.storage.integrity
    ? JSON.stringify(lineage.storage.integrity)
    : "Checksum is the available integrity evidence";

  return (
    <section className="artifact-lineage-panel" aria-label={`${artifact.name} provenance`}>
      <dl className="artifact-lineage-grid">
        <div>
          <dt>Producer</dt>
          <dd>
            {producer.workflow_id ? (
              <Link to={`/workflows/${producer.workflow_id}`}>
                {producer.workflow_name || shortId(producer.workflow_id)}
              </Link>
            ) : "Browser upload"}
            {producer.node_id && <small>Node {shortId(producer.node_id)}</small>}
          </dd>
        </div>
        <div>
          <dt>Run</dt>
          <dd>{producer.run_id ? <code>{shortId(producer.run_id)}</code> : "Not run-produced"}</dd>
        </div>
        <div>
          <dt>Storage</dt>
          <dd>{lineage.storage.backend}<small>Encryption: {lineage.storage.encryption_status.replaceAll("_", " ")}</small></dd>
        </div>
        <div>
          <dt>Retention</dt>
          <dd>{lineage.retention_deadline ? formatCreatedAt(lineage.retention_deadline) : "No expiry recorded"}</dd>
        </div>
        <div>
          <dt>Integrity</dt>
          <dd>{lineage.checksum_sha256 ? <code title={lineage.checksum_sha256}>sha256:{shortChecksum(lineage.checksum_sha256)}</code> : "No checksum recorded"}<small>{integritySummary}</small></dd>
        </div>
        <div>
          <dt>Consumers</dt>
          <dd>{lineage.downstream_consumers.length.toLocaleString()} recorded</dd>
        </div>
      </dl>
      <details className="artifact-lineage-schema">
        <summary>Recorded schema</summary>
        <pre>{schemaSummary}</pre>
      </details>
    </section>
  );
}

export function ArtifactsPage() {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [page, setPage] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [openLineageId, setOpenLineageId] = useState<string | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const inspectionRecordedRef = useRef(false);

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

  useEffect(() => {
    if (artifacts.length === 0 || inspectionRecordedRef.current) return;
    inspectionRecordedRef.current = true;
    recordActivationEvent("output_inspected");
  }, [artifacts.length]);

  async function upload(file: File | undefined): Promise<void> {
    if (!file || uploading) return;
    setUploading(true);
    setUploadError("");
    try {
      await uploadArtifact(file);
      await artifactsQuery.refetch();
      recordActivationEvent("output_inspected");
    } catch (error) {
      setUploadError(errorMessage(error));
    } finally {
      setUploading(false);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  }

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
        {uploadError && (
          <p className="error-text" role="alert">
            Could not upload artifact. {uploadError}
          </p>
        )}

        {artifactsQuery.isLoading ? (
          <div className="artifact-browser-table" aria-label="Loading artifacts">
            <SkeletonRows count={8} />
          </div>
        ) : artifacts.length === 0 ? (
          debouncedQuery || kind !== "all" ? (
            <div className="empty-state">
              <h2>No matching artifacts</h2>
              <p className="muted">Try a different name or remove the kind filter.</p>
              <button
                className="btn"
                type="button"
                onClick={() => {
                  setQuery("");
                  setDebouncedQuery("");
                  setKind("all");
                }}
              >
                Clear filters
              </button>
            </div>
          ) : (
            <section className="artifact-empty" aria-labelledby="artifact-empty-title">
              <div className="artifact-empty-copy">
                <span className="artifact-empty-icon" aria-hidden="true">
                  <ShieldCheck size={25} />
                </span>
                <div>
                  <h2 id="artifact-empty-title">Create your first inspectable artifact</h2>
                  <p>
                    Run the credential-free data template or upload a supported file.
                    Nodyra records size, checksum, producer, and retention metadata.
                  </p>
                </div>
              </div>
              <div className="artifact-empty-actions">
                <Link className="btn btn-primary" to="/?starter=datasetref-filter-export">
                  <PlayCircle size={17} weight="fill" aria-hidden="true" />
                  Run data template
                </Link>
                <button
                  className="btn"
                  type="button"
                  disabled={uploading}
                  onClick={() => uploadRef.current?.click()}
                >
                  <FileArrowUp size={17} aria-hidden="true" />
                  {uploading ? "Uploading…" : "Upload artifact"}
                </button>
                <input
                  ref={uploadRef}
                  type="file"
                  hidden
                  onChange={(event) => void upload(event.target.files?.[0])}
                />
              </div>
              <dl className="artifact-empty-details">
                <div>
                  <dt>Supported uploads</dt>
                  <dd>CSV, JSON, Parquet, images, reports, and binary files</dd>
                </div>
                <div>
                  <dt>After creation</dt>
                  <dd>Preview, download, query, trace lineage, and review retention</dd>
                </div>
                <div>
                  <dt>Retention</dt>
                  <dd><Link to="/settings">Review storage and retention settings</Link></dd>
                </div>
              </dl>
            </section>
          )
        ) : (
          <>
            <div className="artifact-browser-table" role="table" aria-label="Artifacts">
              <div className="artifact-browser-row artifact-browser-row-head" role="row">
                <span role="columnheader">Name</span>
                <span role="columnheader">Kind</span>
                <span role="columnheader">Preview</span>
                <span role="columnheader">Size</span>
                <span role="columnheader">Run</span>
                <span role="columnheader">Created</span>
                <span role="columnheader">Actions</span>
              </div>
              {artifacts.map((artifact) => (
                <Fragment key={artifact.id}>
                <div className="artifact-browser-row" role="row">
                  <span className="artifact-browser-name" role="cell">
                    <strong>{artifact.name}</strong>
                    {artifact.checksum_sha256 && (
                      <code title={artifact.checksum_sha256}>
                        sha256:{shortChecksum(artifact.checksum_sha256)}
                      </code>
                    )}
                  </span>
                  <span role="cell">
                    <span className="artifact-browser-kind-chip">{artifact.kind}</span>
                  </span>
                  <span className="artifact-browser-preview" role="cell">
                    <ArtifactPreview artifact={artifact} />
                  </span>
                  <span role="cell">{formatBytes(artifact.size_bytes)}</span>
                  <span role="cell">
                    <code title={artifact.run_id ?? undefined}>{shortId(artifact.run_id)}</code>
                  </span>
                  <span role="cell">{formatCreatedAt(artifact.created_at)}</span>
                  <span role="cell">
                    <span className="artifact-browser-actions">
                      <button
                        className="btn btn-sm"
                        type="button"
                        aria-expanded={openLineageId === artifact.id}
                        aria-controls={`artifact-lineage-${artifact.id}`}
                        onClick={() => setOpenLineageId((current) => current === artifact.id ? null : artifact.id)}
                      >
                        <CaretDown size={14} aria-hidden="true" />
                        {t("artifact.lineage")}
                      </button>
                      <a
                        className="btn btn-sm"
                        href={artifactDownloadUrl(artifactRef(artifact))}
                        download
                        aria-label={`${t("artifact.download")} ${artifact.name}`}
                        onClick={() => recordActivationEvent("output_inspected")}
                      >
                        <DownloadSimple size={15} aria-hidden="true" />
                        <span className="sr-only">{t("artifact.download")}</span>
                      </a>
                    </span>
                  </span>
                </div>
                {openLineageId === artifact.id && (
                  <div id={`artifact-lineage-${artifact.id}`} className="artifact-lineage-row">
                    <ArtifactLineagePanel artifact={artifact} />
                  </div>
                )}
                </Fragment>
              ))}
            </div>
            <div className="artifact-browser-pagination" aria-label="Artifact pagination">
              <span>
                Showing {formatNumber(pageStart)}-{formatNumber(pageEnd)} of{" "}
                {formatNumber(total)}
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
