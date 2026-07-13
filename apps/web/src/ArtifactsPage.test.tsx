import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { ArtifactsPage } from "./ArtifactsPage";
import type { ArtifactInfo } from "./types";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ArtifactsPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ArtifactsPage", () => {
  it("lists artifacts with checksum and download action", async () => {
    const artifact: ArtifactInfo = {
      id: "artifact-1",
      run_id: "run-123456789",
      node_id: "node-1",
      name: "report.csv",
      kind: "dataset",
      content_type: "text/csv",
      size_bytes: 2048,
      checksum_sha256: "abcdef1234567890abcdef1234567890",
      metadata: {},
      preview: null,
      created_at: "2026-07-03T02:00:00Z",
    };
    const listArtifacts = vi
      .spyOn(api, "listArtifacts")
      .mockResolvedValue({ items: [artifact], total: 1 });

    renderPage();

    expect(await screen.findByText("report.csv")).toBeTruthy();
    expect(screen.getAllByText("dataset").length).toBeGreaterThan(0);
    expect(screen.getByText("2.0 KB")).toBeTruthy();
    expect(screen.getByText(/sha256:abcdef123456/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Download/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/api/artifacts/artifact-1/download"),
    );
    await waitFor(() => {
      expect(listArtifacts).toHaveBeenCalledWith({
        q: undefined,
        kind: undefined,
        limit: 50,
        offset: 0,
      });
    });
  });

  it("renders CSV table previews and image thumbnails", async () => {
    const artifacts: ArtifactInfo[] = [
      {
        id: "csv-1",
        run_id: "run-1",
        node_id: "node-1",
        name: "customers.csv",
        kind: "table",
        content_type: "text/csv",
        size_bytes: 1024,
        checksum_sha256: null,
        metadata: {},
        preview: [
          { name: "Ada", city: "London" },
          { name: "Grace", city: "Arlington" },
        ],
        created_at: "2026-07-03T02:00:00Z",
      },
      {
        id: "image-1",
        run_id: "run-1",
        node_id: "chart",
        name: "chart.webp",
        kind: "image",
        content_type: "image/webp",
        size_bytes: 4096,
        checksum_sha256: null,
        metadata: {},
        preview: null,
        created_at: "2026-07-03T02:01:00Z",
      },
    ];
    vi.spyOn(api, "listArtifacts").mockResolvedValue({ items: artifacts, total: 2 });

    renderPage();

    expect(await screen.findByText("customers.csv")).toBeTruthy();
    expect(screen.getByText("Ada")).toBeTruthy();
    expect(screen.getByText("London")).toBeTruthy();
    const thumbnail = screen.getByAltText("chart.webp thumbnail");
    expect(thumbnail).toHaveAttribute(
      "src",
      expect.stringContaining("/api/artifacts/image-1/download?inline=1"),
    );
  });

  it("loads dataset artifact previews from the query API", async () => {
    const artifact: ArtifactInfo = {
      id: "dataset-1",
      run_id: "run-1",
      node_id: "node-1",
      name: "rows.parquet",
      kind: "dataset",
      content_type: "application/vnd.apache.parquet",
      size_bytes: 8192,
      checksum_sha256: null,
      metadata: {},
      preview: null,
      created_at: "2026-07-03T02:00:00Z",
    };
    vi.spyOn(api, "listArtifacts").mockResolvedValue({ items: [artifact], total: 1 });
    const queryDataset = vi.spyOn(api, "queryDataset").mockResolvedValue({
      columns: [
        { name: "id", type: "BIGINT" },
        { name: "status", type: "VARCHAR" },
      ],
      rows: [{ id: 1, status: "ready" }],
      row_count: 1,
      truncated: false,
      elapsed_ms: 3,
    });

    renderPage();

    expect(await screen.findByText("rows.parquet")).toBeTruthy();
    expect(await screen.findByText("ready")).toBeTruthy();
    await waitFor(() =>
      expect(queryDataset).toHaveBeenCalledWith(
        "dataset-1",
        "SELECT * FROM dataset LIMIT 6 OFFSET 0",
        5,
      ),
    );
  });
});
