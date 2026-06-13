import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OverflowMenu } from "./OverflowMenu";

describe("OverflowMenu", () => {
  const items = [
    { id: "history", label: "History & versions", onSelect: vi.fn() },
    { id: "functions", label: "Functions", onSelect: vi.fn() },
    { id: "unpublish", label: "Unpublish workflow", danger: true, onSelect: vi.fn() },
  ];

  it("hides items until opened", () => {
    render(<OverflowMenu items={items} />);
    expect(screen.queryByText("History & versions")).toBeNull();
  });

  it("opens on trigger click and shows items", async () => {
    render(<OverflowMenu items={items} />);
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    expect(screen.getByText("History & versions")).toBeInTheDocument();
  });

  it("calls onSelect and closes when an item is clicked", async () => {
    const onSelect = vi.fn();
    render(<OverflowMenu items={[{ id: "x", label: "Do thing", onSelect }]} />);
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    await userEvent.click(screen.getByText("Do thing"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Do thing")).toBeNull();
  });

  it("skips falsy items (conditional entries)", async () => {
    render(<OverflowMenu items={[{ id: "a", label: "Shown", onSelect: vi.fn() }, null]} />);
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    expect(screen.getByText("Shown")).toBeInTheDocument();
  });
});
