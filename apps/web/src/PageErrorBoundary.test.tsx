import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PageErrorBoundary } from "./PageErrorBoundary";

function Boom(): never {
  throw new Error("kaboom");
}

describe("PageErrorBoundary", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders children when nothing throws", () => {
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <div>healthy</div>
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByText("healthy")).toBeTruthy();
  });

  it("shows inline fallback with alert role when a child throws", () => {
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <Boom />
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toBeTruthy();
    expect(alert.className).toContain("page-error");
    expect(alert.className).not.toContain("app-error");
  });

  it("shows the correct headline and containment message", () => {
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <Boom />
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByText("This page ran into a problem")).toBeTruthy();
    expect(
      screen.getByText(/The error is contained here/),
    ).toBeTruthy();
  });

  it("shows 'Try again' button and 'Back to workflows' link", () => {
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <Boom />
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to workflows" })).toBeTruthy();
  });

  it("'Try again' clears the error and re-renders children", () => {
    let crash = true;
    function Maybe() {
      if (crash) throw new Error("kaboom");
      return <div>recovered</div>;
    }
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <Maybe />
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByText("This page ran into a problem")).toBeTruthy();
    crash = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("recovered")).toBeTruthy();
    expect(screen.queryByText("This page ran into a problem")).toBeNull();
  });

  it("recovers when navigating from a failed sibling route", () => {
    render(
      <MemoryRouter initialEntries={["/broken"]}>
        <Link to="/settings">Settings</Link>
        <Routes>
          <Route path="/broken" element={<PageErrorBoundary><Boom /></PageErrorBoundary>} />
          <Route path="/" element={<PageErrorBoundary><h1>Workflows</h1></PageErrorBoundary>} />
          <Route path="/settings" element={<PageErrorBoundary><h1>Settings</h1></PageErrorBoundary>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("link", { name: "Back to workflows" }));
    expect(screen.getByRole("heading", { name: "Workflows" })).toBeVisible();
    fireEvent.click(screen.getByRole("link", { name: "Settings" }));
    expect(screen.getByRole("heading", { name: "Settings" })).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("preserves healthy form state when query navigation changes", () => {
    function Form() {
      const [name, setName] = useState("");
      return <input aria-label="Name" value={name} onChange={(event) => setName(event.target.value)} />;
    }
    render(
      <MemoryRouter>
        <Link to="/?tab=advanced">Advanced</Link>
        <PageErrorBoundary><Form /></PageErrorBoundary>
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: "Unsaved draft" } });
    fireEvent.click(screen.getByRole("link", { name: "Advanced" }));
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Unsaved draft");
  });
});
