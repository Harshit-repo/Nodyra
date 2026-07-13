import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DataPanel } from "./DataPanel";

const apiMocks = vi.hoisted(() => ({
  queryDataset: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      queryDataset: apiMocks.queryDataset,
    },
  };
});

beforeEach(() => {
  apiMocks.queryDataset.mockReset();
});

describe("DatasetGrid", () => {
  it("streams DatasetRef rows from the artifact query API", async () => {
    apiMocks.queryDataset
      .mockResolvedValueOnce({
        columns: [
          { name: "id", type: "BIGINT" },
          { name: "name", type: "VARCHAR" },
        ],
        rows: [{ id: 1, name: "Ada" }],
        row_count: 1,
        truncated: true,
        elapsed_ms: 4,
      })
      .mockResolvedValueOnce({
        columns: [
          { name: "id", type: "BIGINT" },
          { name: "name", type: "VARCHAR" },
        ],
        rows: [{ id: 101, name: "Grace" }],
        row_count: 1,
        truncated: true,
        elapsed_ms: 5,
      });

    render(
      <DataPanel
        title="Output"
        data={{
          __nodyra_dataset__: true,
          version: 1,
          dataset_id: "ds-1",
          format: "parquet",
          row_count: 100000,
          column_count: 2,
          schema: [
            { name: "id", type: "BIGINT" },
            { name: "name", type: "VARCHAR" },
          ],
          preview: [],
          preview_truncated: true,
          artifact: {
            __nodyra_artifact__: true,
            version: 1,
            artifact_id: "artifact-1",
            name: "dataset.parquet",
            kind: "dataset",
            content_type: "application/parquet",
            size_bytes: 2048,
          },
        }}
      />,
    );

    expect(await screen.findByText("Ada")).toBeTruthy();
    await waitFor(() =>
      expect(apiMocks.queryDataset).toHaveBeenCalledWith(
        "artifact-1",
        "SELECT * FROM dataset LIMIT 101 OFFSET 0",
        100,
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByText("Grace")).toBeTruthy();
    await waitFor(() =>
      expect(apiMocks.queryDataset).toHaveBeenCalledWith(
        "artifact-1",
        "SELECT * FROM dataset LIMIT 101 OFFSET 100",
        100,
      ),
    );
    expect(screen.getByText(/Rows 101-101 of 100,000/)).toBeTruthy();
  });
});
