import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import {
  activationStorageKey,
  readActivationProgress,
  writeActivationProgress,
} from "./activation";
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

    expect(
      screen.getByRole("complementary", { name: "Get to a trusted first run" }),
    ).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Activation progress" })).toHaveAttribute(
      "aria-valuenow",
      "0",
    );
  });

  it("persists a collapsed checklist per user", () => {
    const auth = authFor({ id: "owner-1" });
    renderWizard(auth);

    fireEvent.click(
      screen.getByRole("button", { name: "Collapse activation checklist" }),
    );

    expect(readActivationProgress(firstRunStorageKey(auth)).collapsed).toBe(true);
    expect(
      localStorage.getItem(activationStorageKey(firstRunStorageKey(auth))),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Open activation checklist, 0 of 5 complete" }),
    ).toBeTruthy();
  });

  it("does not collapse setup for a different user", () => {
    const first = authFor({ id: "owner-1" });
    const second = authFor({ id: "owner-2", email: "owner2@example.com" });
    writeActivationProgress(firstRunStorageKey(first), {
      version: 2,
      completed: {},
      successfulRuns: 0,
      editedAfterRun: false,
      collapsed: true,
    });

    renderWizard(second);

    expect(
      screen.getByRole("complementary", { name: "Get to a trusted first run" }),
    ).toBeTruthy();
  });

  it("links every activation step to the relevant product area", () => {
    renderWizard();

    expect(screen.getByRole("link", { name: /Choose template/i })).toHaveAttribute(
      "href",
      "/?starter=datasetref-filter-export",
    );
    expect(screen.getByRole("link", { name: /Open artifacts/i })).toHaveAttribute(
      "href",
      "/artifacts",
    );
    expect(screen.getByRole("link", { name: /Open credentials/i })).toHaveAttribute(
      "href",
      "/credentials",
    );
  });
});
