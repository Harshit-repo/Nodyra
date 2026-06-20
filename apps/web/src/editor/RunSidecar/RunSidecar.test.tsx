import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { RunSidecar } from "./index";
import * as queries from "../../queries";

vi.mock("../../queries", () => ({
  useRuns: vi.fn(),
  useRun: vi.fn(),
  useRerunRunMutation: vi.fn(),
}));
vi.mock("../store", () => ({
  useEditor: vi.fn((selector: (s: object) => unknown) =>
    selector({ applyRunInfo: vi.fn(), clearRun: vi.fn(), runId: null, nodes: [] }),
  ),
  isTriggerManifest: vi.fn(() => false),
}));

beforeEach(() => {
  vi.mocked(queries.useRuns).mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof queries.useRuns>);
  vi.mocked(queries.useRun).mockReturnValue({
    data: null,
    isLoading: false,
  } as unknown as ReturnType<typeof queries.useRun>);
  vi.mocked(queries.useRerunRunMutation).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof queries.useRerunRunMutation>);
});

describe("RunSidecar", () => {
  const props = { workflowId: "wf-1" };

  it("renders 5 tabs", () => {
    render(<RunSidecar {...props} />);
    expect(screen.getByText("Runs")).toBeTruthy();
    expect(screen.getByText("Diff")).toBeTruthy();
    expect(screen.getByText("Time")).toBeTruthy();
    expect(screen.getByText("Stats")).toBeTruthy();
    expect(screen.getByText("Info")).toBeTruthy();
  });

  it("shows the Runs panel by default", () => {
    render(<RunSidecar {...props} />);
    expect(screen.getByText("Run history")).toBeTruthy();
  });

  it("switches to Diff panel on tab click", () => {
    render(<RunSidecar {...props} />);
    fireEvent.click(screen.getByText("Diff"));
    expect(screen.getByText("Run diff")).toBeTruthy();
  });
});
