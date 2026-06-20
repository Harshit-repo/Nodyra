import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { RunList } from "./RunList";
import * as queries from "../../queries";
import * as editorStore from "../store";

vi.mock("../../queries", () => ({
  useRuns: vi.fn(),
  useRun: vi.fn(),
  useRerunRunMutation: vi.fn(),
}));
vi.mock("../store", () => ({
  useEditor: vi.fn(),
  isTriggerManifest: vi.fn(),
}));

const mockRuns = [
  {
    id: "run-1",
    status: "error",
    trigger_type: "manual",
    started_at: "2026-06-19T14:14:00Z",
    finished_at: "2026-06-19T14:14:01Z",
    node_runs: [
      { node_id: "n1", status: "success", output: null, error: null, duration_ms: 82 },
      { node_id: "n2", status: "error", output: null, error: "SMTP timeout", duration_ms: 920 },
    ],
  },
];

beforeEach(() => {
  vi.mocked(queries.useRuns).mockReturnValue({ data: mockRuns, isLoading: false } as ReturnType<typeof queries.useRuns>);
  vi.mocked(queries.useRun).mockReturnValue({ data: mockRuns[0], isLoading: false } as ReturnType<typeof queries.useRun>);
  vi.mocked(queries.useRerunRunMutation).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof queries.useRerunRunMutation>);
  vi.mocked(editorStore.useEditor).mockImplementation((selector: (s: unknown) => unknown) =>
    selector({ applyRunInfo: vi.fn(), clearRun: vi.fn(), runId: null, nodes: [] })
  );
});

describe("RunList", () => {
  const defaultProps = {
    workflowId: "wf-1",
    selectedRunId: null,
    onSelectRun: vi.fn(),
    pinnedNodeId: null,
    onPinNode: vi.fn(),
    onSwitchTab: vi.fn(),
  };

  it("renders run rows from useRuns", () => {
    render(<RunList {...defaultProps} />);
    expect(screen.getByText(/14:14|2:14/i)).toBeTruthy();
    expect(screen.getByText("error")).toBeTruthy();
  });

  it("calls onSelectRun when a row is clicked", () => {
    const onSelectRun = vi.fn();
    render(<RunList {...defaultProps} onSelectRun={onSelectRun} />);
    fireEvent.click(screen.getByText("error").closest(".sc-run-row")!);
    expect(onSelectRun).toHaveBeenCalledWith("run-1");
  });

  it("shows node inspector when a run is selected", () => {
    render(<RunList {...defaultProps} selectedRunId="run-1" />);
    expect(screen.getByText("n1")).toBeTruthy();
    expect(screen.getByText("n2")).toBeTruthy();
  });

  it("filters to errors when Errors chip is clicked", () => {
    const successRun = { ...mockRuns[0], id: "run-2", status: "success" };
    vi.mocked(queries.useRuns).mockReturnValue({ data: [mockRuns[0], successRun], isLoading: false } as ReturnType<typeof queries.useRuns>);
    render(<RunList {...defaultProps} />);
    fireEvent.click(screen.getByText("Errors"));
    expect(screen.getByText("error")).toBeTruthy();
    expect(screen.queryAllByText("success")).toHaveLength(0);
  });
});
