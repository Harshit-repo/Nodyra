import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type RuntimeModeStatus } from "./api";
import { ReadinessPanel } from "./ReadinessPanel";

function readyStatus(overrides: Partial<RuntimeModeStatus> = {}): RuntimeModeStatus {
  return {
    mode: "production",
    database_dialect: "postgresql",
    queue_backend: "redis",
    scheduler_role: "leader",
    webhook_role: "ingress",
    artifact_backend: "s3",
    runner_providers: [],
    replica_safe: true,
    replica_unsafe_reasons: [],
    allow_insecure: false,
    otel_enabled: true,
    warnings: [],
    ...overrides,
  };
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ReadinessPanel />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ReadinessPanel", () => {
  it("labels an otherwise warning-free local runtime as development mode", async () => {
    vi.spyOn(api, "getRuntimeMode").mockResolvedValue(
      readyStatus({
        mode: "local",
        database_dialect: "sqlite",
        queue_backend: "none",
        artifact_backend: "local",
        otel_enabled: false,
      }),
    );

    renderPanel();

    expect(await screen.findByText("Local development mode")).toBeTruthy();
    expect(screen.queryByText("Production-ready")).toBeNull();
    expect(screen.queryByLabelText("Production readiness warnings")).toBeNull();
    expect(screen.getByText(/Switch to production mode/)).toBeTruthy();
  });

  it("shows a production-ready state when runtime warnings are empty", async () => {
    vi.spyOn(api, "getRuntimeMode").mockResolvedValue(readyStatus());

    renderPanel();

    expect(await screen.findByText("Production-ready")).toBeTruthy();
    expect(screen.getByText("postgresql")).toBeTruthy();
    expect(screen.getByText("redis")).toBeTruthy();
    expect(screen.queryByLabelText("Production readiness warnings")).toBeNull();
  });

  it("maps known runtime warnings to fixes and deployment docs", async () => {
    vi.spyOn(api, "getRuntimeMode").mockResolvedValue(
      readyStatus({
        database_dialect: "sqlite",
        warnings: [
          "RUNTIME_MODE=production with a SQLite database_url; use PostgreSQL for durable, concurrent production storage.",
        ],
      }),
    );

    renderPanel();

    expect(await screen.findByText("SQLite is not production storage")).toBeTruthy();
    expect(screen.getByText(/Set DATABASE_URL to PostgreSQL/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Database docs/i })).toHaveAttribute(
      "href",
      expect.stringContaining("#configuration-flags"),
    );
  });

  it("surfaces replica-unsafe reasons even when production warnings are empty", async () => {
    vi.spyOn(api, "getRuntimeMode").mockResolvedValue(
      readyStatus({
        replica_safe: false,
        replica_unsafe_reasons: [
          "run event broker uses per-replica in-process buffers without Redis (api_replica_count=2)",
        ],
      }),
    );

    renderPanel();

    expect(await screen.findByText("Replica coordination needs Redis")).toBeTruthy();
    expect(screen.getByText(/run event broker uses per-replica/)).toBeTruthy();
    expect(screen.getByText(/Set QUEUE_BACKEND=redis/)).toBeTruthy();
  });
});
