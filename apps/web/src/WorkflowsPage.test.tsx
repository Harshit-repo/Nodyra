import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setUser } from "./api";
import { ToastProvider } from "./ToastProvider";
import type { WorkflowSummary } from "./types";

const credentialQueryOptions: Array<{ enabled?: boolean }> = [];
const navigateMock = vi.hoisted(() => vi.fn());
const createWorkflowMock = vi.hoisted(() => vi.fn());
const instantiateTemplateMock = vi.hoisted(() => vi.fn());
const workflowRows = vi.hoisted(() => ({ current: [] as WorkflowSummary[] }));
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

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("./queries", () => ({
  useWorkflows: () => ({ data: workflowRows.current, isError: false, error: null, refetch: vi.fn() }),
  useTemplates: () => ({
    data: [
      {
        id: "webhook_to_slack",
        name: "Webhook to Slack alert",
        description: "Receive a webhook and notify Slack.",
        tags: ["starter"],
      },
    ],
    isError: false,
    error: null,
  }),
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
  useCreateWorkflowMutation: () => ({ mutateAsync: createWorkflowMock }),
  useInstantiateTemplateMutation: () => ({ mutateAsync: instantiateTemplateMock }),
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

beforeEach(() => {
  workflowRows.current = [workflow];
});

afterEach(() => {
  setUser(null);
  credentialQueryOptions.length = 0;
  navigateMock.mockReset();
  createWorkflowMock.mockReset();
  instantiateTemplateMock.mockReset();
});

describe("WorkflowsPage permissions", () => {
  it("keeps viewer workflows read-only and does not request credentials", () => {
    setUser({ id: "viewer", email: "viewer@example.com", name: "Viewer", company: "Nodyra", role: "viewer" });
    renderPage();

    expect(screen.getByRole("button", { name: "Open workflow: Read-only workflow" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New workflow" })).toBeNull();
    expect(screen.queryByRole("button", { name: "More options" })).toBeNull();
    expect(screen.queryByText("Credential attention")).toBeNull();
    expect(credentialQueryOptions.at(-1)?.enabled).toBe(false);
  });

  it("shows mutation controls to workspace editors in single-tenant mode", () => {
    setUser({ id: "editor", email: "editor@example.com", name: "Editor", company: "Nodyra", role: "editor" });
    renderPage();

    expect(screen.getByRole("button", { name: "New workflow" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "More options" })).toBeTruthy();
    expect(screen.getByText("Credential attention")).toBeTruthy();
    expect(credentialQueryOptions.at(-1)?.enabled).toBe(true);
  });

  it("instantiates a selected server template from the create dialog", async () => {
    setUser({ id: "editor", email: "editor@example.com", name: "Editor", company: "Nodyra", role: "editor" });
    instantiateTemplateMock.mockResolvedValue({
      id: "workflow-from-template",
      name: "Webhook to Slack alert",
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "New workflow" }));
    fireEvent.click(
      screen.getByRole("button", { name: /Webhook to Slack alert/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(instantiateTemplateMock).toHaveBeenCalledWith({
        id: "webhook_to_slack",
        name: "Webhook to Slack alert",
      }),
    );
    expect(createWorkflowMock).not.toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith("/workflows/workflow-from-template");
  });

  it("shows a template gallery for a fresh writable workspace", async () => {
    workflowRows.current = [];
    setUser({ id: "editor", email: "editor@example.com", name: "Editor", company: "Nodyra", role: "editor" });
    createWorkflowMock.mockResolvedValue({
      id: "workflow-from-gallery",
      name: "API fetch + Python",
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "Start with a workflow template" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create with AI" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Blank workflow" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Use template:/i }).length).toBeGreaterThanOrEqual(4);

    fireEvent.click(screen.getByRole("button", { name: "Use template: API fetch + Python" }));

    await waitFor(() =>
      expect(createWorkflowMock).toHaveBeenCalledWith({
        name: "API fetch + Python",
        graph: expect.objectContaining({
          nodes: expect.any(Array),
          edges: expect.any(Array),
        }),
      }),
    );
    expect(instantiateTemplateMock).not.toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith("/workflows/workflow-from-gallery");
  });

  it("creates a blank workflow and opens the AI draft path from the empty state", async () => {
    workflowRows.current = [];
    setUser({ id: "editor", email: "editor@example.com", name: "Editor", company: "Nodyra", role: "editor" });
    createWorkflowMock.mockResolvedValue({
      id: "workflow-ai",
      name: "AI workflow draft",
    });

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "Create with AI" }));

    await waitFor(() =>
      expect(createWorkflowMock).toHaveBeenCalledWith({
        name: "AI workflow draft",
      }),
    );
    expect(navigateMock).toHaveBeenCalledWith("/workflows/workflow-ai?ai=1");
  });
});
