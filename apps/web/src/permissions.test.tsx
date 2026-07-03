import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { AuthRuntimeProvider } from "./AuthRuntime";
import { useCan } from "./permissions";
import type { AuthState, UserInfo } from "./types";
import { WorkspaceAccessProvider } from "./WorkspaceAccess";

function PermissionProbe() {
  return <span>{useCan("workflow:write") ? "allowed" : "denied"}</span>;
}

function renderProbe(auth: AuthState) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <AuthRuntimeProvider auth={auth} signOut={null}>
      <QueryClientProvider client={client}>
        <WorkspaceAccessProvider>
          <PermissionProbe />
        </WorkspaceAccessProvider>
      </QueryClientProvider>
    </AuthRuntimeProvider>,
  );
}

const user = (role: UserInfo["role"]): UserInfo => ({
  id: `user-${role}`,
  email: `${role}@example.com`,
  name: role,
  role,
  company: "Nodyra",
});

afterEach(() => vi.restoreAllMocks());

describe("workspace-aware permissions", () => {
  it("does not grant workspace writes from an unrelated global role", async () => {
    vi.spyOn(api, "listMyOrgs").mockResolvedValue([
      { id: "default", name: "Nodyra", slug: "nodyra", status: "active", role: "viewer" },
    ]);
    renderProbe({ auth_required: true, signed_in: true, registration_open: false, multi_tenancy: true, user: user("admin") });
    expect(await screen.findByText("denied")).toBeTruthy();
  });

  it("uses the selected workspace membership when it grants write access", async () => {
    vi.spyOn(api, "listMyOrgs").mockResolvedValue([
      { id: "default", name: "Nodyra", slug: "nodyra", status: "active", role: "editor" },
    ]);
    renderProbe({ auth_required: true, signed_in: true, registration_open: false, multi_tenancy: true, user: user("viewer") });
    expect(await screen.findByText("allowed")).toBeTruthy();
  });

  it("uses live auth state even when browser identity caching is unavailable", () => {
    renderProbe({ auth_required: true, signed_in: true, registration_open: false, multi_tenancy: false, user: user("viewer") });
    expect(screen.getByText("denied")).toBeTruthy();
  });
});
