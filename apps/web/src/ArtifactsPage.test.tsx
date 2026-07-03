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
});
