import { describe, expect, it } from "vitest";
import type { Connection } from "@xyflow/react";

import type { NodeManifest, PortSpec } from "../types";
import { useEditor } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}

function manifest(
  id: string,
  inputKind: PortSpec["data_kind"],
  outputKind: PortSpec["data_kind"],
): NodeManifest {
  return {
    id,
    name: id,
    category: "Data",
    version: "1",
    description: "",
    icon: null,
    inputs: [port("input", inputKind)],
    outputs: [port("main", outputKind)],
    params: [],
  };
}

const records = manifest("http_request", "any", "any");
const datasetSql = manifest("duckdb_sql", "dataset", "dataset");
const recordsToDataset = manifest("records_to_dataset", "any", "dataset");

function resetEditor(): void {
  useEditor.setState({
    manifests: [],
    manifestsById: {},
    nodes: [],
    edges: [],
    selectedId: null,
    dirty: false,
    runId: null,
    running: false,
    runStatus: {},
    runOutputs: {},
    runMeta: {},
    runError: null,
    workflowId: null,
    pinned: {},
    _past: [],
    _future: [],
  });
}

function loadDatasetMismatch(): Connection {
  const state = useEditor.getState();
  state.setManifests([records, datasetSql, recordsToDataset]);
  state.loadGraph({
    nodes: [
      {
        id: "source",
        type: "http_request",
        params: {},
        position: { x: 0, y: 0 },
        disabled: false,
        outputs_override: null,
        on_error: "stop",
        retry_on_fail: false,
        retries: 1,
        retry_wait_seconds: 0,
        retry_backoff: false,
        always_output_data: false,
        timeout_seconds: null,
      },
      {
        id: "sql",
        type: "duckdb_sql",
        params: {},
        position: { x: 400, y: 0 },
        disabled: false,
        outputs_override: null,
        on_error: "stop",
        retry_on_fail: false,
        retries: 1,
        retry_wait_seconds: 0,
        retry_backoff: false,
        always_output_data: false,
        timeout_seconds: null,
      },
    ],
    edges: [],
  });
  return { source: "source", sourceHandle: "main", target: "sql", targetHandle: "input" };
}

describe("editor DatasetRef wiring", () => {
  it("does not add an incompatible edge", () => {
    resetEditor();
    const connection = loadDatasetMismatch();

    const result = useEditor.getState().onConnect(connection);

    expect(result.ok).toBe(false);
    expect(result.quickFixId).toBe("records_to_dataset");
    expect(useEditor.getState().edges).toHaveLength(0);
  });

  it("inserts a quick-fix converter between incompatible nodes", () => {
    resetEditor();
    const connection = loadDatasetMismatch();

    const result = useEditor.getState().insertQuickFixNode("records_to_dataset", connection);
    const state = useEditor.getState();

    expect(result.ok).toBe(true);
    expect(state.nodes.map((node) => node.data.manifest.id)).toContain("records_to_dataset");
    expect(state.edges).toHaveLength(2);
    const helper = state.nodes.find((node) => node.data.manifest.id === "records_to_dataset");
    expect(helper).toBeTruthy();
    expect(state.edges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ source: "source", target: helper?.id, sourceHandle: "main", targetHandle: "input" }),
        expect.objectContaining({ source: helper?.id, target: "sql", sourceHandle: "main", targetHandle: "input" }),
      ]),
    );
  });
});
