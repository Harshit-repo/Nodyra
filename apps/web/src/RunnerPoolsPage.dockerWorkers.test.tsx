import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { DockerWorkerCard } from "./RunnerPoolsPage";

describe("DockerWorkerCard", () => {
  it("shows the sandbox checkbox with a trust warning", () => {
    render(
      <DockerWorkerCard
        poolId="p1"
        config={{}}
        canWrite
        onSave={vi.fn()}
        onAddRunner={vi.fn()}
      />
    );
    expect(screen.getByLabelText(/sandboxed execution support/i)).toBeInTheDocument();
    expect(screen.getByText(/root-equivalent/i)).toBeInTheDocument();
  });

  it("calls onAddRunner when the button is clicked", async () => {
    const onAdd = vi.fn().mockResolvedValue(undefined);
    render(
      <DockerWorkerCard
        poolId="p1"
        config={{}}
        canWrite
        onSave={vi.fn()}
        onAddRunner={onAdd}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /add docker runner/i }));
    await waitFor(() => expect(onAdd).toHaveBeenCalled());
  });

  it("hides mutating controls when canWrite is false", () => {
    render(
      <DockerWorkerCard
        poolId="p1"
        config={{}}
        canWrite={false}
        onSave={vi.fn()}
        onAddRunner={vi.fn()}
      />
    );
    expect(
      screen.queryByRole("button", { name: /add docker runner/i })
    ).toBeNull();
  });
});
