import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { GlobalCommandMenu } from "./GlobalCommandMenu";

describe("GlobalCommandMenu", () => {
  it("opens with the keyboard shortcut and filters navigation truthfully", () => {
    render(
      <MemoryRouter>
        <GlobalCommandMenu user={{ role: "viewer" }} workspaceRole="viewer" />
      </MemoryRouter>,
    );

    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    const input = screen.getByPlaceholderText("Search pages and commands…");
    fireEvent.change(input, { target: { value: "runner" } });

    expect(screen.getByRole("option", { name: /Runners/i })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Team & access/i })).toBeNull();
  });

  it("separates workspace and instance administration", () => {
    render(
      <MemoryRouter>
        <GlobalCommandMenu
          user={{ role: "viewer" }}
          workspaceRole="admin"
          multiTenancyEnabled
        />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /Go to pages and commands/i }));
    expect(document.getElementById("noodle-command-organization")).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Team & access/i })).toBeNull();
    expect(screen.getByRole("option", { name: /Activity/i })).toBeTruthy();
  });

  it("closes on Escape and restores focus to the trigger", () => {
    render(
      <MemoryRouter>
        <GlobalCommandMenu user={{ role: "owner" }} workspaceRole="owner" />
      </MemoryRouter>,
    );

    const trigger = screen.getByRole("button", { name: /Go to pages and commands/i });
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
