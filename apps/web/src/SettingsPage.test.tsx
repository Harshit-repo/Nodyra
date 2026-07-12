import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, setUser } from "./api";
import { ConfirmProvider } from "./ConfirmProvider";
import { SettingsPage } from "./SettingsPage";
import type { AuthState, LicenseInfo, SystemSettings, UserInfo } from "./types";

const viewer: UserInfo = {
  id: "viewer-1",
  email: "viewer@example.com",
  name: "Viewer Person",
  company: "Nodyra Labs",
  role: "viewer",
};

const owner: UserInfo = { ...viewer, id: "owner-1", role: "owner", name: "Owner Person" };

function authFor(user: UserInfo): AuthState {
  return {
    auth_required: true,
    signed_in: true,
    registration_open: false,
    multi_tenancy: true,
    user,
  };
}

function renderSettings() {
  const router = createMemoryRouter([{ path: "/settings", element: <SettingsPage /> }], {
    initialEntries: ["/settings"],
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConfirmProvider>
        <RouterProvider router={router} />
      </ConfirmProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  setUser(null);
});

describe("SettingsPage", () => {
  it("keeps profile read-only, separates role scopes, and applies appearance locally", async () => {
    setUser(viewer);
    vi.spyOn(api, "authRequired").mockResolvedValue(authFor(viewer));
    vi.spyOn(api, "listMyOrgs").mockResolvedValue([
      { id: "default", name: "Acme", slug: "acme", status: "active", role: "admin" },
    ]);

    renderSettings();

    expect(screen.getByText("Viewer Person")).toBeTruthy();
    expect(screen.getByText("Instance role")).toBeTruthy();
    expect(await screen.findByText("Current workspace role")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "MCP server" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Save profile/i })).toBeNull();
    expect(screen.queryByText("Instance settings")).toBeNull();

    fireEvent.click(screen.getByRole("radio", { name: /DarkLow-light graphite/i }));
    expect(document.documentElement.dataset.theme).toBe("dark");

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    fireEvent.click(screen.getByRole("radio", { name: /LightBright neutral/i }));
    expect(screen.getByText(/Applied for this session/)).toBeTruthy();
  });

  it("shows complete instance and license controls only to instance admins", async () => {
    const settings: SystemSettings = {
      max_concurrent_runs: 4,
      runner_idle_seconds: 60,
      run_retention_days: 30,
      run_retention_max_per_workflow: 500,
      max_output_bytes: 1000000,
      max_artifact_bytes: 2000000,
      max_artifacts_per_run: 20,
      app_timezone: "Australia/Sydney",
      worker_rss_soft_budget_bytes: 4000000000,
    };
    const license: LicenseInfo = {
      edition: "community",
      customer: null,
      expires_at: null,
      entitlements: [],
      limits: { environments: 1, runners: 1, deployments: 1, seats: 3 },
      notice: null,
    };
    setUser(owner);
    vi.spyOn(api, "authRequired").mockResolvedValue(authFor(owner));
    vi.spyOn(api, "listMyOrgs").mockResolvedValue([
      { id: "default", name: "Acme", slug: "acme", status: "active", role: "viewer" },
    ]);
    vi.spyOn(api, "getSystemSettings").mockResolvedValue(settings);
    vi.spyOn(api, "getLicense").mockResolvedValue(license);
    vi.spyOn(api, "sandboxStatus").mockResolvedValue({
      mode: "off",
      active: false,
      runtime: null,
      network: null,
      idle: 0,
      active_runs: 0,
    });

    renderSettings();

    expect(await screen.findByText("Worker memory soft budget")).toBeTruthy();
    expect(await screen.findByText("Sandboxed execution is off")).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Plan & license" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save instance settings" })).toBeDisabled();

    fireEvent.change(screen.getByRole("spinbutton", { name: /Maximum concurrent runs/i }), {
      target: { value: "0" },
    });
    expect(screen.getByText(/Enter a value from 1 to 1,024/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save instance settings" })).toBeDisabled();
  });

  it("keeps instance administration available in auth-disabled local mode", async () => {
    setUser(null);
    vi.spyOn(api, "getSystemSettings").mockResolvedValue({
      max_concurrent_runs: 4,
      runner_idle_seconds: 60,
      run_retention_days: 30,
      run_retention_max_per_workflow: 500,
      max_output_bytes: 1000000,
      max_artifact_bytes: 2000000,
      max_artifacts_per_run: 20,
      app_timezone: "",
      worker_rss_soft_budget_bytes: 0,
    });
    vi.spyOn(api, "getLicense").mockResolvedValue({
      edition: "community",
      customer: null,
      expires_at: null,
      entitlements: [],
      limits: {},
      notice: null,
    });
    vi.spyOn(api, "sandboxStatus").mockResolvedValue({
      mode: "off",
      active: false,
      runtime: null,
      network: null,
      idle: 0,
      active_runs: 0,
    });

    renderSettings();

    expect(await screen.findByRole("heading", { name: "Instance settings" })).toBeTruthy();
    expect(await screen.findByText("Sandboxed execution is off")).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Plan & license" })).toBeTruthy();
  });

  it("shows the MCP agent quickstart with the instance URL", async () => {
    setUser(viewer);
    vi.spyOn(api, "authRequired").mockResolvedValue(authFor(viewer));
    vi.spyOn(api, "listMyOrgs").mockResolvedValue([
      { id: "default", name: "Acme", slug: "acme", status: "active", role: "admin" },
    ]);

    renderSettings();

    expect(await screen.findByText("Connect an AI agent")).toBeTruthy();
    expect(screen.getAllByText(/mcpServers/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/\/mcp/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Bearer <paste-token-here>/).length).toBeGreaterThan(0);
  });
});
