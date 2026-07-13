import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExecutionsPage } from "./ExecutionsPage";
import type { RunInfo, RunListItem, WorkflowGraph, WorkflowSummary } from "./types";

const apiMocks = vi.hoisted(() => ({
  listWorkflows: vi.fn(),
  listAllRuns: vi.fn(),
  runtimeMode: vi.fn(),
  queueStats: vi.fn(),
  queueCapacity: vi.fn(),
  getRun: vi.fn(),
  runTimeline: vi.fn(),
  runApprovals: vi.fn(),
  replayRun: vi.fn(),
  getVersionGraph: vi.fn(),
  listBackends: vi.fn(),
}));

vi.mock("./api", () => ({
  api: apiMocks,
  errorMessage: (error: unknown) =>
    error instanceof Error ? error.message : String(error),
  subscribeToRunEvents: vi.fn(() => ({ close: vi.fn() })),
}));

vi.mock("@xyflow/react", () => ({
  ReactFlowProvider: ({
    children,
  }: {
    children: import("react").ReactNode;
  }) => <>{children}</>,
}));

vi.mock("./editor/WorkflowDiffView", () => ({
  GraphDiffView: ({
    baseGraph,
    compareGraph,
  }: {
    baseGraph: { nodes: unknown[] };
    compareGraph: { nodes: unknown[] };
  }) => (
    <div data-testid="graph-diff-view">
      added changed {baseGraph.nodes.length}:{compareGraph.nodes.length}
    </div>
  ),
}));

function renderExecutions(initialPath = "/executions?run=run-old") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <ExecutionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const workflow: WorkflowSummary = {
  id: "wf-1",
  name: "Failed pipeline",
  active: true,
  version: 3,
  published_version: 3,
  graph_revision: 1,
  has_unpublished_changes: false,
  node_count: 2,
  environment_id: null,
  updated_at: "2026-07-03T10:00:00.000Z",
  created_at: "2026-07-03T09:00:00.000Z",
};

const runRow: RunListItem = {
  id: "run-old",
  workflow_id: "wf-1",
  workflow_name: "Failed pipeline",
  workflow_version: 3,
  workflow_version_id: "version-error",
  trace_id: "1234567890abcdef1234567890abcdef",
  mode: "queue",
  status: "error",
  trigger_type: "manual",
  started_at: "2026-07-03T10:01:00.000Z",
  finished_at: "2026-07-03T10:01:05.000Z",
};

const oldRun: RunInfo = {
  id: "run-old",
  workflow_id: "wf-1",
  workflow_version: 3,
  workflow_version_id: "version-error",
  trace_id: "1234567890abcdef1234567890abcdef",
  mode: "queue",
  status: "error",
  trigger_type: "manual",
  started_at: "2026-07-03T10:01:00.000Z",
  finished_at: "2026-07-03T10:01:05.000Z",
  node_runs: [
    {
      node_id: "fetch",
      status: "success",
      output: { ok: true },
      error: null,
      duration_ms: 42,
    },
    {
      node_id: "transform",
      status: "error",
      output: null,
      error: "Bad transform",
      duration_ms: 18,
    },
  ],
};

const replayRun: RunInfo = {
  ...oldRun,
  id: "run-new",
  status: "queued",
  node_runs: [],
};

