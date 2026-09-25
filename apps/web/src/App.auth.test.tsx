import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { AuthState } from "./types";

const mocks = vi.hoisted(() => ({ authRequired: vi.fn(), logout: vi.fn() }));
vi.mock("./api", () => ({
  api: { authRequired: mocks.authRequired }, apiLogout: mocks.logout,
  getUser: () => null, setUser: vi.fn(), onUnauthorized: vi.fn(),
}));
vi.mock("./sessionIsolation", () => ({ clearClientDataScope: vi.fn(), clearClientSession: vi.fn() }));
vi.mock("./LoginPage", () => ({ LoginPage: () => <h1>Workspace sign in</h1> }));
vi.mock("./HomeLayout", () => ({ HomeLayout: () => <h1>Workspace</h1> }));
vi.mock("./FirstRunWizard", () => ({ FirstRunWizard: () => null }));
vi.mock("./AuthRuntime", () => ({
  AuthRuntimeProvider: ({ children, signOut }: { children: ReactNode; signOut: () => void }) =>
    <><button onClick={signOut}>Sign out</button>{children}</>,
}));
vi.mock("./WorkspaceAccess", () => ({ WorkspaceAccessProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock("./ToastProvider", () => ({ ToastProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock("./ConfirmProvider", () => ({ ConfirmProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock("./entitlements", () => ({ EntitlementsProvider: ({ children }: { children: ReactNode }) => children }));

import App from "./App";

const signedIn: AuthState = {
  auth_required: true, signed_in: true, registration_open: false, multi_tenancy: false,
  user: { id: "owner", email: "owner@example.com", name: "Owner", company: "Test", role: "owner" },
};
const signedOut = { ...signedIn, signed_in: false, user: null };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.authRequired.mockReset().mockResolvedValue(signedIn);
  mocks.logout.mockReset().mockResolvedValue(undefined);
});

describe("sign-out identity ordering", () => {
  it("waits for cookie revocation before refreshing identity", async () => {
    const logout = deferred<void>();
    mocks.logout.mockReturnValue(logout.promise);
    render(<MemoryRouter><App /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(screen.getByRole("heading", { name: "Workspace sign in" })).toBeVisible();
    expect(mocks.authRequired).toHaveBeenCalledTimes(1);

    mocks.authRequired.mockResolvedValue(signedOut);
    await act(async () => { logout.resolve(); });
    await waitFor(() => expect(mocks.authRequired).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("heading", { name: "Workspace sign in" })).toBeVisible();
  });

  it("ignores a signed-in focus response that arrives after sign-out", async () => {
    const stale = deferred<AuthState>();
    render(<MemoryRouter><App /></MemoryRouter>);
    await screen.findByRole("button", { name: "Sign out" });
    mocks.authRequired.mockReturnValueOnce(stale.promise).mockResolvedValue(signedOut);
    fireEvent(window, new Event("focus"));
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await act(async () => { stale.resolve(signedIn); });
    expect(screen.getByRole("heading", { name: "Workspace sign in" })).toBeVisible();
  });

  it("stays signed out if an unreachable logout leaves the server cookie alive", async () => {
    render(<MemoryRouter><App /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(mocks.authRequired).toHaveBeenCalledTimes(2));
    fireEvent(window, new Event("focus"));
    expect(mocks.authRequired).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "Workspace sign in" })).toBeVisible();
  });
});
