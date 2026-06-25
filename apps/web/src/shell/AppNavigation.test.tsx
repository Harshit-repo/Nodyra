import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AppSidebar, MobileNavigation } from "./AppNavigation";
import { SHELL_OVERLAY_OPEN_EVENT } from "./overlay";

describe("MobileNavigation", () => {
  it("opens an accessible permission-filtered More drawer", () => {
    render(
      <MemoryRouter>
        <MobileNavigation
          user={{ role: "viewer" }}
          workspaceRole="admin"
          multiTenancyEnabled
        />
      </MemoryRouter>,
    );

    const more = screen.getByRole("button", { name: "More", hidden: true });
    fireEvent.click(more);

    expect(screen.getByRole("dialog", { name: "More", hidden: true })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Workspace", hidden: true })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Team & access", hidden: true })).toBeNull();
    expect(screen.getByRole("link", { name: "Activity", hidden: true })).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "More", hidden: true })).toBeNull();
    expect(document.activeElement).toBe(more);
  });

  it("yields focus ownership before a nested portal overlay opens", () => {
    render(
      <MemoryRouter>
        <MobileNavigation user={{ role: "owner" }} workspaceRole="owner" />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "More", hidden: true }));
    expect(screen.getByRole("dialog", { name: "More", hidden: true })).toBeTruthy();

    fireEvent(window, new CustomEvent(SHELL_OVERLAY_OPEN_EVENT));
    expect(screen.queryByRole("dialog", { name: "More", hidden: true })).toBeNull();
  });
});

describe("AppSidebar", () => {
  it("exposes a persistent collapse control without changing navigation access", () => {
    const onCollapsedChange = vi.fn();
    render(
      <MemoryRouter>
        <AppSidebar
          user={{ role: "owner" }}
          workspaceRole="owner"
          onCollapsedChange={onCollapsedChange}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Credentials" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(onCollapsedChange).toHaveBeenCalledWith(true);
  });
});