describe("ExecutionsPage replay from node", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.listWorkflows.mockResolvedValue([workflow]);
    apiMocks.listAllRuns.mockImplementation((filters?: { status?: string }) =>
      Promise.resolve(filters?.status === "success" ? [] : [runRow]),
    );
    apiMocks.runtimeMode.mockResolvedValue({
      mode: "production",
      database_dialect: "postgresql",
      queue_backend: "database",
      scheduler_role: "leader",
      webhook_role: "enabled",
      artifact_backend: "local",
      runner_providers: [],
      replica_safe: true,
      replica_unsafe_reasons: [],
      allow_insecure: false,
      otel_enabled: false,
      warnings: [],
    });
    apiMocks.queueStats.mockResolvedValue({
      queued: 0,
      leased: 0,
      running: 0,
      waiting: 0,
      completed: 4,
      failed: 1,
      dead_lettered: 0,
      cancelled: 0,
      oldest_queued_age_seconds: null,
    });
    apiMocks.queueCapacity.mockResolvedValue({
      dispatchers: [],
      leased: 0,
      running: 0,
      local_max_slots: 0,
      local_available_slots: 0,
      queued: 0,
      label_blocked_queued: 0,
    });
    apiMocks.getRun.mockImplementation((runId: string) =>
      Promise.resolve(runId === "run-new" ? replayRun : oldRun),
    );
    apiMocks.runTimeline.mockResolvedValue({
      run_id: "run-old",
      status: "error",
      trace_id: "1234567890abcdef1234567890abcdef",
      events: [],
    });
    apiMocks.runApprovals.mockResolvedValue([]);
    apiMocks.replayRun.mockResolvedValue({
      run_id: "run-new",
      previous_status: "failed",
      status: "queued",
    });
    apiMocks.getVersionGraph.mockRejectedValue(new Error("not mocked"));
    apiMocks.listBackends.mockResolvedValue({ platform: "windows" });
  });

  it("replays from the failed node and follows the queued replay run", async () => {
    renderExecutions();

    const replayButton = await screen.findByRole("button", {
      name: "Replay from here",
    });

    fireEvent.click(replayButton);

    await waitFor(() =>
      expect(apiMocks.replayRun).toHaveBeenCalledWith("run-old", "transform"),
    );
    await waitFor(() => expect(apiMocks.getRun).toHaveBeenCalledWith("run-new"));
    expect(screen.getByText("Run run-new")).toBeInTheDocument();
  });

  it("links failed runs into the AI repair flow", async () => {
    renderExecutions();

    const repairLink = await screen.findByRole("link", {
      name: "Fix with AI",
    });

    expect(repairLink).toHaveAttribute(
      "href",
      "/workflows/wf-1?ai=fix_failed&run_id=run-old",
    );
  });

  it("links traced runs to the trace explorer", async () => {
    renderExecutions();

    const traceLink = await screen.findByRole("link", {
      name: "Trace 12345678",
    });

    expect(traceLink).toHaveAttribute(
      "href",
      "http://localhost:16686/trace/1234567890abcdef1234567890abcdef",
    );
  });

  it("compares a run graph to the last successful run on a different version", async () => {
    const greenRun: RunListItem = {
      ...runRow,
      id: "run-green",
      status: "success",
      workflow_version: 2,
      workflow_version_id: "version-green",
      started_at: "2026-07-03T09:50:00.000Z",
      finished_at: "2026-07-03T09:50:02.000Z",
    };
    const greenGraph: WorkflowGraph = {
      nodes: [
        {
          id: "fetch",
          type: "http_request",
          params: { url: "https://example.com/a" },
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
      ],
      edges: [],
    };
    const runGraph: WorkflowGraph = {
      nodes: [
        {
          ...greenGraph.nodes[0],
          params: { url: "https://example.com/b" },
        },
        {
          id: "transform",
          type: "code",
          params: { code: "output = input" },
          position: { x: 220, y: 0 },
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
      edges: [
        {
          id: "e1",
          source: "fetch",
          source_output: "main",
          target: "transform",
          target_input: "input",
        },
      ],
    };
    apiMocks.listAllRuns.mockImplementation((filters?: { status?: string }) =>
      Promise.resolve(filters?.status === "success" ? [greenRun] : [runRow]),
    );
    apiMocks.getVersionGraph.mockImplementation(
      (_workflowId: string, versionId: string) =>
        Promise.resolve({
          graph: versionId === "version-green" ? greenGraph : runGraph,
        }),
    );

    renderExecutions();

    const compare = await screen.findByRole("button", {
      name: "Compare to last green run",
    });
    await waitFor(() => expect(compare).not.toBeDisabled());
    fireEvent.click(compare);

    await waitFor(() =>
      expect(apiMocks.getVersionGraph).toHaveBeenCalledWith(
        "wf-1",
        "version-green",
      ),
    );
    await waitFor(() =>
      expect(apiMocks.getVersionGraph).toHaveBeenCalledWith(
        "wf-1",
        "version-error",
      ),
    );
    expect(
      await screen.findByRole("dialog", { name: "Run graph diff" }),
    ).toBeTruthy();
    expect(await screen.findByText(/Green v2 to run v3/)).toBeTruthy();
    expect(document.body.textContent).toMatch(/added/i);
    expect(document.body.textContent).toMatch(/changed/i);
  });
});
