import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExecutionsPage } from "./ExecutionsPage";
import type { RunInfo, RunListItem, WorkflowSummary } from "./types";

const apiMocks = vi.hoisted(() => ({
  listWorkflows: vi.fn(),
  listAllRuns: vi.fn(),
  runtimeMode: vi.fn(),
  queueStats: vi.fn(),
  getRun: vi.fn(),
  runTimeline: vi.fn(),
  runApprovals: vi.fn(),
  replayRun: vi.fn(),
}));

vi.mock("./api", () => ({
  api: apiMocks,
  errorMessage: (error: unknown) =>
    error instanceof Error ? error.message : String(error),
  subscribeToRunEvents: vi.fn(() => ({ close: vi.fn() })),
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
    apiMocks.listAllRuns.mockResolvedValue([runRow]);
    apiMocks.runtimeMode.mockResolvedValue({
      mode: "production",
      database_dialect: "postgresql",
      queue_backend: "database",
      scheduler_role: "leader",
      webhook_role: "enabled",
      artifact_backend: "local",
      runner_providers: [],
      allow_insecure: false,
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
    apiMocks.getRun.mockImplementation((runId: string) =>
      Promise.resolve(runId === "run-new" ? replayRun : oldRun),
    );
    apiMocks.runTimeline.mockResolvedValue({
      run_id: "run-old",
      status: "error",
      events: [],
    });
    apiMocks.runApprovals.mockResolvedValue([]);
    apiMocks.replayRun.mockResolvedValue({
      run_id: "run-new",
      previous_status: "failed",
      status: "queued",
    });
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
});
