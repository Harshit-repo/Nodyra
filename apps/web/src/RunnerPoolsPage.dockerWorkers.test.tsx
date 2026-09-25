import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, it, expect, vi } from "vitest";

import { DockerWorkerCard, RunnerPoolsPage } from "./RunnerPoolsPage";
import { ConfirmProvider } from "./ConfirmProvider";
import type { RunnerPoolInfo } from "./types";

const entitlementMock = vi.hoisted(() => ({ hasDedicatedPools: true }));
afterEach(() => { entitlementMock.hasDedicatedPools = true; });

describe("DockerWorkerCard", () => {
  it("shows the sandbox checkbox with a trust warning", () => {
    render(
      <DockerWorkerCard poolId="p1" config={{}} canWrite onSave={vi.fn()} onAddRunner={vi.fn()} />
    );
    expect(screen.getByLabelText(/sandboxed execution support/i)).toBeInTheDocument();
    expect(screen.getByText(/root-equivalent/i)).toBeInTheDocument();
  });

  it("calls onAddRunner when the button is clicked", async () => {
    const onAdd = vi.fn().mockResolvedValue(undefined);
    render(
      <DockerWorkerCard poolId="p1" config={{}} canWrite onSave={vi.fn()} onAddRunner={onAdd} />
    );
    fireEvent.click(screen.getByRole("button", { name: /add docker runner/i }));
    await waitFor(() => expect(onAdd).toHaveBeenCalled());
  });

  it("hides mutating controls when canWrite is false", () => {
    render(
      <DockerWorkerCard
        poolId="p1"
        config={{}}
        canWrite={false}
        onSave={vi.fn()}
        onAddRunner={vi.fn()}
      />
    );
    expect(screen.queryByRole("button", { name: /add docker runner/i })).toBeNull();
  });

  it("saves docker_runner + docker_autoscale config with the checkbox and daemon", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <DockerWorkerCard poolId="p1" config={{}} canWrite onSave={onSave} onAddRunner={vi.fn()} />
    );
    // Turn on the sandbox checkbox and autoscaling, then save.
    fireEvent.click(screen.getByLabelText(/sandboxed execution support/i));
    fireEvent.click(screen.getByLabelText(/enable autoscaling/i));
    // Autoscale fields appear only when enabled.
    expect(screen.getByLabelText(/maximum runners/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /save docker settings/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const payload = onSave.mock.calls[0][0] as Record<string, unknown>;
    expect((payload.docker_runner as { sandbox?: boolean }).sandbox).toBe(true);
    expect((payload.docker_autoscale as { enabled?: boolean }).enabled).toBe(true);
  });

  it("surfaces the remote docker_host field only when Remote daemon is chosen", () => {
    render(
      <DockerWorkerCard poolId="p1" config={{}} canWrite onSave={vi.fn()} onAddRunner={vi.fn()} />
    );
    expect(screen.queryByLabelText(/remote docker_host/i)).toBeNull();
    fireEvent.change(screen.getByLabelText(/docker daemon/i), { target: { value: "remote" } });
    expect(screen.getByLabelText(/remote docker_host/i)).toBeInTheDocument();
  });
});

// --- Page integration: the card must actually be mounted in the real page ----

const agentPool: RunnerPoolInfo = {
  id: "pool-agent-1",
  name: "Docker agents",
  provider: "agent",
  provider_config: {},
  max_concurrent_runs: 4,
  runner_count: 0,
  online_count: 0,
  ghost_count: 0,
  aws_secret_configured: false,
  created_at: "2026-07-06T00:00:00Z",
  updated_at: "2026-07-06T00:00:00Z",
} as RunnerPoolInfo;

vi.mock("./permissions", () => ({ useCan: () => true }));
vi.mock("./entitlements", () => ({
  useEntitlements: () => ({
    atLimit: () => false,
    limitFor: () => null,
    has: () => entitlementMock.hasDedicatedPools,
  }),
}));
vi.mock("./queries", () => ({
  useRunnerPools: () => ({ data: [agentPool], isError: false, error: null, refetch: vi.fn() }),
  useRunnerFleetHealth: () => ({ data: undefined }),
  useRunnerPoolRunners: () => ({ data: [], refetch: vi.fn() }),
  useRunnerPoolRunHistory: () => ({ data: undefined }),
  useEnvironments: () => ({ data: [] }),
  useCreateRunnerPoolMutation: () => ({ mutateAsync: vi.fn() }),
  useUpdateRunnerPoolMutation: () => ({ mutateAsync: vi.fn() }),
  useDeleteRunnerPoolMutation: () => ({ mutateAsync: vi.fn() }),
  useCreateRunnerRegistrationTokenMutation: () => ({ mutateAsync: vi.fn() }),
  useSshOnboardRunnerMutation: () => ({ mutateAsync: vi.fn() }),
  useDeleteRunnerMutation: () => ({ mutateAsync: vi.fn() }),
  useDrainRunnerMutation: () => ({ mutateAsync: vi.fn() }),
  useRestartRunnerMutation: () => ({ mutateAsync: vi.fn() }),
  useUpdateRunnerMutation: () => ({ mutateAsync: vi.fn() }),
  useCleanupGhostsMutation: () => ({ mutateAsync: vi.fn() }),
  useAddDockerRunnerMutation: () => ({ mutateAsync: vi.fn() }),
  useRemoveDockerRunnerMutation: () => ({ mutateAsync: vi.fn() }),
}));

describe("RunnerPoolsPage — Docker workers integration", () => {
  it("explains the Enterprise requirement before offering a rejected creation flow", () => {
    entitlementMock.hasDedicatedPools = false;
    render(<MemoryRouter><ConfirmProvider><RunnerPoolsPage /></ConfirmProvider></MemoryRouter>);
    expect(screen.queryByRole("button", { name: /New pool|Create runner pool/i })).toBeNull();
    expect(screen.getByText(/Enterprise adds dedicated/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View license settings" })).toHaveAttribute("href", "/settings");
  });
  it("renders the Docker workers card inside an expanded agent pool", async () => {
    render(
      <MemoryRouter>
        <ConfirmProvider>
          <RunnerPoolsPage />
        </ConfirmProvider>
      </MemoryRouter>
    );
    // Expand the pool (the card lives in the expanded agent-pool body).
    fireEvent.click(screen.getByRole("button", { name: /^runners$/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /add docker runner/i })).toBeInTheDocument()
    );
    expect(screen.getByLabelText(/sandboxed execution support/i)).toBeInTheDocument();
  });
});
