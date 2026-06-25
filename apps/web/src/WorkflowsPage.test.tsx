import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setUser } from "./api";
import { ToastProvider } from "./ToastProvider";
import type { WorkflowSummary } from "./types";

const credentialQueryOptions: Array<{ enabled?: boolean }> = [];
const workflow: WorkflowSummary = {
  id: "workflow-1",
  name: "Read-only workflow",
  active: false,
  node_count: 2,
  version: 1,
  published_version: 1,
  has_unpublished_changes: false,
  last_run_status: null,
  last_run_started_at: null,
  updated_at: "2026-06-22T00:00:00Z",
} as WorkflowSummary;

vi.mock("./queries", () => ({
  useWorkflows: () => ({ data: [workflow], isError: false, error: null, refetch: vi.fn() }),
  useDeployments: () => ({ data: [] }),
  useCredentials: (options: { enabled?: boolean }) => {
    credentialQueryOptions.push(options);
    return { data: [] };
  },
  useWorkflowProviderTriggers: () => ({ data: null, isError: false, error: null }),
  useFolders: () => ({ data: [], isError: false, error: null }),
  useCreateFolderMutation: () => ({ mutateAsync: vi.fn() }),
  useUpdateFolderMutation: () => ({ mutateAsync: vi.fn() }),
  useDeleteFolderMutation: () => ({ mutateAsync: vi.fn() }),
  useCreateWorkflowMutation: () => ({ mutateAsync: vi.fn() }),
  useDeleteWorkflowMutation: () => ({ mutateAsync: vi.fn() }),
  useUpdateWorkflowMutation: () => ({ mutateAsync: vi.fn() }),
}));

const { WorkflowsPage } = await import("./WorkflowsPage");

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <WorkflowsPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  setUser(null);
  credentialQueryOptions.length = 0;
});

describe("WorkflowsPage permissions", () => {
  it("keeps viewer workflows read-only and does not request credentials", () => {
    setUser({ id: "viewer", email: "viewer@example.com", name: "Viewer", company: "Noodle", role: "viewer" });
    renderPage();

    expect(screen.getByRole("button", { name: "Open workflow: Read-only workflow" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New workflow" })).toBeNull();
    expect(screen.queryByRole("button", { name: "More options" })).toBeNull();
    expect(screen.queryByText("Credential attention")).toBeNull();
    expect(credentialQueryOptions.at(-1)?.enabled).toBe(false);
  });

  it("shows mutation controls to workspace editors in single-tenant mode", () => {
    setUser({ id: "editor", email: "editor@example.com", name: "Editor", company: "Noodle", role: "editor" });
    renderPage();

    expect(screen.getByRole("button", { name: "New workflow" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "More options" })).toBeTruthy();
    expect(screen.getByText("Credential attention")).toBeTruthy();
    expect(credentialQueryOptions.at(-1)?.enabled).toBe(true);
  });
});
