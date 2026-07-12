import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { FirstRunWizard, firstRunStorageKey } from "./FirstRunWizard";
import type { AuthState, UserInfo } from "./types";

function authFor(user: Partial<UserInfo> = {}): AuthState {
  return {
    auth_required: true,
    signed_in: true,
    registration_open: false,
    multi_tenancy: true,
    user: {
      id: "user-1",
      email: "owner@example.com",
      name: "Owner",
      company: "Nodyra",
      role: "owner",
      ...user,
    },
  };
}

function renderWizard(auth: AuthState = authFor()) {
  return render(
    <MemoryRouter>
      <FirstRunWizard auth={auth} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  localStorage.clear();
});

describe("FirstRunWizard", () => {
  it("shows setup for a new signed-in user", () => {
    renderWizard();

    expect(screen.getByRole("dialog", { name: "Workspace setup" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Admin confirmed" })).toBeTruthy();
  });

  it("persists dismissal per user", () => {
    const auth = authFor({ id: "owner-1" });
    renderWizard(auth);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(localStorage.getItem(firstRunStorageKey(auth))).toBe("done");
    expect(screen.queryByRole("dialog", { name: "Workspace setup" })).toBeNull();
  });

  it("does not hide setup for a different user", () => {
    const first = authFor({ id: "owner-1" });
    const second = authFor({ id: "owner-2", email: "owner2@example.com" });
    localStorage.setItem(firstRunStorageKey(first), "done");

    renderWizard(second);

    expect(screen.getByRole("dialog", { name: "Workspace setup" })).toBeTruthy();
  });

  it("steps through credential and template links", () => {
    renderWizard();

    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));
    expect(screen.getByRole("heading", { name: "Connect a credential" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Open credentials/i })).toHaveAttribute(
      "href",
      "/credentials",
    );

    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));
    expect(screen.getByRole("heading", { name: "Run a template" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Open templates/i })).toHaveAttribute(
      "href",
      "/",
    );
  });
});
