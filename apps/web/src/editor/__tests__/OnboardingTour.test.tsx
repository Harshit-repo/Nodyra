import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OnboardingTour } from "../OnboardingTour";

beforeEach(() => {
  localStorage.clear();
  document.body.innerHTML = "";
  // jsdom may not implement requestAnimationFrame — polyfill for polling logic
  if (!window.requestAnimationFrame) {
    Object.defineProperty(window, "requestAnimationFrame", {
      value: (cb: FrameRequestCallback) => setTimeout(() => cb(Date.now()), 0) as unknown as number,
      writable: true,
    });
  }
});

describe("OnboardingTour", () => {
  it("renders step 1 when active", () => {
    const canvas = document.createElement("div");
    canvas.setAttribute("data-tour-id", "canvas");
    document.body.appendChild(canvas);

    render(<OnboardingTour />);
    expect(screen.getByText("Your workflow canvas")).toBeTruthy();
    expect(screen.getByText("Step 1 of 5")).toBeTruthy();
  });

  it("does not render when dismissed via localStorage", () => {
    localStorage.setItem("nodyra-editor-tour-v1", "true");
    const canvas = document.createElement("div");
    canvas.setAttribute("data-tour-id", "canvas");
    document.body.appendChild(canvas);

    const { container } = render(<OnboardingTour />);
    expect(container.querySelector(".onboarding-tooltip")).toBeNull();
  });

  it("advances through steps on Next click", async () => {
    const user = userEvent.setup();
    for (const id of ["canvas", "palette", "toolbar", "run-button", "ai-draft-button"]) {
      const el = document.createElement("div");
      el.setAttribute("data-tour-id", id);
      document.body.appendChild(el);
    }

    render(<OnboardingTour />);
    expect(screen.getByText("Your workflow canvas")).toBeTruthy();

    await user.click(screen.getByText("Next"));
    expect(screen.getByText("Node palette")).toBeTruthy();
    expect(screen.getByText("Step 2 of 5")).toBeTruthy();
  });

  it("shows Finish on last step", async () => {
    const user = userEvent.setup();
    for (const id of ["canvas", "palette", "toolbar", "run-button", "ai-draft-button"]) {
      const el = document.createElement("div");
      el.setAttribute("data-tour-id", id);
      document.body.appendChild(el);
    }

    render(<OnboardingTour />);
    // 5 steps (indices 0-4). Click Next 4 times to reach step 4 (last), then Finish.
    for (let i = 0; i < 4; i++) {
      await user.click(screen.getByText("Next"));
    }
    // Now at step 4 (last step), button should say "Finish"
    await user.click(screen.getByText("Finish"));
    // After clicking Finish, useEffect sets isActive=false and persists
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(localStorage.getItem("nodyra-editor-tour-v1")).toBe("true");
  });

  it("Skip button dismisses tour", async () => {
    const user = userEvent.setup();
    const canvas = document.createElement("div");
    canvas.setAttribute("data-tour-id", "canvas");
    document.body.appendChild(canvas);

    render(<OnboardingTour />);
    await user.click(screen.getByText("Skip tour"));
    expect(localStorage.getItem("nodyra-editor-tour-v1")).toBe("true");
  });

  it.skip("shows fallback message when target element not found (RAF-dependent, test in E2E)", async () => {
    render(<OnboardingTour />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 2100));
    });
    expect(screen.getByText(/not currently visible/)).toBeTruthy();
  });

  it("Previous button goes back to prior step", async () => {
    const user = userEvent.setup();
    for (const id of ["canvas", "palette"]) {
      const el = document.createElement("div");
      el.setAttribute("data-tour-id", id);
      document.body.appendChild(el);
    }

    render(<OnboardingTour />);
    await user.click(screen.getByText("Next"));
    expect(screen.getByText("Node palette")).toBeTruthy();
    await user.click(screen.getByText("Previous"));
    expect(screen.getByText("Your workflow canvas")).toBeTruthy();
  });
});
