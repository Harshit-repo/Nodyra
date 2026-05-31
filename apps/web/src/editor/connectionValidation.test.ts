import { describe, expect, it } from "vitest";
import type { Connection, Edge } from "@xyflow/react";

import type { NodeManifest, PortSpec } from "../types";
import type { NoodleNode } from "./store";
import {
  checkConnectionKinds,
  datasetConnectionIssues,
  validateConnection,
} from "./connectionValidation";

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

function node(id: string, m: NodeManifest, x = 0): NoodleNode {
  return {
    id,
    type: "noodle",
    position: { x, y: 0 },
    data: {
      manifest: m,
      params: {},
      disabled: false,
      outputsOverride: null,
      onError: "stop",
      retryOnFail: false,
      retries: 1,
      retryWaitSeconds: 0,
      retryBackoff: false,
      alwaysOutputData: false,
      timeoutSeconds: null,
    },
  };
}

describe("DatasetRef connection validation", () => {
  it("accepts dataset output to dataset input and explains DatasetRef behavior", () => {
    const result = checkConnectionKinds(
      manifest("dataset_filter", "dataset", "dataset"),
      "main",
      manifest("dataset_preview", "dataset", "any"),
      "input",
    );

    expect(result.ok).toBe(true);
    expect(result.message).toContain("DatasetRef connection");
  });

  it("rejects records wired into a DatasetRef input and suggests Records To Dataset", () => {
    const source = manifest("http_request", "any", "any");
    const target = manifest("duckdb_sql", "dataset", "dataset");

    const result = checkConnectionKinds(source, "main", target, "input");

    expect(result.ok).toBe(false);
    expect(result.quickFixId).toBe("records_to_dataset");
    expect(result.message).toContain("expects a DatasetRef");
  });

  it("rejects DatasetRef wired into records inputs and suggests Dataset To Records", () => {
    const source = manifest("csv_parse", "any", "dataset");
    const target = manifest("slack", "any", "any");

    const result = checkConnectionKinds(source, "main", target, "input");

    expect(result.ok).toBe(false);
    expect(result.quickFixId).toBe("dataset_to_records");
    expect(result.message).toContain("DatasetRef");
  });

  it("finds incompatible existing dataset edges", () => {
    const records = node("records", manifest("http_request", "any", "any"));
    const sql = node("sql", manifest("duckdb_sql", "dataset", "dataset"), 300);
    const edges: Edge[] = [
      { id: "e1", source: "records", sourceHandle: "main", target: "sql", targetHandle: "input" },
    ];

    const issues = datasetConnectionIssues([records, sql], edges);

    expect(issues).toHaveLength(1);
    expect(issues[0].check.quickFixId).toBe("records_to_dataset");
  });

  it("reports missing endpoints instead of throwing", () => {
    const connection: Connection = {
      source: "missing",
      sourceHandle: "main",
      target: "target",
      targetHandle: "input",
    };

    const result = validateConnection([], connection);

    expect(result.ok).toBe(false);
    expect(result.message).toContain("endpoint is missing");
  });
});
